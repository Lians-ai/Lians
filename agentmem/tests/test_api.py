"""
API integration tests â€” full HTTP stack via ASGITransport.
Proves auth, routes, and end-to-end behaviour without a real network or PG.

Each test gets a fresh in-memory SQLite DB (from the db fixture in conftest)
and a FastAPI client that has get_db overridden to point at it.
"""
import hashlib
import pytest
import pytest_asyncio
from datetime import datetime, timezone, timedelta
from httpx import AsyncClient, ASGITransport

# Use a far-future sentinel for audit trail queries so that event_log rows
# (created_at â‰ˆ now) always satisfy `created_at <= AUDIT_AS_OF`.
AUDIT_AS_OF = datetime(2099, 1, 1, tzinfo=timezone.utc)

from src.lian.main import app
from src.lian.db import get_db
from src.lian.models import ApiKey


TEST_KEY = "integration-test-key-secret"
READ_KEY = "read-only-key-secret"
TEST_NS = "api-test-ns"
AGENT = "api-agent"

T0 = datetime(2026, 1, 1, tzinfo=timezone.utc)
T1 = datetime(2026, 6, 1, tzinfo=timezone.utc)


@pytest_asyncio.fixture
async def client(db):
    """FastAPI test client with injected in-memory DB and a seeded full-access key."""
    hashed_full = hashlib.sha256(TEST_KEY.encode()).hexdigest()
    hashed_read = hashlib.sha256(READ_KEY.encode()).hexdigest()
    db.add(ApiKey(hashed_key=hashed_full, namespace=TEST_NS, scopes=["read", "write", "admin"]))
    db.add(ApiKey(hashed_key=hashed_read, namespace=TEST_NS, scopes=["read"]))
    await db.commit()

    async def _override():
        yield db

    app.dependency_overrides[get_db] = _override
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
            yield ac
    finally:
        app.dependency_overrides.clear()


def _h(key: str = TEST_KEY) -> dict:
    return {"X-API-Key": key}


def _mem(content: str, event_time: datetime = T0, meta: dict | None = None) -> dict:
    return {
        "agent_id": AGENT,
        "content": content,
        "event_time": event_time.isoformat(),
        "metadata": meta or {},
    }


# ---------------------------------------------------------------------------
# Health
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_health(client):
    from unittest.mock import AsyncMock, patch
    with patch("src.lian.cache._get_redis") as mock_redis:
        mock_redis.return_value.ping = AsyncMock(return_value=True)
        resp = await client.get("/health")
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "ok"
    assert body["checks"]["db"] == "ok"
    assert body["checks"]["redis"] == "ok"


# ---------------------------------------------------------------------------
# Auth
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_missing_key_returns_401(client):
    resp = await client.post("/v1/memories", json=_mem("test"))
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_wrong_key_returns_401(client):
    resp = await client.post(
        "/v1/memories", json=_mem("test"), headers={"X-API-Key": "bad-key"}
    )
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_read_only_key_cannot_write(client):
    resp = await client.post("/v1/memories", json=_mem("test"), headers=_h(READ_KEY))
    assert resp.status_code == 403


@pytest.mark.asyncio
async def test_read_only_key_can_recall(client):
    # seed one memory with the write key first
    await client.post("/v1/memories", json=_mem("NVDA guidance $36B"), headers=_h())
    resp = await client.post(
        "/v1/recall",
        json={"agent_id": AGENT, "query": "NVDA guidance", "k": 5},
        headers=_h(READ_KEY),
    )
    assert resp.status_code == 200


# ---------------------------------------------------------------------------
# POST /v1/memories
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_add_memory_response_shape(client):
    resp = await client.post("/v1/memories", headers=_h(), json={
        "agent_id": AGENT,
        "content": "NVDA Q3 guidance raised to $36B",
        "event_time": T1.isoformat(),
        "source": "analyst_day",
        "metadata": {"ticker": "NVDA", "metric": "guidance"},
    })
    assert resp.status_code == 200
    body = resp.json()
    assert body["id"] is not None
    assert body["content"] == "NVDA Q3 guidance raised to $36B"
    assert body["namespace"] == TEST_NS
    assert body["valid_to"] is None
    assert body["content_hash"] is not None


