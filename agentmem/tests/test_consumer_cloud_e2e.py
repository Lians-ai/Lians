"""Full local-client to HTTP API proof of the consumer cross-tool moment."""

from __future__ import annotations

import asyncio
import hashlib
import json
import socket
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest
from sqlalchemy import select
from src.lians.db import get_db
from src.lians.main import app
from src.lians.models import ApiKey, SyncEnrollment, SyncRevision
from uvicorn import Config, Server

PACKAGE_ROOT = Path(__file__).resolve().parents[2] / "packages" / "lians-easy"
sys.path.insert(0, str(PACKAGE_ROOT))

from lians_easy.cloud_service import CloudSyncService
from lians_easy.mcp import call_tool
from lians_easy.store import MemoryStore
from lians_easy.sync_http import OpaqueSyncHTTPClient

SYNC_KEY = "consumer-e2e-sync-key"


class ConnectedAuth:
    def __init__(self, cloud_url: str):
        self.config = SimpleNamespace(cloud_url=cloud_url)

    def status(self):
        return {"state": "connected", "configured": True, "message": "Connected."}

    def access_token(self):
        raise AssertionError("The API-key test transport must not request an OAuth token")


def _service(store, cloud_url, state_path, device_name):
    def client_factory(base_url, *, bearer_token_provider):
        assert bearer_token_provider is not None
        return OpaqueSyncHTTPClient(base_url, SYNC_KEY)

    return CloudSyncService(
        store,
        ConnectedAuth(cloud_url),
        state_path=state_path,
        device_name=device_name,
        client_factory=client_factory,
    )


@pytest.mark.asyncio
async def test_two_clean_stores_enroll_and_share_corrected_memory_through_real_api(
    db, tmp_path
):
    db.add(
        ApiKey(
            hashed_key=hashlib.sha256(SYNC_KEY.encode()).hexdigest(),
            namespace="consumer-e2e",
            scopes=["sync"],
        )
    )
    await db.commit()

    async def override_db():
        yield db

    app.dependency_overrides[get_db] = override_db
    listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    listener.bind(("127.0.0.1", 0))
    listener.listen(128)
    port = listener.getsockname()[1]
    server = Server(Config(app, log_level="critical", lifespan="off"))
    server_task = asyncio.create_task(server.serve(sockets=[listener]))
    try:
        for _ in range(500):
            if server.started:
                break
            await asyncio.sleep(0.01)
        assert server.started
        cloud_url = f"http://127.0.0.1:{port}"

        first_store = MemoryStore(tmp_path / "main" / "memory.sqlite3")
        first = _service(
            first_store,
            cloud_url,
            tmp_path / "main" / "sync-state.json",
            "Main PC",
        )
        project = tmp_path / "project"
        (project / ".git").mkdir(parents=True)
        remembered = await asyncio.to_thread(
            call_tool,
            first_store,
            "remember",
            {
                "content": "We use FastAPI and never write migrations manually.",
                "kind": "preference",
                "scope": "global",
                "source_client": "cursor",
                "project_root": str(project),
            },
            cloud_sync=first,
        )
        assert remembered["structuredContent"]["cloud_sync"]["state"] == "synced"

        second_store = MemoryStore(tmp_path / "laptop" / "memory.sqlite3")
        second = _service(
            second_store,
            cloud_url,
            tmp_path / "laptop" / "sync-state.json",
            "Laptop",
        )
        request = await asyncio.to_thread(second.start_device_enrollment)
        pending = await asyncio.to_thread(first.pending_device_requests)
        assert pending["requests"] == [
            {
                "state": "waiting_for_approval",
                "request_id": request["request_id"],
                "verification_code": request["verification_code"],
                "device": request["device"],
                "expires_at": request["expires_at"],
            }
        ]
        await asyncio.to_thread(
            first.approve_device_request,
            request["request_id"],
            request["verification_code"],
            confirmed=True,
        )
        connected = await asyncio.to_thread(second.device_enrollment_status)
        assert connected["state"] == "connected"
        assert connected["device_count"] == 2

        codex_recall = await asyncio.to_thread(
            call_tool,
            second_store,
            "recall",
            {"query": "How should I build the API?", "client": "codex"},
            cloud_sync=second,
        )
        recalled = codex_recall["structuredContent"]
        assert recalled["memories"][0]["content"] == (
            "We use FastAPI and never write migrations manually."
        )
        assert recalled["receipt"]["client"] == "codex"
        assert recalled["receipt"]["memories"][0]["source_client"] == "cursor"

        await asyncio.to_thread(
            call_tool,
            second_store,
            "correct_memory",
            {
                "memory_id": recalled["memories"][0]["id"],
                "content": "We use FastAPI and only use reviewed Alembic migrations.",
            },
            cloud_sync=second,
        )
        claude_recall = await asyncio.to_thread(
            call_tool,
            first_store,
            "recall",
            {"query": "FastAPI migration policy", "client": "claude"},
            cloud_sync=first,
        )
        corrected = claude_recall["structuredContent"]
        assert [item["content"] for item in corrected["memories"]] == [
            "We use FastAPI and only use reviewed Alembic migrations."
        ]
        assert corrected["receipt"]["client"] == "claude"

        await asyncio.to_thread(
            call_tool,
            first_store,
            "forget_memory",
            {"memory_id": corrected["memories"][0]["id"], "confirmed": True},
            cloud_sync=first,
        )
        after_forgetting = await asyncio.to_thread(
            call_tool,
            second_store,
            "recall",
            {"query": "FastAPI migration policy", "client": "codex"},
            cloud_sync=second,
        )
        assert after_forgetting["structuredContent"]["memories"] == []
        assert after_forgetting["structuredContent"]["receipt"]["memory_count"] == 0

        rows = (await db.execute(select(SyncRevision))).scalars().all()
        opaque = json.dumps([row.envelope for row in rows])
        assert "FastAPI" not in opaque
        assert "migrations" not in opaque
        assert (await db.execute(select(SyncEnrollment))).scalars().all() == []
    finally:
        server.should_exit = True
        await server_task
        listener.close()
        app.dependency_overrides.clear()
