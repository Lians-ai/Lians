from __future__ import annotations

import json
from datetime import UTC, datetime
from types import SimpleNamespace

from lians_easy.cloud_service import CloudSyncService
from lians_easy.mcp import call_tool
from lians_easy.store import MemoryStore
from lians_easy.sync import (
    DeviceIdentity,
    OpaqueRevisionLog,
    SyncState,
    accept_enrollment,
    approve_enrollment,
    create_enrollment_request,
)
from lians_easy.sync_http import SyncCloudError

NOW = datetime(2026, 8, 15, 12, 0, tzinfo=UTC)


class FakeAuth:
    config = SimpleNamespace(cloud_url="https://api.lians.ai")

    def status(self):
        return {"state": "connected", "configured": True, "message": "Connected."}

    def access_token(self):
        return "short-lived-access-token"


class HTTPShapeCloud:
    def __init__(self):
        self.log = OpaqueRevisionLog()
        self.deleted = False

    def client_factory(self, base_url, *, bearer_token_provider):
        assert base_url == "https://api.lians.ai"
        assert bearer_token_provider() == "short-lived-access-token"
        outer = self

        class Client:
            def create_workspace(self, state):
                outer.log.create_workspace(state)
                return {"status": "created"}

            def grants(self, workspace_id):
                return outer.log.grants(workspace_id)

            def revisions_after(self, workspace_id, revision):
                rows = outer.log.revisions_after(workspace_id, revision)
                workspace = outer.log._workspace(workspace_id)
                head = workspace["revisions"][-1] if workspace["revisions"] else None
                return {
                    "revisions": rows,
                    "head": {
                        "revision": len(workspace["revisions"]),
                        "object_hash": head["object_hash"] if head else None,
                    },
                    "has_more": False,
                }

            def push(self, workspace_id, envelope):
                assert workspace_id == envelope["workspace_id"]
                return outer.log.push(envelope)

            def delete_workspace(self, workspace_id, *, confirmed=False):
                assert confirmed is True
                workspace = outer.log._workspace(workspace_id)
                revision_count = len(workspace["revisions"])
                device_count = len(workspace["devices"])
                del outer.log._workspaces[workspace_id]
                outer.deleted = True
                return {
                    "encrypted_revisions_deleted": revision_count,
                    "devices_deleted": device_count,
                }

        return Client()


def _service(store, auth, cloud, state_path, name):
    return CloudSyncService(
        store,
        auth,
        state_path=state_path,
        device_name=name,
        client_factory=cloud.client_factory,
    )


def test_service_provisions_pulls_merges_pushes_and_deletes_opaque_memory(tmp_path):
    auth = FakeAuth()
    cloud = HTTPShapeCloud()
    first_store = MemoryStore(tmp_path / "first" / "memory.sqlite3")
    first_state_path = tmp_path / "first" / "sync-state.json"
    first = _service(first_store, auth, cloud, first_state_path, "Main PC")
    first_store.remember(
        "Never use em dashes.",
        kind="preference",
        scope="global",
        source_client="cursor",
    )

    initial = first.sync_now()
    assert initial["workspace_created"] is True
    assert initial["head_revision"] == 1
    first_identity = DeviceIdentity.from_store(first_store, "Main PC")
    first_state = SyncState.load(first_state_path, first_store.cipher, first_identity)
    cloud_document = json.dumps(cloud.log._workspace(first_state.workspace_id))
    assert "Never use em dashes" not in cloud_document

    second_store = MemoryStore(tmp_path / "second" / "memory.sqlite3")
    second_state_path = tmp_path / "second" / "sync-state.json"
    second_identity = DeviceIdentity.from_store(second_store, "Laptop")
    request = create_enrollment_request(second_identity, now=NOW)
    approval = approve_enrollment(first_state, first_identity, request, now=NOW)
    cloud.log.register_approval(approval)
    accept_enrollment(
        second_store,
        second_identity,
        request,
        approval,
        second_state_path,
        now=NOW,
    )
    second = _service(second_store, auth, cloud, second_state_path, "Laptop")
    received = second.sync_now()
    assert received["revisions_pulled"] == 1
    assert second_store.recall("writing preference")[0]["content"] == "Never use em dashes."

    second_store.remember(
        "The project uses FastAPI.",
        kind="project",
        scope="global",
        source_client="codex",
    )
    second.sync_now()
    updated = first.sync_now()
    assert updated["revisions_pulled"] >= 1
    assert any(
        item["content"] == "The project uses FastAPI."
        for item in first_store.recall("FastAPI")
    )

    deleted = first.delete_cloud_memory(confirmed=True)
    assert deleted["state"] == "deleted"
    assert deleted["local_memory_preserved"] is True
    assert cloud.deleted is True
    assert first_state_path.exists() is False
    assert first_store.stats()["current"] == 2