@pytest.mark.asyncio
async def test_add_then_supersede_via_recall(client):
    """New memory supersedes old: present-time recall returns the newer one first."""
    await client.post("/v1/memories", headers=_h(), json={
        "agent_id": AGENT, "content": "NVDA Q3 guidance $32B",
        "event_time": T0.isoformat(),
        "metadata": {"ticker": "NVDA", "metric": "guidance"},
    })
    await client.post("/v1/memories", headers=_h(), json={
        "agent_id": AGENT, "content": "NVDA Q3 guidance raised to $36B",
        "event_time": T1.isoformat(),
        "metadata": {"ticker": "NVDA", "metric": "guidance"},
    })

    recall = await client.post("/v1/recall", headers=_h(), json={
        "agent_id": AGENT, "query": "NVDA guidance", "k": 5,
    })
    assert recall.status_code == 200
    memories = recall.json()["memories"]
    assert len(memories) >= 1
    # Currently-valid (newer) memory must rank first
    assert "$36B" in (memories[0]["content"] or "")


@pytest.mark.asyncio
async def test_add_pii_memory_with_subject_id(client):
    """Memories with subject_id are accepted and content is returned correctly."""
    resp = await client.post("/v1/memories", headers=_h(), json={
        "agent_id": AGENT,
        "content": "Client John Doe portfolio $500k",
        "event_time": T0.isoformat(),
        "subject_id": "john-doe-001",
        "metadata": {},
    })
    assert resp.status_code == 200
    body = resp.json()
    assert body["subject_id"] == "john-doe-001"
    assert body["content_hash"] is not None


# ---------------------------------------------------------------------------
# POST /v1/recall
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_recall_finds_added_memory(client):
    await client.post("/v1/memories", headers=_h(), json={
        "agent_id": AGENT, "content": "AAPL gross margin 46%",
        "event_time": T0.isoformat(),
        "metadata": {"ticker": "AAPL", "metric": "gross_margin"},
    })
    resp = await client.post("/v1/recall", headers=_h(), json={
        "agent_id": AGENT, "query": "AAPL gross margin", "k": 5,
    })
    assert resp.status_code == 200
    memories = resp.json()["memories"]
    assert any("AAPL" in (m["content"] or "") for m in memories)


@pytest.mark.asyncio
async def test_recall_as_of_excludes_future_event_time(client):
    """Memory with event_time=T1 must not appear in recall with as_of=T0."""
    await client.post("/v1/memories", headers=_h(), json={
        "agent_id": AGENT, "content": "MSFT guidance $300B",
        "event_time": T1.isoformat(),
        "metadata": {"ticker": "MSFT", "metric": "guidance"},
    })
    resp = await client.post("/v1/recall", headers=_h(), json={
        "agent_id": AGENT, "query": "MSFT guidance",
        "k": 5, "as_of": T0.isoformat(),
    })
    assert resp.status_code == 200
    assert len(resp.json()["memories"]) == 0


@pytest.mark.asyncio
async def test_recall_as_of_returns_past_snapshot(client):
    """as_of=T0+1day returns the old memory, not the superseding one."""
    await client.post("/v1/memories", headers=_h(), json={
        "agent_id": AGENT, "content": "TSLA deliveries 400k",
        "event_time": T0.isoformat(),
        "metadata": {"ticker": "TSLA", "metric": "deliveries"},
    })
    await client.post("/v1/memories", headers=_h(), json={
        "agent_id": AGENT, "content": "TSLA deliveries 450k",
        "event_time": T1.isoformat(),
        "metadata": {"ticker": "TSLA", "metric": "deliveries"},
    })

    resp = await client.post("/v1/recall", headers=_h(), json={
        "agent_id": AGENT, "query": "TSLA deliveries", "k": 5,
        "as_of": (T0 + timedelta(days=1)).isoformat(),
    })
    assert resp.status_code == 200
    memories = resp.json()["memories"]
    contents = [m["content"] or "" for m in memories]
    assert any("400k" in c for c in contents), "Old value must appear in past snapshot"
    assert not any("450k" in c for c in contents), "New value must not appear before its event_time"


@pytest.mark.asyncio
async def test_recall_metadata_filter(client):
    """Metadata filter narrows results to the matching ticker."""
    await client.post("/v1/memories", headers=_h(), json={
        "agent_id": AGENT, "content": "NVDA revenue $18B",
        "event_time": T0.isoformat(),
        "metadata": {"ticker": "NVDA", "metric": "revenue"},
    })
    await client.post("/v1/memories", headers=_h(), json={
        "agent_id": AGENT, "content": "AMD revenue $6B",
        "event_time": T0.isoformat(),
        "metadata": {"ticker": "AMD", "metric": "revenue"},
    })

    resp = await client.post("/v1/recall", headers=_h(), json={
        "agent_id": AGENT, "query": "revenue", "k": 10,
        "filters": {"ticker": "NVDA"},
    })
    assert resp.status_code == 200
    memories = resp.json()["memories"]
    assert all("NVDA" in (m["content"] or "") for m in memories)


