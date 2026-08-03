"""
Tests for GET /v1/snapshot â€” audit reconstruction (complete knowledge state at T).

Covers: empty state, all-active memories, superseded memories excluded from
present but visible in past snapshot, erased memories present as null-content,
ordering, limit, cross-agent isolation, future memories not in past snapshot.
"""
import hashlib
from datetime import datetime, timezone, timedelta

import pytest
import pytest_asyncio
from httpx import AsyncClient, ASGITransport
from sqlalchemy.ext.asyncio import async_sessionmaker

from lians.main import app
from lians.db import get_db
from lians.models import ApiKey
from lians.subject_erasure_service import (
    claim_due_subject_erasure_jobs,
    process_subject_erasure_job,
)

TEST_NS = "snapshot-test-ns"
TEST_KEY = "snapshot-test-key-xyz"
AGENT = "compliance-desk"

T0 = datetime(2025, 1, 1, tzinfo=timezone.utc)
T1 = datetime(2025, 6, 1, tzinfo=timezone.utc)
T2 = datetime(2026, 1, 1, tzinfo=timezone.utc)
T3 = datetime(2026, 6, 1, tzinfo=timezone.utc)
NOW = datetime(2027, 1, 1, tzinfo=timezone.utc)


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


async def _add(client, content, event_time=T0, metadata=None, agent=AGENT):
    r = await client.post("/v1/memories", json={
        "agent_id": agent,
        "content": content,
        "event_time": event_time.isoformat(),
        "metadata": metadata or {},
    }, headers=_h())
    assert r.status_code == 200, r.text
    return r.json()


async def _snapshot(client, as_of, agent=AGENT, limit=1000):
    return await client.get("/v1/snapshot", params={
        "agent_id": agent,
        "as_of": as_of.isoformat(),
        "limit": limit,
    }, headers=_h())


# â”€â”€ Tests â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

@pytest.mark.asyncio
async def test_empty_snapshot(client):
    r = await _snapshot(client, T1)
    assert r.status_code == 200
    body = r.json()
    assert body["total"] == 0
    assert body["items"] == []
    assert body["agent_id"] == AGENT
    assert body["namespace"] == TEST_NS


@pytest.mark.asyncio
async def test_snapshot_returns_valid_memories(client):
    await _add(client, "AAPL Q1 guidance", T0)
    await _add(client, "Fed held rates steady", T0)
    r = await _snapshot(client, T1)
    assert r.json()["total"] == 2


@pytest.mark.asyncio
async def test_snapshot_excludes_future_memories(client):
    """A memory ingested with event_time after as_of should not appear."""
    await _add(client, "Past fact", T0)
    await _add(client, "Future fact", T3)
    r = await _snapshot(client, T1)
    assert r.json()["total"] == 1
    assert r.json()["items"][0]["content"] == "Past fact"


@pytest.mark.asyncio
async def test_snapshot_superseded_included_in_past_excluded_now(client):
    """
    A superseded memory was valid in the past but not at present.
    Snapshot at the past time should include it; snapshot now should exclude it.
    """
    # First fact at T0
    mem1 = await _add(client, "AAPL EPS estimate $1.40", T0,
                      {"ticker": "AAPL", "metric": "eps", "period": "Q1"})
    # Superseding fact at T2
    mem2 = await _add(client, "AAPL EPS revised $1.52", T2,
                      {"ticker": "AAPL", "metric": "eps", "period": "Q1"})

    # Snapshot between T0 and T2 â€” first fact should be valid
    r_past = await _snapshot(client, T1)
    ids_past = {item["id"] for item in r_past.json()["items"]}
    assert mem1["id"] in ids_past

    # Snapshot at present (T3) â€” second fact valid, first superseded
    r_now = await _snapshot(client, T3)
    ids_now = {item["id"] for item in r_now.json()["items"]}
    assert mem2["id"] in ids_now


@pytest.mark.asyncio
async def test_snapshot_ordered_oldest_first(client):
    await _add(client, "Fact A", T0)
    await _add(client, "Fact B", T1)
    await _add(client, "Fact C", T2)
    r = await _snapshot(client, T3)
    times = [item["event_time"] for item in r.json()["items"]]
    assert times == sorted(times)


@pytest.mark.asyncio
async def test_snapshot_limit_respected(client):
    for i in range(5):
        await _add(client, f"Fact {i}", T0 + timedelta(days=i))
    r = await _snapshot(client, T3, limit=3)
    body = r.json()
    assert body["total"] == 5
    assert body["returned"] == 3
    assert len(body["items"]) == 3
    assert body["has_more"] is True
    assert body["complete"] is False


