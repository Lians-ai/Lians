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

from src.lians.main import app
from src.lians.db import get_db
from src.lians.models import ApiKey


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
    with patch("src.lians.cache._get_redis") as mock_redis:
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
# Studio inventory and controls
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_memory_inventory_separates_active_and_historical_versions(client):
    await client.post("/v1/memories", headers=_h(), json={
        "agent_id": AGENT,
        "content": "The user prefers the dark theme",
        "event_time": T0.isoformat(),
        "metadata": {"entity": "profile", "field": "theme"},
    })
    await client.post("/v1/memories", headers=_h(), json={
        "agent_id": AGENT,
        "content": "The user prefers the light theme",
        "event_time": T1.isoformat(),
        "metadata": {"entity": "profile", "field": "theme"},
    })

    active = await client.get(
        f"/v1/memories?agent_id={AGENT}&state=active",
        headers=_h(READ_KEY),
    )
    assert active.status_code == 200
    assert active.json()["state"] == "active"
    assert any("light theme" in (m["content"] or "") for m in active.json()["memories"])
    assert not any("dark theme" in (m["content"] or "") for m in active.json()["memories"])

    historical = await client.get(
        f"/v1/memories?agent_id={AGENT}&state=historical",
        headers=_h(READ_KEY),
    )
    assert historical.status_code == 200
    assert historical.json()["total"] >= 1
    assert any("dark theme" in (m["content"] or "") for m in historical.json()["memories"])


@pytest.mark.asyncio
async def test_memory_control_pin_is_visible_and_audited(client):
    created = await client.post("/v1/memories", headers=_h(), json={
        "agent_id": AGENT,
        "content": "Always return concise explanations",
        "event_time": T0.isoformat(),
        "metadata": {"entity": "profile", "field": "response_style"},
        "importance": 0.6,
    })
    memory_id = created.json()["id"]

    denied = await client.post(
        f"/v1/memories/{memory_id}/control",
        headers=_h(READ_KEY),
        json={"agent_id": AGENT, "action": "pin", "actor": "developer@example.com"},
    )
    assert denied.status_code == 403

    controlled = await client.post(
        f"/v1/memories/{memory_id}/control",
        headers=_h(),
        json={
            "agent_id": AGENT,
            "action": "pin",
            "actor": "developer@example.com",
            "note": "Explicit product preference",
        },
    )
    assert controlled.status_code == 200
    assert controlled.json()["status"] == "pinned"
    assert controlled.json()["importance"] == 1.0

    inventory = await client.get(
        f"/v1/memories?agent_id={AGENT}&state=active", headers=_h()
    )
    pinned = next(m for m in inventory.json()["memories"] if m["id"] == memory_id)
    assert pinned["importance"] == 1.0
    assert pinned["metadata"]["_pinned"] is True
    assert pinned["metadata"]["_studio_control"]["actor"] == "developer@example.com"

    audit = await client.get(
        "/v1/audit/reconstruct",
        headers=_h(),
        params={"agent_id": AGENT, "as_of": AUDIT_AS_OF.isoformat()},
    )
    assert audit.status_code == 200
    assert any(row["op"] == "memory_control" for row in audit.json()["event_trail"])


@pytest.mark.asyncio
async def test_memory_control_replace_preserves_history(client):
    created = await client.post("/v1/memories", headers=_h(), json={
        "agent_id": AGENT,
        "content": "The user's preferred editor is Vim",
        "event_time": T0.isoformat(),
        "metadata": {"entity": "profile", "field": "editor"},
        "importance": 0.8,
    })
    memory_id = created.json()["id"]

    replaced = await client.post(
        f"/v1/memories/{memory_id}/control",
        headers=_h(),
        json={
            "agent_id": AGENT,
            "action": "replace",
            "actor": "developer@example.com",
            "correction": "The user's preferred editor is VS Code",
        },
    )
    assert replaced.status_code == 200
    replacement_id = replaced.json()["replacement_memory_id"]
    assert replacement_id

    active = await client.get(
        f"/v1/memories?agent_id={AGENT}&state=active", headers=_h()
    )
    assert any(m["id"] == replacement_id for m in active.json()["memories"])
    assert not any(m["id"] == memory_id for m in active.json()["memories"])

    historical = await client.get(
        f"/v1/memories?agent_id={AGENT}&state=historical", headers=_h()
    )
    original = next(m for m in historical.json()["memories"] if m["id"] == memory_id)
    assert original["content"] == "The user's preferred editor is Vim"
    assert original["superseded_by"] == replacement_id


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


@pytest.mark.asyncio
async def test_erased_memory_never_reaches_recall(client):
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

    resp = await client.post("/v1/erase", headers=_h(), json={
        "subject_id": "ana-k-004", "request_ref": "GDPR-req-0044",
    })
    assert resp.status_code == 200 and resp.json()["memories_erased"] == 1

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
    from src.lians.models import LiveFact, Memory
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
    await client.post("/v1/erase", headers=_h(), json={
        "subject_id": "rex-b-005", "request_ref": "GDPR-req-0045",
    })

    mem = (await db.execute(select(Memory).where(Memory.id == mem_id))).scalar_one()
    assert mem.erased_at is not None
    assert mem.content_encrypted is None
    assert mem.embedding is None, "content-derived embedding must be shredded on erase"
    assert mem.metadata_ in ({}, None), "plaintext metadata must be scrubbed on erase"

    lf = (await db.execute(select(LiveFact).where(LiveFact.memory_id == mem_id))).scalar_one_or_none()
    assert lf is None, "live_facts read-model row must be purged on erase"


