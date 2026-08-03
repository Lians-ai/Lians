"""
Idempotency-Key + readiness probe tests.

- A retried POST /v1/memories with the same Idempotency-Key returns the original
  memory (exactly-once write), while a different key creates a new one.
- /livez is a cheap liveness probe; /readyz is the deep readiness check.
"""
from __future__ import annotations

import hashlib
import uuid
from datetime import datetime, timezone

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from lians.db import get_db
from lians.idempotency import scoped_key_hash
from lians.main import app
from lians.models import ApiKey, OperationIdempotency
from lians.schemas import MemoryAdd

NS = "idem-ns"
KEY = "idem-key"
AGENT = "idem-agent"
T = datetime(2026, 1, 1, tzinfo=timezone.utc)


@pytest_asyncio.fixture
async def client(db):
    db.add(ApiKey(hashed_key=hashlib.sha256(KEY.encode()).hexdigest(),
                  namespace=NS, scopes=["read", "write", "admin"]))
    await db.commit()

    async def _override():
        yield db

    app.dependency_overrides[get_db] = _override
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
            yield ac
    finally:
        app.dependency_overrides.clear()


def _h(extra=None):
    h = {"X-API-Key": KEY}
    if extra:
        h.update(extra)
    return h


def _body(content):
    return {"agent_id": AGENT, "content": content, "event_time": T.isoformat(),
            "metadata": {"ticker": "NVDA", "metric": "eps"}}


@pytest.mark.asyncio
async def test_same_idempotency_key_returns_original(client):
    body = _body("NVDA EPS $6.20")
    r1 = await client.post("/v1/memories", headers=_h({"Idempotency-Key": "abc-123"}),
                           json=body)
    assert r1.status_code == 200, r1.text
    r2 = await client.post("/v1/memories", headers=_h({"Idempotency-Key": "abc-123"}),
                           json=body)
    assert r2.status_code == 200
    assert r1.json()["id"] == r2.json()["id"]
    assert r2.json()["content"] == "NVDA EPS $6.20"


@pytest.mark.asyncio
async def test_same_key_with_different_body_is_a_conflict(client):
    first = await client.post(
        "/v1/memories",
        headers=_h({"Idempotency-Key": "body-bound"}),
        json=_body("NVDA EPS $6.20"),
    )
    second = await client.post(
        "/v1/memories",
        headers=_h({"Idempotency-Key": "body-bound"}),
        json=_body("NVDA EPS $9.99"),
    )
    assert first.status_code == 200
    assert second.status_code == 409


@pytest.mark.asyncio
async def test_raw_key_is_never_persisted(client, db):
    raw_key = "customer-retry-token"
    response = await client.post(
        "/v1/memories",
        headers=_h({"Idempotency-Key": raw_key}),
        json=_body("Immutable retry claim"),
    )
    assert response.status_code == 200
    claim = await db.get(
        OperationIdempotency,
        (NS, "memory.create", scoped_key_hash(NS, "memory.create", raw_key)),
    )
    assert claim is not None
    assert claim.key_hash != raw_key
    assert raw_key not in str(claim.resource_ids)


@pytest.mark.asyncio
async def test_migrated_unverified_claim_blocks_without_replaying(client, db):
    raw_key = "pre-upgrade-key"
    db.add(
        OperationIdempotency(
            namespace=NS,
            operation="memory.create",
            key_hash=scoped_key_hash(NS, "memory.create", raw_key),
            request_digest="0" * 64,
            legacy_unverified_request=True,
            resource_kind="memory",
            resource_ids=[str(uuid.uuid4())],
            response_status=200,
            created_at=T,
        )
    )
    await db.commit()

    response = await client.post(
        "/v1/memories",
        headers=_h({"Idempotency-Key": raw_key}),
        json=_body("A body the legacy row could not authenticate"),
    )

    assert response.status_code == 409
    assert "cannot safely replay" in response.text


@pytest.mark.asyncio
async def test_batch_replay_is_ordered_and_body_bound(client):
    body = {
        "memories": [
            _body("Batch fact one"),
            {
                **_body("Batch fact two"),
                "event_time": datetime(
                    2026, 1, 2, tzinfo=timezone.utc
                ).isoformat(),
            },
        ]
    }
    headers = _h({"Idempotency-Key": "ordered-batch"})
    first = await client.post("/v1/memories/batch", headers=headers, json=body)
    replay = await client.post("/v1/memories/batch", headers=headers, json=body)
    changed = await client.post(
        "/v1/memories/batch",
        headers=headers,
        json={"memories": [_body("Different batch")]},
    )
    assert first.status_code == replay.status_code == 200
    assert [row["id"] for row in first.json()["memories"]] == [
        row["id"] for row in replay.json()["memories"]
    ]
    assert changed.status_code == 409


@pytest.mark.asyncio
async def test_different_idempotency_key_creates_new(client):
    r1 = await client.post("/v1/memories", headers=_h({"Idempotency-Key": "k1"}),
                           json=_body("AAPL EPS $1.50"))
    r2 = await client.post("/v1/memories", headers=_h({"Idempotency-Key": "k2"}),
                           json=_body("AAPL EPS $1.62"))
    assert r1.json()["id"] != r2.json()["id"]


@pytest.mark.asyncio
async def test_no_key_still_works(client):
    r = await client.post("/v1/memories", headers=_h(), json=_body("MSFT cloud $25B"))
    assert r.status_code == 200
    assert r.json()["id"]


@pytest.mark.asyncio
async def test_batch_preacquires_agent_locks_in_canonical_order(monkeypatch):
    """Opposite request order must not become opposite PostgreSQL lock order."""
    from lians import memory_service

    acquired: list[str] = []

    async def _record_lock(_db, _namespace, agent_id, *, shared=False):
        assert shared is False
        acquired.append(agent_id)
        return True

    async def _stop_before_mutation(*_args, **_kwargs):
        raise RuntimeError("stop after lock acquisition")

    monkeypatch.setattr(memory_service, "_acquire_pg_advisory_lock", _record_lock)
    monkeypatch.setattr(memory_service, "add_memory", _stop_before_mutation)
    requests = [
        MemoryAdd(agent_id="z-agent", content="z", event_time=T),
        MemoryAdd(agent_id="a-agent", content="a", event_time=T),
        MemoryAdd(agent_id="z-agent", content="z2", event_time=T),
    ]

    with pytest.raises(RuntimeError, match="stop after lock acquisition"):
        await memory_service.batch_add_memories(object(), NS, requests)

    assert acquired == ["a-agent", "z-agent"]


@pytest.mark.asyncio
async def test_livez_is_cheap_and_alive(client):
    r = await client.get("/livez")
    assert r.status_code == 200
    assert r.json()["status"] == "alive"


@pytest.mark.asyncio
async def test_readyz_deep_check(client):
    r = await client.get("/readyz")
    assert r.status_code in (200, 503)          # deep check; shape always present
    assert "checks" in r.json()
    assert r.json()["checks"]["db"] == "ok"
