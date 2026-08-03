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
from sqlalchemy.ext.asyncio import async_sessionmaker

# Use a far-future sentinel for audit trail queries so that event_log rows
# (created_at â‰ˆ now) always satisfy `created_at <= AUDIT_AS_OF`.
AUDIT_AS_OF = datetime(2099, 1, 1, tzinfo=timezone.utc)

from lians.main import app  # noqa: E402
from lians.db import get_db  # noqa: E402
from lians.models import ApiKey  # noqa: E402
from lians.subject_erasure_service import (  # noqa: E402
    claim_due_subject_erasure_jobs,
    process_subject_erasure_job,
)


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


async def _complete_erasure(client, db, subject_id: str, request_ref: str) -> dict:
    response = await client.post("/v1/erase", headers=_h(), json={
        "subject_id": subject_id,
        "request_ref": request_ref,
    })
    assert response.status_code == 202, response.text
    queued = response.json()
    worker_id = f"api-erasure-test-{queued['job_id']}"
    claims = await claim_due_subject_erasure_jobs(
        db,
        worker_id=worker_id,
        batch_size=100,
        lease_seconds=60,
    )
    claim = next(claim for claim in claims if str(claim.job_id) == queued["job_id"])
    session_factory = async_sessionmaker(db.bind, expire_on_commit=False)
    await process_subject_erasure_job(
        session_factory,
        claim=claim,
        worker_id=worker_id,
        page_size=100,
        max_pages=20,
        lease_seconds=60,
    )
    await db.run_sync(lambda session: session.expire_all())
    status = await client.get(f"/v1/erase/jobs/{queued['job_id']}", headers=_h())
    assert status.status_code == 200, status.text
    assert status.json()["status"] == "completed"
    return status.json()


# ---------------------------------------------------------------------------
# Health
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_health(client, db):
    from unittest.mock import AsyncMock, patch
    from lians.config import get_settings
    from lians.version import EXPECTED_ALEMBIC_HEAD
    from sqlalchemy import text

    await db.execute(text("CREATE TABLE alembic_version (version_num VARCHAR(64) NOT NULL)"))
    await db.execute(
        text("INSERT INTO alembic_version (version_num) VALUES (:head)"),
        {"head": EXPECTED_ALEMBIC_HEAD},
    )
    await db.commit()

    settings = get_settings().model_copy(update={
        "recall_cache_enabled": True,
        "integration_worker_enabled": False,
        "impact_assessment_worker_enabled": False,
        "recorder_evidence_index_worker_enabled": False,
        "subject_erasure_worker_enabled": False,
        "scim_reconciliation_worker_enabled": False,
        "retention_prune_interval_hours": 0,
        "metrics_enabled": False,
        "supersession_llm_stage": False,
    })
    with (
        patch("lians.main.get_settings", return_value=settings),
        patch("lians.cache._get_redis") as mock_redis,
    ):
        mock_redis.return_value.ping = AsyncMock(return_value=True)
        resp = await client.get("/health")
    assert resp.status_code == 200, resp.text
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
async def test_erase_subject_tombstones_memory(client, db):
    """After erasure, the memory row exists (tombstone) but content is gone."""
    await client.post("/v1/memories", headers=_h(), json={
        "agent_id": AGENT,
        "content": "Client: Jane Doe, DOB 1985-03-12",
        "event_time": T0.isoformat(),
        "subject_id": "jane-doe-002",
        "metadata": {},
    })

    body = await _complete_erasure(client, db, "jane-doe-002", "GDPR-req-0042")
    assert body["snapshot"]["memories"] == 1
    assert body["progress"]["memories"] == 1
    assert body["request_ref"].startswith("lians:erasure-request:v2:hmac-sha256:")
    assert "GDPR-req-0042" not in body["request_ref"]


@pytest.mark.asyncio
async def test_erase_event_appears_in_audit_trail(client, db):
    """The durable request and terminal completion are both chain-anchored."""
    from sqlalchemy import select
    from lians.models import EventLog

    await client.post("/v1/memories", headers=_h(), json={
        "agent_id": AGENT,
        "content": "Client: Bob Smith, account $1M",
        "event_time": T0.isoformat(),
        "subject_id": "bob-smith-003",
        "metadata": {},
    })
    await _complete_erasure(client, db, "bob-smith-003", "GDPR-req-0043")
    ops = set(
        (
            await db.execute(
                select(EventLog.op).where(
                    EventLog.namespace == TEST_NS,
                    EventLog.op.in_((
                        "subject_erasure_requested",
                        "subject_erasure_completed",
                    )),
                )
            )
        ).scalars()
    )
    assert ops == {"subject_erasure_requested", "subject_erasure_completed"}


@pytest.mark.asyncio
async def test_erased_memory_never_reaches_recall(client, db):
    """
    Regression (found in live limit-testing 2026-07-02): erase nulled the
    ciphertext but left the live_facts read-model row, so present-time recall
    returned the erased memory as a null-content tombstone — and the
    content-derived embedding plus plaintext metadata survived on the row.
    """
    add = await client.post("/v1/memories", headers=_h(), json={
        "agent_id": AGENT,
        "content": "patient Ana K takes 40mg atorvastatin, MRN 555-0199",
        "event_time": T0.isoformat(),
        "subject_id": "ana-k-004",
        "metadata": {"patient_name": "Ana K", "mrn": "555-0199"},
    })
    mem_id = add.json()["id"]

    erased = await _complete_erasure(client, db, "ana-k-004", "GDPR-req-0044")
    assert erased["progress"]["memories"] == 1

    # 1 · present-time recall returns no tombstone for the erased fact
    resp = await client.post("/v1/recall", headers=_h(), json={
        "agent_id": AGENT, "query": "Ana atorvastatin MRN", "k": 10,
    })
    assert resp.status_code == 200
    ids = [m["id"] for m in resp.json()["memories"]]
    assert mem_id not in ids, "erased memory leaked into recall as a tombstone"

    # the tombstone row itself remains reachable for audit (lineage still 200)
    resp = await client.get(f"/v1/memories/{mem_id}/lineage", headers=_h())
    assert resp.status_code == 200


@pytest.mark.asyncio
async def test_erase_shreds_embedding_and_metadata(db, client):
    from lians.models import LiveFact, Memory
    from sqlalchemy import select
    from uuid import UUID

    add = await client.post("/v1/memories", headers=_h(), json={
        "agent_id": AGENT,
        "content": "subject Rex B salary is $250k",
        "event_time": T0.isoformat(),
        "subject_id": "rex-b-005",
        "metadata": {"person": "Rex B", "field": "salary"},
    })
    mem_id = UUID(add.json()["id"])
    await _complete_erasure(client, db, "rex-b-005", "GDPR-req-0045")

    mem = (await db.execute(select(Memory).where(Memory.id == mem_id))).scalar_one()
    assert mem.erased_at is not None
    assert mem.content_encrypted is None
    assert mem.embedding is None, "content-derived embedding must be shredded on erase"
    assert mem.metadata_ in ({}, None), "plaintext metadata must be scrubbed on erase"

    lf = (await db.execute(select(LiveFact).where(LiveFact.memory_id == mem_id))).scalar_one_or_none()
    assert lf is None, "live_facts read-model row must be purged on erase"