# ---------------------------------------------------------------------------
# Policy profiles and hierarchical scopes
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_agent_policy_profile_is_versioned_and_applied_to_capture(client):
    catalog = await client.get("/v1/policy-profiles", headers=_h())
    assert catalog.status_code == 200
    assert {item["name"] for item in catalog.json()["profiles"]} >= {
        "balanced", "personal_assistant", "coding_agent", "regulated_analyst",
    }

    default = await client.get(f"/v1/agents/{AGENT}/policy", headers=_h())
    assert default.status_code == 200
    assert default.json()["profile"] == "balanced"
    assert default.json()["revision"] == 0

    assigned = await client.put(
        f"/v1/agents/{AGENT}/policy",
        headers=_h(),
        json={
            "profile": "personal_assistant",
            "actor": "product-owner",
            "expected_revision": 0,
        },
    )
    assert assigned.status_code == 200
    assert assigned.json()["revision"] == 1

    added = await client.post(
        "/v1/memories",
        headers=_h(),
        json=_mem("I prefer concise answers with a short example."),
    )
    assert added.status_code == 200
    body = added.json()
    assert body["importance"] >= 0.94
    assert body["metadata"]["_policy"] == {
        "profile": "personal_assistant",
        "profile_version": assigned.json()["profile_version"],
        "revision": 1,
    }


@pytest.mark.asyncio
async def test_policy_assignment_uses_optimistic_revision_check(client):
    first = await client.put(
        f"/v1/agents/{AGENT}/policy",
        headers=_h(),
        json={"profile": "coding_agent", "actor": "owner", "expected_revision": 0},
    )
    assert first.status_code == 200
    stale = await client.put(
        f"/v1/agents/{AGENT}/policy",
        headers=_h(),
        json={"profile": "balanced", "actor": "owner", "expected_revision": 0},
    )
    assert stale.status_code == 409


@pytest.mark.asyncio
async def test_scoped_recall_inherits_parents_without_cross_project_leakage(client):
    memories = [
        ("The organization uses UTC for release timestamps.", "org/acme"),
        ("The platform team requires signed release artifacts.", "org/acme/team/platform"),
        ("Project Atlas deploys from the main branch.", "org/acme/team/platform/project/atlas"),
        ("Project Nova deploys only on Fridays.", "org/acme/team/platform/project/nova"),
    ]
    for content, scope in memories:
        response = await client.post(
            "/v1/memories",
            headers=_h(),
            json={**_mem(content), "scope": scope},
        )
        assert response.status_code == 200
        assert response.json()["scope"] == scope

    recalled = await client.post(
        "/v1/recall",
        headers=_h(),
        json={
            "agent_id": AGENT,
            "query": "What are the release and deployment rules?",
            "k": 20,
            "scope": "org/acme/team/platform/project/atlas",
            "include_parent_scopes": True,
        },
    )
    assert recalled.status_code == 200
    contents = [item["content"] for item in recalled.json()["memories"]]
    assert any("organization uses UTC" in content for content in contents)
    assert any("platform team requires" in content for content in contents)
    assert any("Project Atlas" in content for content in contents)
    assert all("Project Nova" not in content for content in contents)
    assert recalled.json()["receipt"]["scope"].endswith("project/atlas")


@pytest.mark.asyncio
async def test_scope_path_validation_rejects_ambiguous_values(client):
    response = await client.post(
        "/v1/memories",
        headers=_h(),
        json={**_mem("invalid scope"), "scope": "org/acme/project"},
    )
    assert response.status_code == 422


@pytest.mark.asyncio
async def test_fast_write_defers_embedding_without_putting_plaintext_in_job(db, client):
    from sqlalchemy import select
    from src.lians.memory_enrichment import (
        MEMORY_ENRICHMENT_JOB,
        handle_memory_enrichment_job,
    )
    from src.lians.models import DurableJob, Memory

    response = await client.post(
        "/v1/memories",
        headers=_h(),
        json={**_mem("Remember that release notes must include migration steps."), "write_mode": "fast"},
    )
    assert response.status_code == 200
    assert response.json()["enrichment_status"] == "pending"

    job = (
        await db.execute(
            select(DurableJob).where(DurableJob.kind == MEMORY_ENRICHMENT_JOB)
        )
    ).scalar_one()
    assert set(job.payload) == {"memory_id"}
    assert "release notes" not in str(job.payload)

    from uuid import UUID

    memory = await db.get(Memory, UUID(response.json()["id"]))
    assert memory.embedding is None
    await handle_memory_enrichment_job(db, job)
    await db.refresh(memory)
    assert memory.embedding is not None
    assert memory.metadata_["_enrichment"]["status"] == "complete"