def test_cursor_memory_reaches_codex_and_claude_with_correction_and_forgetting(tmp_path):
    """Prove the launch-critical cross-tool sequence across two local devices."""

    auth = FakeAuth()
    cloud = HTTPShapeCloud()
    cursor_project = tmp_path / "desktop-project"
    (cursor_project / ".git").mkdir(parents=True)
    cursor_store = MemoryStore(tmp_path / "desktop" / "memory.sqlite3")
    cursor_state_path = tmp_path / "desktop" / "sync-state.json"
    cursor_sync = _service(cursor_store, auth, cloud, cursor_state_path, "Main PC")

    remembered = call_tool(
        cursor_store,
        "remember",
        {
            "content": "We use FastAPI and never write migrations manually.",
            "kind": "preference",
            "scope": "global",
            "source_client": "cursor",
            "project_root": str(cursor_project),
        },
        cloud_sync=cursor_sync,
    )
    assert remembered["structuredContent"]["cloud_sync"]["state"] == "synced"
    assert "Remembered everywhere" in remembered["content"][0]["text"]

    cursor_identity = DeviceIdentity.from_store(cursor_store, "Main PC")
    cursor_state = SyncState.load(cursor_state_path, cursor_store.cipher, cursor_identity)
    cloud_document = json.dumps(cloud.log._workspace(cursor_state.workspace_id))
    assert "FastAPI" not in cloud_document
    assert "migrations manually" not in cloud_document

    codex_store = MemoryStore(tmp_path / "laptop" / "memory.sqlite3")
    codex_state_path = tmp_path / "laptop" / "sync-state.json"
    codex_identity = DeviceIdentity.from_store(codex_store, "Laptop")
    request = create_enrollment_request(codex_identity, now=NOW)
    approval = approve_enrollment(cursor_state, cursor_identity, request, now=NOW)
    cloud.log.register_approval(approval)
    accept_enrollment(
        codex_store,
        codex_identity,
        request,
        approval,
        codex_state_path,
        now=NOW,
    )
    codex_sync = _service(codex_store, auth, cloud, codex_state_path, "Laptop")

    recalled = call_tool(
        codex_store,
        "recall",
        {"query": "How do we build this API?", "client": "codex"},
        cloud_sync=codex_sync,
    )["structuredContent"]
    assert recalled["cloud_sync"]["revisions_pulled"] == 1
    assert recalled["receipt"]["client"] == "codex"
    assert recalled["receipt"]["memories"][0]["source_client"] == "cursor"
    assert recalled["memories"][0]["content"] == (
        "We use FastAPI and never write migrations manually."
    )

    corrected = call_tool(
        codex_store,
        "correct_memory",
        {
            "memory_id": recalled["memories"][0]["id"],
            "content": "We use FastAPI and only use reviewed Alembic migrations.",
        },
        cloud_sync=codex_sync,
    )["structuredContent"]
    assert corrected["cloud_sync"]["memory_scope"] == "everywhere"

    claude_recall = call_tool(
        cursor_store,
        "recall",
        {"query": "FastAPI migration policy", "client": "claude"},
        cloud_sync=cursor_sync,
    )["structuredContent"]
    assert claude_recall["receipt"]["client"] == "claude"
    assert [item["content"] for item in claude_recall["memories"]] == [
        "We use FastAPI and only use reviewed Alembic migrations."
    ]

    forgotten = call_tool(
        cursor_store,
        "forget_memory",
        {"memory_id": claude_recall["memories"][0]["id"], "confirmed": True},
        cloud_sync=cursor_sync,
    )["structuredContent"]
    assert forgotten["status"] == "forgotten"
    assert forgotten["cloud_sync"]["memory_scope"] == "everywhere"

    after_forgetting = call_tool(
        codex_store,
        "recall",
        {"query": "FastAPI migration policy", "client": "codex"},
        cloud_sync=codex_sync,
    )["structuredContent"]
    assert after_forgetting["memories"] == []
    assert after_forgetting["receipt"]["memory_count"] == 0


def test_cloud_failure_never_blocks_or_leaks_from_a_local_remember(tmp_path):
    class FailingCloud:
        def client_factory(self, _base_url, *, bearer_token_provider):
            assert bearer_token_provider() == "short-lived-access-token"

            class Client:
                def create_workspace(self, _state):
                    raise SyncCloudError("access-token=C:/private/person/token")

            return Client()

    store = MemoryStore(tmp_path / "memory.sqlite3")
    cloud = FailingCloud()
    sync = _service(store, FakeAuth(), cloud, tmp_path / "sync-state.json", "Laptop")
    project = tmp_path / "project"
    (project / ".git").mkdir(parents=True)

    result = call_tool(
        store,
        "remember",
        {
            "content": "Never use em dashes.",
            "kind": "preference",
            "scope": "global",
            "source_client": "cursor",
            "project_root": str(project),
        },
        cloud_sync=sync,
    )

    assert result["isError"] is False
    assert result["structuredContent"]["cloud_sync"]["state"] == "pending"
    assert result["structuredContent"]["cloud_sync"]["pending"] is True
    assert store.recall("writing style")[0]["content"] == "Never use em dashes."
    assert "access-token" not in json.dumps(result)
    assert "private/person" not in json.dumps(result)


def test_broken_local_cloud_session_never_blocks_a_local_remember(tmp_path):
    class BrokenStatusAuth(FakeAuth):
        def status(self):
            raise OSError("C:/private/person/cloud-session.json")

    store = MemoryStore(tmp_path / "memory.sqlite3")
    sync = CloudSyncService(
        store,
        BrokenStatusAuth(),
        state_path=tmp_path / "sync-state.json",
    )

    result = call_tool(
        store,
        "remember",
        {
            "content": "Use concise sentences.",
            "kind": "preference",
            "scope": "global",
        },
        cloud_sync=sync,
    )

    cloud = result["structuredContent"]["cloud_sync"]
    assert cloud == {
        "state": "needs_attention",
        "attempted": False,
        "memory_scope": "local",
        "pending": True,
    }
    assert store.recall("writing")[0]["content"] == "Use concise sentences."
    assert "private/person" not in json.dumps(result)