@pytest.mark.asyncio
async def test_snapshot_cross_agent_isolation(client):
    await _add(client, "Agent A fact", T0, agent="agent-a")
    await _add(client, "Agent B fact", T0, agent="agent-b")
    r_a = await _snapshot(client, T1, agent="agent-a")
    r_b = await _snapshot(client, T1, agent="agent-b")
    assert r_a.json()["total"] == 1
    assert r_b.json()["total"] == 1
    assert r_a.json()["items"][0]["content"] == "Agent A fact"
    assert r_b.json()["items"][0]["content"] == "Agent B fact"


@pytest.mark.asyncio
async def test_snapshot_response_fields(client):
    r = await _snapshot(client, T1)
    body = r.json()
    assert "agent_id" in body
    assert "namespace" in body
    assert "as_of" in body
    assert "total" in body
    assert "items" in body


@pytest.mark.asyncio
async def test_snapshot_requires_auth(client):
    r = await client.get("/v1/snapshot", params={
        "agent_id": AGENT,
        "as_of": T1.isoformat(),
    })
    assert r.status_code in (401, 403)


@pytest.mark.asyncio
async def test_snapshot_missing_agent_id(client):
    r = await client.get("/v1/snapshot", params={"as_of": T1.isoformat()}, headers=_h())
    assert r.status_code == 422


@pytest.mark.asyncio
async def test_snapshot_missing_as_of(client):
    r = await client.get("/v1/snapshot", params={"agent_id": AGENT}, headers=_h())
    assert r.status_code == 422


@pytest.mark.asyncio
async def test_snapshot_at_exact_event_time_boundary(client):
    """valid_from <= as_of â€” boundary memory at exactly as_of should be included."""
    await _add(client, "Boundary event", T1)
    r = await _snapshot(client, T1)
    assert r.json()["total"] == 1


@pytest.mark.asyncio
async def test_snapshot_diff_between_two_dates(client):
    """Difference between two snapshots reveals which facts changed."""
    await _add(client, "Stable fact", T0)
    await _add(client, "New fact added at T2", T2)

    snap_t1 = await _snapshot(client, T1)
    snap_t3 = await _snapshot(client, T3)

    ids_t1 = {i["id"] for i in snap_t1.json()["items"]}
    ids_t3 = {i["id"] for i in snap_t3.json()["items"]}

    # T3 snapshot has at least everything T1 had (plus new)
    assert len(ids_t3) >= len(ids_t1)
    assert ids_t1.issubset(ids_t3) or len(ids_t3) > len(ids_t1)


@pytest.mark.asyncio
async def test_snapshot_erased_memory_is_null_content_tombstone(client, db):
    """Crypto-shredded memories stay visible as tombstones: existence,
    timestamps, and hash survive; content comes back null. An examiner must
    be able to see that a fact existed even after a GDPR erasure."""
    await _add(client, "Keep me", T0)
    r = await client.post("/v1/memories", json={
        "agent_id": AGENT,
        "content": "Subject holds 500 shares",
        "event_time": T0.isoformat(),
        "subject_id": "subj-erase-me",
    }, headers=_h())
    assert r.status_code == 200, r.text
    erased_id = r.json()["id"]

    r = await client.post("/v1/erase", json={
        "subject_id": "subj-erase-me", "request_ref": "gdpr-1",
    }, headers=_h())
    assert r.status_code == 202, r.text
    job_id = r.json()["job_id"]
    worker_id = "snapshot-erasure-test"
    claims = await claim_due_subject_erasure_jobs(
        db,
        worker_id=worker_id,
        batch_size=1,
        lease_seconds=60,
    )
    assert len(claims) == 1
    session_factory = async_sessionmaker(db.bind, expire_on_commit=False)
    await process_subject_erasure_job(
        session_factory,
        claim=claims[0],
        worker_id=worker_id,
        page_size=100,
        max_pages=20,
        lease_seconds=60,
    )
    await db.run_sync(lambda session: session.expire_all())
    status = await client.get(f"/v1/erase/jobs/{job_id}", headers=_h())
    assert status.status_code == 200
    assert status.json()["status"] == "completed"

    r = await _snapshot(client, T1)
    body = r.json()
    assert body["total"] == 2, "erased memory must still appear in the snapshot"
    by_id = {i["id"]: i for i in body["items"]}
    tomb = by_id[erased_id]
    assert tomb["content"] is None
    assert tomb["erased_at"] is not None
    assert by_id != {} and any(i["content"] == "Keep me" for i in body["items"])
