"""
Tests for GET /v1/compliance/report.

Covers: empty namespace, window filtering, supersession counts,
conflict counts, erasure events, retention policy snapshot,
and verify=true chain status.
"""
import hashlib
from datetime import datetime, timezone, timedelta

import pytest
import pytest_asyncio
from httpx import AsyncClient, ASGITransport

from src.agentmem.main import app
from src.agentmem.db import get_db
from src.agentmem.models import ApiKey, NamespacePolicy

TEST_NS = "compliance-test-ns"
TEST_KEY = "compliance-test-key-xyz"
T0 = datetime(2026, 1, 1, tzinfo=timezone.utc)
T1 = datetime(2026, 6, 1, tzinfo=timezone.utc)
T2 = datetime(2026, 7, 1, tzinfo=timezone.utc)


def _h():
    return {"X-API-Key": TEST_KEY}


@pytest_asyncio.fixture
async def client(db):
    hashed = hashlib.sha256(TEST_KEY.encode()).hexdigest()
    db.add(ApiKey(hashed_key=hashed, namespace=TEST_NS, scopes=["read", "write", "admin"]))
    await db.commit()

    async def _override():
        yield db

    app.dependency_overrides[get_db] = _override
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
            yield ac
    finally:
        app.dependency_overrides.clear()


# ── Helpers ───────────────────────────────────────────────────────────────────

async def _add(client, content, event_time=T0, metadata=None, agent="agent-1"):
    r = await client.post("/v1/memories", json={
        "agent_id": agent,
        "content": content,
        "event_time": event_time.isoformat(),
        "metadata": metadata or {},
    }, headers=_h())
    assert r.status_code == 200, r.text
    return r.json()


# ── Tests ─────────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_empty_namespace_report(client):
    resp = await client.get("/v1/compliance/report", headers=_h())
    assert resp.status_code == 200
    body = resp.json()
    assert body["namespace"] == TEST_NS
    assert body["summary"]["total_memories"] == 0
    assert body["summary"]["active_memories"] == 0
    assert body["conflicts"]["open"] == 0
    assert body["erasures"]["total_requests"] == 0
    assert body["audit_chain"]["status"] == "unchecked"


@pytest.mark.asyncio
async def test_report_counts_active_memories(client):
    await _add(client, "AAPL EPS $1.40")
    await _add(client, "MSFT revenue $56B")
    resp = await client.get("/v1/compliance/report", headers=_h())
    body = resp.json()
    assert body["summary"]["total_memories"] == 2
    assert body["summary"]["active_memories"] == 2
    assert body["summary"]["superseded_memories"] == 0


@pytest.mark.asyncio
async def test_report_counts_supersessions(client):
    await _add(client, "NVDA EPS $5.40", T0, {"ticker": "NVDA", "metric": "eps"})
    await _add(client, "NVDA EPS $6.10", T1, {"ticker": "NVDA", "metric": "eps"})  # supersedes
    resp = await client.get("/v1/compliance/report", headers=_h())
    body = resp.json()
    assert body["summary"]["total_memories"] == 2
    assert body["summary"]["superseded_memories"] == 1
    assert body["summary"]["active_memories"] == 1
    assert body["supersessions"]["total_supersessions"] == 1


@pytest.mark.asyncio
async def test_report_window_filtering(client):
    # Both memories are ingested at wall-clock "now"; total is always all-time.
    # A past window (before either ingestion) should show new_in_window == 0.
    await _add(client, "fact A", T0)
    await _add(client, "fact B", T1)

    # Window in the distant past: nothing ingested before 2020
    past_window = {"from": "2020-01-01T00:00:00+00:00", "to": "2020-12-31T00:00:00+00:00"}
    resp = await client.get("/v1/compliance/report", params=past_window, headers=_h())
    body = resp.json()
    assert body["summary"]["total_memories"] == 2       # all-time total unchanged
    assert body["summary"]["new_in_window"] == 0        # nothing was ingested in 2020


@pytest.mark.asyncio
async def test_report_conflict_counts(client):
    # Same time conflict
    await _add(client, "AAPL EPS $1.40 (Bloomberg)", T0, {"ticker": "AAPL", "metric": "eps"})
    await _add(client, "AAPL EPS $1.38 (Refinitiv)", T0, {"ticker": "AAPL", "metric": "eps"})

    resp = await client.get("/v1/compliance/report", headers=_h())
    body = resp.json()
    assert body["conflicts"]["open"] == 1
    assert body["conflicts"]["detected_in_window"] == 1


@pytest.mark.asyncio
async def test_report_erasure_section(client, db):
    from src.agentmem.schemas import MemoryAdd
    from src.agentmem.memory_service import add_memory, erase_subject

    await add_memory(db, TEST_NS, MemoryAdd(
        agent_id="agent-1",
        content="PII memory",
        event_time=T0,
        subject_id="user-abc",
    ))
    await erase_subject(db, TEST_NS, "user-abc", "GDPR-001")

    resp = await client.get("/v1/compliance/report", headers=_h())
    body = resp.json()
    assert body["erasures"]["total_requests"] == 1
    assert "user-abc" in body["erasures"]["subject_ids"]
    assert body["summary"]["erased_memories"] == 1


@pytest.mark.asyncio
async def test_report_retention_policy_included(client, db):
    db.add(NamespacePolicy(
        namespace=TEST_NS,
        content_ttl_days=365,
        audit_retention_days=1825,
        legal_hold=False,
    ))
    await db.commit()

    resp = await client.get("/v1/compliance/report", headers=_h())
    body = resp.json()
    assert body["retention"] is not None
    assert body["retention"]["content_ttl_days"] == 365
    assert body["retention"]["audit_retention_days"] == 1825
    assert body["retention"]["legal_hold"] is False


@pytest.mark.asyncio
async def test_report_retention_null_when_no_policy(client):
    resp = await client.get("/v1/compliance/report", headers=_h())
    body = resp.json()
    assert body["retention"] is None


@pytest.mark.asyncio
async def test_report_verify_chain(client):
    await _add(client, "audited fact", T0)
    resp = await client.get("/v1/compliance/report", params={"verify": "true"}, headers=_h())
    body = resp.json()
    assert body["audit_chain"]["status"] in ("ok", "tampered")
    assert body["audit_chain"]["rows_checked"] >= 1


@pytest.mark.asyncio
async def test_report_supersession_high_low_confidence(client):
    # Force a deterministic keyed supersession (confidence=1.0)
    await _add(client, "JPM EPS $4.20", T0, {"ticker": "JPM", "metric": "eps"})
    await _add(client, "JPM EPS $4.50", T1, {"ticker": "JPM", "metric": "eps"})

    resp = await client.get("/v1/compliance/report", headers=_h())
    body = resp.json()
    assert body["supersessions"]["total_supersessions"] == 1
    assert body["supersessions"]["high_confidence"] == 1
    assert body["supersessions"]["low_confidence"] == 0


@pytest.mark.asyncio
async def test_report_generated_at_is_recent(client):
    resp = await client.get("/v1/compliance/report", headers=_h())
    body = resp.json()
    generated = datetime.fromisoformat(body["generated_at"])
    now = datetime.now(timezone.utc)
    assert abs((now - generated).total_seconds()) < 10
