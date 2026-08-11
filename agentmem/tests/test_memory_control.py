"""Verifiable-memory product contract: receipts plus inspect/correct/forget."""
from __future__ import annotations

import hashlib
from datetime import UTC, datetime
from uuid import uuid4

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from src.lians.db import get_db
from src.lians.main import app
from src.lians.models import ApiKey

KEY = "memory-control-key"
OTHER_KEY = "other-memory-control-key"
NS = "memory-control-ns"
AGENT = "personal-assistant"
T0 = datetime(2026, 1, 1, tzinfo=UTC)


def _headers(key: str = KEY) -> dict[str, str]:
    return {"X-API-Key": key}


@pytest_asyncio.fixture
async def client(db):
    db.add_all([
        ApiKey(
            hashed_key=hashlib.sha256(KEY.encode()).hexdigest(),
            namespace=NS,
            scopes=["read", "write"],
        ),
        ApiKey(
            hashed_key=hashlib.sha256(OTHER_KEY.encode()).hexdigest(),
            namespace="other-memory-control-ns",
            scopes=["read", "write"],
        ),
    ])
    await db.commit()

    async def _override():
        yield db

    app.dependency_overrides[get_db] = _override
    try:
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as api:
            yield api
    finally:
        app.dependency_overrides.clear()


async def _add(client: AsyncClient, content: str = "I prefer aisle seats") -> dict:
    response = await client.post(
        "/v1/memories",
        headers=_headers(),
        json={
            "agent_id": AGENT,
            "content": content,
            "event_time": T0.isoformat(),
            "source": "user",
            "subject_id": f"person-{uuid4()}",
            "metadata": {"entity": "traveler", "field": "seat_preference"},
        },
    )
    assert response.status_code == 200, response.text
    return response.json()


@pytest.mark.asyncio
async def test_recall_and_context_include_human_readable_receipts(client):
    memory = await _add(client)

    recall = await client.post(
        "/v1/recall",
        headers=_headers(),
        json={"agent_id": AGENT, "query": "What seat do I prefer?", "k": 5},
    )
    assert recall.status_code == 200, recall.text
    recall_body = recall.json()
    view = recall_body["receipt_view"]
    assert view["receipt_sha256"] == recall_body["receipt_sha256"]
    assert view["receipt_kind"] == "recall"
    assert view["integrity_status"] == "complete"
    assert view["exclusion_scope"] == "not_evaluated"
    assert view["memories_used"][0]["id"] == memory["id"]
    assert view["memories_used"][0]["content"] == "I prefer aisle seats"
    assert "memory used" in view["headline"]

    context = await client.post(
        "/v1/context",
        headers=_headers(),
        json={
            "agent_id": AGENT,
            "query": "What seat do I prefer?",
            "k": 5,
            "max_tokens": 128,
        },
    )
    assert context.status_code == 200, context.text
    context_body = context.json()
    context_view = context_body["receipt_view"]
    assert context_view["receipt_kind"] == "context"
    assert context_view["receipt_sha256"] == context_body["receipt_sha256"]
    assert context_view["exclusion_scope"] == "context_budget_and_policy"
    assert context_view["token_estimate"] == context_body["token_estimate"]


@pytest.mark.asyncio
async def test_list_and_correct_memory_preserve_version_history(client):
    original = await _add(client)

    listed = await client.get(
        "/v1/memories",
        headers=_headers(),
        params={"agent_id": AGENT, "state": "current"},
    )
    assert listed.status_code == 200, listed.text
    assert listed.json()["total"] == 1
    assert listed.json()["items"][0]["id"] == original["id"]

    corrected = await client.post(
        f"/v1/memories/{original['id']}/correct",
        headers=_headers(),
        json={"content": "I now prefer window seats"},
    )
    assert corrected.status_code == 200, corrected.text
    replacement = corrected.json()
    assert replacement["content"] == "I now prefer window seats"
    assert replacement["metadata"]["_correction_of"] == original["id"]

    current = await client.get(
        "/v1/memories",
        headers=_headers(),
        params={"agent_id": AGENT, "state": "current"},
    )
    history = await client.get(
        "/v1/memories",
        headers=_headers(),
        params={"agent_id": AGENT, "state": "superseded"},
    )
    assert [item["id"] for item in current.json()["items"]] == [replacement["id"]]
    assert [item["id"] for item in history.json()["items"]] == [original["id"]]
    assert history.json()["items"][0]["superseded_by"] == replacement["id"]

    recall = await client.post(
        "/v1/recall",
        headers=_headers(),
        json={"agent_id": AGENT, "query": "seat preference", "k": 5},
    )
    contents = [item["content"] for item in recall.json()["memories"]]
    assert "I now prefer window seats" in contents
    assert "I prefer aisle seats" not in contents


@pytest.mark.asyncio
async def test_forget_requires_confirmation_and_returns_audit_proof(client):
    memory = await _add(client)
    path = f"/v1/memories/{memory['id']}/forget"

    refused = await client.post(path, headers=_headers(), json={"confirm": False})
    assert refused.status_code == 422

    forgotten = await client.post(
        path,
        headers=_headers(),
        json={"confirm": True, "request_ref": "user-request-42"},
    )
    assert forgotten.status_code == 200, forgotten.text
    body = forgotten.json()
    assert body["status"] == "forgotten"
    assert len(body["audit_event_hash"]) == 64

    again = await client.post(
        path,
        headers=_headers(),
        json={"confirm": True, "request_ref": "user-request-42"},
    )
    assert again.status_code == 200
    assert again.json()["status"] == "already_forgotten"

    erased = await client.get(
        "/v1/memories",
        headers=_headers(),
        params={"agent_id": AGENT, "state": "erased"},
    )
    assert erased.json()["items"][0]["id"] == memory["id"]
    assert erased.json()["items"][0]["content"] is None
    assert erased.json()["items"][0]["metadata"] == {}

    recall = await client.post(
        "/v1/recall",
        headers=_headers(),
        json={"agent_id": AGENT, "query": "aisle seat", "k": 5},
    )
    assert memory["id"] not in [item["id"] for item in recall.json()["memories"]]


@pytest.mark.asyncio
async def test_memory_controls_fail_closed_across_namespaces(client):
    memory = await _add(client)

    correction = await client.post(
        f"/v1/memories/{memory['id']}/correct",
        headers=_headers(OTHER_KEY),
        json={"content": "cross-tenant overwrite"},
    )
    forgetting = await client.post(
        f"/v1/memories/{memory['id']}/forget",
        headers=_headers(OTHER_KEY),
        json={"confirm": True},
    )
    assert correction.status_code == 404
    assert forgetting.status_code == 404