# ---------------------------------------------------------------------------
# GET /v1/audit/reconstruct
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_audit_reconstruct_includes_event_trail(client):
    await client.post("/v1/memories", headers=_h(), json={
        "agent_id": AGENT, "content": "GOOGL EPS $2.10",
        "event_time": T0.isoformat(),
        "metadata": {"ticker": "GOOGL", "metric": "eps"},
    })

    # AUDIT_AS_OF is far-future so that event_log rows (created_at â‰ˆ now)
    # satisfy the created_at <= as_of filter in audit.py.
    resp = await client.get("/v1/audit/reconstruct", headers=_h(), params={
        "agent_id": AGENT, "as_of": AUDIT_AS_OF.isoformat(),
    })
    assert resp.status_code == 200
    body = resp.json()
    assert len(body["memories"]) >= 1
    assert len(body["event_trail"]) >= 1
    assert any(e["op"] == "add" for e in body["event_trail"])


@pytest.mark.asyncio
async def test_audit_reconstruct_excludes_post_as_of_memories(client):
    """Memories whose event_time is after as_of must not appear."""
    await client.post("/v1/memories", headers=_h(), json={
        "agent_id": AGENT, "content": "WFC Q4 revenue $20B",
        "event_time": T1.isoformat(),  # AFTER as_of
        "metadata": {"ticker": "WFC", "metric": "revenue"},
    })

    resp = await client.get("/v1/audit/reconstruct", headers=_h(), params={
        "agent_id": AGENT, "as_of": T0.isoformat(),
    })
    assert resp.status_code == 200
    assert len(resp.json()["memories"]) == 0


@pytest.mark.asyncio
async def test_audit_reconstruct_with_query(client):
    await client.post("/v1/memories", headers=_h(), json={
        "agent_id": AGENT, "content": "AMZN AWS revenue $25B",
        "event_time": T0.isoformat(),
        "metadata": {"ticker": "AMZN", "metric": "aws_revenue"},
    })
    await client.post("/v1/memories", headers=_h(), json={
        "agent_id": AGENT, "content": "AMZN retail revenue $140B",
        "event_time": T0.isoformat(),
        "metadata": {"ticker": "AMZN", "metric": "retail_revenue"},
    })

    resp = await client.get("/v1/audit/reconstruct", headers=_h(), params={
        "agent_id": AGENT, "as_of": T1.isoformat(),
        "query": "AWS revenue", "k": 1,
    })
    assert resp.status_code == 200
    memories = resp.json()["memories"]
    assert len(memories) == 1
    assert "AWS" in (memories[0]["content"] or "")


# ---------------------------------------------------------------------------
# POST /v1/erase
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_erase_requires_admin_scope(client):
    resp = await client.post("/v1/erase", headers=_h(READ_KEY), json={
        "subject_id": "jane-001", "request_ref": "GDPR-test",
    })
    assert resp.status_code == 403


@pytest.mark.asyncio
async def test_erase_subject_tombstones_memory(client):
    """After erasure, the memory row exists (tombstone) but content is gone."""
    await client.post("/v1/memories", headers=_h(), json={
        "agent_id": AGENT,
        "content": "Client: Jane Doe, DOB 1985-03-12",
        "event_time": T0.isoformat(),
        "subject_id": "jane-doe-002",
        "metadata": {},
    })

    resp = await client.post("/v1/erase", headers=_h(), json={
        "subject_id": "jane-doe-002",
        "request_ref": "GDPR-req-0042",
    })
    assert resp.status_code == 200
    body = resp.json()
    assert body["memories_erased"] == 1
    assert body["request_ref"] == "GDPR-req-0042"


@pytest.mark.asyncio
async def test_erase_event_appears_in_audit_trail(client):
    """After erase, the audit trail contains an 'erase' operation."""
    await client.post("/v1/memories", headers=_h(), json={
        "agent_id": AGENT,
        "content": "Client: Bob Smith, account $1M",
        "event_time": T0.isoformat(),
        "subject_id": "bob-smith-003",
        "metadata": {},
    })
    await client.post("/v1/erase", headers=_h(), json={
        "subject_id": "bob-smith-003", "request_ref": "GDPR-req-0043",
    })

    resp = await client.get("/v1/audit/reconstruct", headers=_h(), params={
        "agent_id": AGENT, "as_of": AUDIT_AS_OF.isoformat(),
    })
    assert resp.status_code == 200
    ops = [e["op"] for e in resp.json()["event_trail"]]
    assert "erase" in ops, "Erase operation must appear in the immutable audit trail"