@pytest.mark.asyncio
async def test_progressive_recall_stream_emits_fast_snapshot_before_deep_result(client):
    added = await client.post(
        "/v1/memories",
        headers=_h(),
        json=_mem("The Atlas release policy requires signed artifacts."),
    )
    assert added.status_code == 200

    async with client.stream(
        "POST",
        "/v1/recall/stream",
        headers=_h(),
        json={
            "agent_id": AGENT,
            "query": "What does the Atlas release policy require?",
            "mode": "deep",
            "k": 5,
        },
    ) as response:
        body = (await response.aread()).decode()

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/event-stream")
    assert body.index("event: started") < body.index("event: snapshot")
    assert body.index("event: snapshot") < body.index("event: final")
    assert body.index("event: final") < body.index("event: done")
    assert '"phase":"fast"' in body
    assert '"phase":"deep"' in body


# ---------------------------------------------------------------------------
# Hosted workspace and connector ingestion
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_workspace_metadata_and_stats_are_namespace_scoped(client):
    initial = await client.get("/v1/workspace", headers=_h())
    assert initial.status_code == 200
    assert initial.json()["namespace"] == TEST_NS
    assert initial.json()["plan"] == "developer"

    updated = await client.put(
        "/v1/workspace",
        headers=_h(),
        json={
            "display_name": "API product team",
            "plan": "team",
            "region": "us-east",
            "settings": {"default_scope": "org/acme/team/api"},
        },
    )
    assert updated.status_code == 200
    assert updated.json()["display_name"] == "API product team"
    assert updated.json()["stats"]["connectors"] == 0


@pytest.mark.asyncio
async def test_connector_rejects_credentials_and_ingests_idempotently(client):
    rejected = await client.post(
        "/v1/connectors",
        headers=_h(),
        json={
            "kind": "github",
            "name": "unsafe",
            "agent_id": AGENT,
            "config": {"oauth": {"access_token": "must-not-live-here"}},
        },
    )
    assert rejected.status_code == 422

    created = await client.post(
        "/v1/connectors",
        headers=_h(),
        json={
            "kind": "github",
            "name": "product-repository",
            "agent_id": AGENT,
            "scope": "org/acme/team/platform/project/atlas",
            "config": {"repository": "Lians-ai/Lians", "event_types": ["pull_request"]},
        },
    )
    assert created.status_code == 201
    connector_id = created.json()["id"]

    event = {
        "events": [{
            "external_id": "pr-42:merged",
            "content": "Pull request 42 established signed release artifacts as project policy.",
            "event_time": T1.isoformat(),
            "metadata": {"pull_request": 42},
            "importance": 0.8,
        }],
        "cursor": "github-cursor-9",
        "write_mode": "fast",
    }
    first = await client.post(
        f"/v1/connectors/{connector_id}/events", headers=_h(), json=event,
    )
    second = await client.post(
        f"/v1/connectors/{connector_id}/events", headers=_h(), json=event,
    )
    assert first.status_code == 200
    assert first.json()["accepted"] == 1
    assert first.json()["duplicates"] == 0
    assert second.status_code == 200
    assert second.json()["accepted"] == 0
    assert second.json()["duplicates"] == 1
    assert second.json()["memory_ids"] == first.json()["memory_ids"]

    inventory = await client.get(
        "/v1/memories",
        headers=_h(),
        params={"scope": "org/acme/team/platform/project/atlas"},
    )
    assert inventory.status_code == 200
    assert inventory.json()["total"] == 1
    memory = inventory.json()["memories"][0]
    assert memory["metadata"]["_connector"]["external_id"] == "pr-42:merged"
    assert memory["enrichment_status"] == "pending"


@pytest.mark.asyncio
async def test_readonly_key_can_list_but_not_configure_connectors(client):
    catalog = await client.get("/v1/connector-catalog", headers=_h(READ_KEY))
    assert catalog.status_code == 200
    assert catalog.json()["total"] >= 5
    forbidden = await client.post(
        "/v1/connectors",
        headers=_h(READ_KEY),
        json={"kind": "direct", "name": "nope", "agent_id": AGENT},
    )
    assert forbidden.status_code == 403


@pytest.mark.asyncio
async def test_control_plane_overview_consolidates_posture_evidence_and_queues(client):
    await client.post(
        "/v1/memories",
        headers=_h(),
        json=_mem("The control-plane fixture is active."),
    )
    response = await client.get(
        "/v1/control-plane/overview",
        headers=_h(),
        params={"verify_audit": "true"},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["namespace"] == TEST_NS
    assert body["memory"]["active"] == 1
    assert body["posture"]["audit_chain"]["status"] == "ok"
    assert set(body["operations"]["jobs"]) == {"pending", "leased", "dead", "completed"}
    assert "replayable_rate" in body["evidence"]
    assert body["retention"]["audit_retention_days"] == 1825

    forbidden = await client.get("/v1/control-plane/overview", headers=_h(READ_KEY))
    assert forbidden.status_code == 403
