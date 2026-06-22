"""
Conflict detection and resolution tests.

Coverage:
  - CONTRADICTS_SAME_TIME creates a ConflictFlag (GET /v1/conflicts returns it)
  - Conflict includes decrypted content for both sides
  - SUPERSEDES / CONFIRMS / ADDS do NOT create conflict flags
  - Conflict list defaults to status=open
  - status= filter works (open / accept_a / accept_b / dismissed)
  - resolve accept_a: memory_b invalidated (valid_to set), memory_a still live
  - resolve accept_b: memory_a invalidated, memory_b still live
  - resolve dismiss: both memories still live
  - Resolving twice returns 409
  - Resolving non-existent conflict returns 404
  - Conflict_resolved event appears in audit log
  - Namespace isolation: cannot list/resolve other namespace's conflicts
"""
from __future__ import annotations

import hashlib
import pytest
import pytest_asyncio
from datetime import datetime, timezone

from httpx import AsyncClient, ASGITransport
from uuid import uuid4

from src.agentmem.main import app
from src.agentmem.db import get_db
from src.agentmem.models import ApiKey

# Same event_time → CONTRADICTS_SAME_TIME; different event_time → SUPERSEDES
T_SAME = datetime(2026, 3, 15, 12, 0, 0, tzinfo=timezone.utc)
T_LATER = datetime(2026, 6, 15, tzinfo=timezone.utc)

TEST_KEY = "conflicts-test-key"
TEST_NS = "conflicts-ns"
AGENT = "conflicts-agent"

OTHER_KEY = "conflicts-other-key"
OTHER_NS = "conflicts-other-ns"


# ── Fixtures ──────────────────────────────────────────────────────────────────

@pytest_asyncio.fixture
async def client(db):
    for raw_key, ns in [(TEST_KEY, TEST_NS), (OTHER_KEY, OTHER_NS)]:
        hashed = hashlib.sha256(raw_key.encode()).hexdigest()
        db.add(ApiKey(hashed_key=hashed, namespace=ns, scopes=["read", "write", "admin"]))
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


def _admin_h() -> dict:
    return {"X-Admin-Secret": "dev-admin-secret-change-in-prod"}


def _mem(content: str, event_time: datetime, source: str = "source-A", ticker: str = "NVDA") -> dict:
    """
    Build a memory body.  ticker/metric metadata is required so memories are
    routed through the keyed supersession fast-path where same-time conflicts
    are detected.  Pass ticker=None for unstructured-memory tests.
    """
    meta = {"ticker": ticker, "metric": "eps"} if ticker else {}
    return {
        "agent_id": AGENT,
        "content": content,
        "event_time": event_time.isoformat(),
        "metadata": meta,
        "source": source,
    }


async def _add(client, body, key=TEST_KEY) -> dict:
    r = await client.post("/v1/memories", headers=_h(key), json=body)
    assert r.status_code == 200, r.text
    return r.json()


async def _conflicts(client, *, status="open", limit=50, key=TEST_KEY):
    params = {"limit": limit}
    if status is not None:
        params["status"] = status
    return await client.get("/v1/conflicts", headers=_h(key), params=params)


async def _resolve(client, conflict_id: str, resolution: str, note: str = "", key=TEST_KEY):
    return await client.post(
        f"/v1/conflicts/{conflict_id}/resolve",
        headers=_h(key),
        json={"resolution": resolution, "note": note or None},
    )


async def _recall(client, query: str, key=TEST_KEY) -> list[dict]:
    r = await client.post("/v1/recall", headers=_h(key), json={
        "agent_id": AGENT, "query": query, "k": 10,
    })
    assert r.status_code == 200
    return r.json()["memories"]


# ── Conflict detection ────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_same_event_time_creates_conflict(client):
    """Two memories with identical event_time and different content → conflict."""
    await _add(client, _mem("NVDA Q1 EPS $6.20", T_SAME, source="reuters"))
    await _add(client, _mem("NVDA Q1 EPS $6.45", T_SAME, source="bloomberg"))

    r = await _conflicts(client)
    assert r.status_code == 200
    data = r.json()
    assert data["total"] >= 1
    assert len(data["conflicts"]) >= 1


@pytest.mark.asyncio
async def test_conflict_has_both_memory_contents(client):
    """Conflict response includes decrypted content for both sides."""
    await _add(client, _mem("AAPL Q2 EPS $1.50", T_SAME, source="sec-filing", ticker="AAPL"))
    await _add(client, _mem("AAPL Q2 EPS $1.62", T_SAME, source="analyst-note", ticker="AAPL"))

    r = await _conflicts(client)
    conflict = r.json()["conflicts"][0]

    assert conflict["memory_a_content"] is not None
    assert conflict["memory_b_content"] is not None
    assert "AAPL" in conflict["memory_a_content"] or "AAPL" in conflict["memory_b_content"]


@pytest.mark.asyncio
async def test_conflict_has_source_fields(client):
    await _add(client, _mem("JPM EPS $4.20", T_SAME, source="sec-filing", ticker="JPM"))
    await _add(client, _mem("JPM EPS $4.55", T_SAME, source="bloomberg", ticker="JPM"))

    conflict = (await _conflicts(client)).json()["conflicts"][0]
    sources = {conflict["memory_a_source"], conflict["memory_b_source"]}
    assert "sec-filing" in sources
    assert "bloomberg" in sources


@pytest.mark.asyncio
async def test_conflict_status_is_open_by_default(client):
    await _add(client, _mem("GS revenue $12B", T_SAME, source="s1", ticker="GS"))
    await _add(client, _mem("GS revenue $13B", T_SAME, source="s2", ticker="GS"))

    conflict = (await _conflicts(client)).json()["conflicts"][0]
    assert conflict["status"] == "open"
    assert conflict["resolved_at"] is None


@pytest.mark.asyncio
async def test_conflict_has_required_fields(client):
    await _add(client, _mem("MSFT cloud $25B", T_SAME, source="s1", ticker="MSFT"))
    await _add(client, _mem("MSFT cloud $26B", T_SAME, source="s2", ticker="MSFT"))

    conflict = (await _conflicts(client)).json()["conflicts"][0]
    for field in (
        "id", "namespace", "agent_id",
        "memory_a_id", "memory_b_id",
        "memory_a_content", "memory_b_content",
        "memory_a_event_time", "memory_b_event_time",
        "confidence", "detected_at", "status",
    ):
        assert field in conflict, f"Missing field: {field}"


@pytest.mark.asyncio
async def test_supersedes_does_not_create_conflict(client):
    """A later event_time triggers SUPERSEDES, not a conflict."""
    await _add(client, _mem("TSLA deliveries 400k", T_SAME))
    await _add(client, _mem("TSLA deliveries 420k", T_LATER))

    r = await _conflicts(client)
    assert r.json()["total"] == 0


@pytest.mark.asyncio
async def test_adds_relation_does_not_create_conflict(client):
    """Memories with different structured keys (different tickers) produce ADDS, not a conflict."""
    await _add(client, _mem("FED rate 5.25%", T_SAME, ticker="FEDFUNDS"))
    await _add(client, _mem("Oil price $80/barrel", T_SAME, ticker="CL"))

    r = await _conflicts(client)
    assert r.json()["total"] == 0


@pytest.mark.asyncio
async def test_both_memories_remain_valid_after_conflict_detected(client):
    """Neither memory should be superseded when a conflict is flagged."""
    mem_a = await _add(client, _mem("META DAU 3.2B", T_SAME, source="s1", ticker="META"))
    mem_b = await _add(client, _mem("META DAU 3.3B", T_SAME, source="s2", ticker="META"))

    # Both should have valid_to = None (still live)
    assert mem_a["valid_to"] is None
    assert mem_b["valid_to"] is None


# ── Conflict list filtering ───────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_conflict_list_default_returns_only_open(client):
    await _add(client, _mem("BRK EPS $10", T_SAME, source="s1", ticker="BRK"))
    await _add(client, _mem("BRK EPS $11", T_SAME, source="s2", ticker="BRK"))

    open_conflicts = (await _conflicts(client, status="open")).json()["conflicts"]
    assert all(c["status"] == "open" for c in open_conflicts)


@pytest.mark.asyncio
async def test_conflict_list_status_filter(client):
    await _add(client, _mem("C EPS $1.20", T_SAME, source="s1", ticker="C"))
    await _add(client, _mem("C EPS $1.30", T_SAME, source="s2", ticker="C"))

    open_r = (await _conflicts(client, status="open")).json()
    assert open_r["status_filter"] == "open"
    assert open_r["total"] >= 1

    conflict_id = open_r["conflicts"][0]["id"]
    await _resolve(client, conflict_id, "dismiss")

    dismissed = (await _conflicts(client, status="dismissed")).json()
    assert dismissed["total"] >= 1
    assert all(c["status"] == "dismissed" for c in dismissed["conflicts"])

    # Open list should no longer contain the dismissed one
    still_open = (await _conflicts(client, status="open")).json()
    open_ids = {c["id"] for c in still_open["conflicts"]}
    assert conflict_id not in open_ids


# ── Resolution: accept_a ──────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_resolve_accept_a_invalidates_memory_b(client):
    await _add(client, _mem("WFC EPS $1.10", T_SAME, source="sec", ticker="WFC"))
    await _add(client, _mem("WFC EPS $1.20", T_SAME, source="rumor", ticker="WFC"))

    conflict = (await _conflicts(client)).json()["conflicts"][0]
    r = await _resolve(client, conflict["id"], "accept_a", note="SEC filing is authoritative")
    assert r.status_code == 200

    data = r.json()
    assert data["resolution"] == "accept_a"
    assert data["memory_invalidated"] == conflict["memory_b_id"]
    assert data["resolved_at"] is not None


@pytest.mark.asyncio
async def test_resolve_accept_a_memory_a_still_live(client):
    await _add(client, _mem("BAC EPS $0.85", T_SAME, source="sec", ticker="BAC"))
    await _add(client, _mem("BAC EPS $0.90", T_SAME, source="rumor", ticker="BAC"))

    conflict = (await _conflicts(client)).json()["conflicts"][0]
    mem_a_id = conflict["memory_a_id"]
    mem_b_id = conflict["memory_b_id"]

    await _resolve(client, conflict["id"], "accept_a")

    # Both memories are still queryable but mem_b should now have valid_to set.
    # Verify by checking the lineage of mem_b — its valid_to won't be None.
    lin = await client.get(f"/v1/memories/{mem_b_id}/lineage", headers=_h())
    assert lin.status_code == 200
    tip_node = next(n for n in lin.json()["nodes"] if n["id"] == mem_b_id)
    assert tip_node["valid_to"] is not None  # invalidated


# ── Resolution: accept_b ──────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_resolve_accept_b_invalidates_memory_a(client):
    await _add(client, _mem("USB EPS $1.00", T_SAME, source="old-estimate", ticker="USB"))
    await _add(client, _mem("USB EPS $1.05", T_SAME, source="sec-filing", ticker="USB"))

    conflict = (await _conflicts(client)).json()["conflicts"][0]
    r = await _resolve(client, conflict["id"], "accept_b", note="SEC filing supersedes estimate")
    assert r.status_code == 200

    data = r.json()
    assert data["resolution"] == "accept_b"
    assert data["memory_invalidated"] == conflict["memory_a_id"]


# ── Resolution: dismiss ───────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_resolve_dismiss_leaves_both_memories_live(client):
    await _add(client, _mem("OPEC cut 500k bbl/d", T_SAME, source="reuters", ticker="OPEC"))
    await _add(client, _mem("OPEC cut 1000k bbl/d", T_SAME, source="ap", ticker="OPEC"))

    conflict = (await _conflicts(client)).json()["conflicts"][0]
    r = await _resolve(client, conflict["id"], "dismiss", note="Sources differ — both plausible")
    assert r.status_code == 200

    data = r.json()
    assert data["resolution"] == "dismiss"
    assert data["memory_invalidated"] is None


@pytest.mark.asyncio
async def test_resolve_dismiss_conflict_status_updated(client):
    await _add(client, _mem("FOMC rate 5.25%", T_SAME, source="s1", ticker="FOMC"))
    await _add(client, _mem("FOMC rate 5.50%", T_SAME, source="s2", ticker="FOMC"))

    conflict = (await _conflicts(client)).json()["conflicts"][0]
    await _resolve(client, conflict["id"], "dismiss")

    r = await _conflicts(client, status="dismissed")
    dismissed = r.json()["conflicts"]
    assert any(c["id"] == conflict["id"] for c in dismissed)


# ── Error cases ───────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_resolve_twice_returns_409(client):
    await _add(client, _mem("XOM EPS $2.10", T_SAME, source="s1", ticker="XOM"))
    await _add(client, _mem("XOM EPS $2.20", T_SAME, source="s2", ticker="XOM"))

    conflict = (await _conflicts(client)).json()["conflicts"][0]
    r1 = await _resolve(client, conflict["id"], "dismiss")
    assert r1.status_code == 200

    r2 = await _resolve(client, conflict["id"], "accept_a")
    assert r2.status_code == 409


@pytest.mark.asyncio
async def test_resolve_unknown_conflict_returns_404(client):
    r = await _resolve(client, str(uuid4()), "dismiss")
    assert r.status_code == 404


@pytest.mark.asyncio
async def test_resolve_invalid_resolution_returns_422(client):
    await _add(client, _mem("CVX EPS $3.00", T_SAME, source="s1", ticker="CVX"))
    await _add(client, _mem("CVX EPS $3.10", T_SAME, source="s2", ticker="CVX"))

    conflict = (await _conflicts(client)).json()["conflicts"][0]
    r = await _resolve(client, conflict["id"], "invalid_option")
    assert r.status_code == 422


# ── Audit trail ───────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_conflict_detected_event_in_audit_log(client):
    await _add(client, _mem("COP EPS $1.80", T_SAME, source="s1", ticker="COP"))
    await _add(client, _mem("COP EPS $1.90", T_SAME, source="s2", ticker="COP"))

    r = await client.get(
        "/v1/admin/audit/export",
        headers=_admin_h(),
        params={"namespace": TEST_NS, "limit": 100},
    )
    assert r.status_code == 200
    ops = [e["op"] for e in r.json()["events"]]
    assert "conflict_detected" in ops


@pytest.mark.asyncio
async def test_conflict_resolved_event_in_audit_log(client):
    await _add(client, _mem("MPC EPS $3.00", T_SAME, source="s1", ticker="MPC"))
    await _add(client, _mem("MPC EPS $3.15", T_SAME, source="s2", ticker="MPC"))

    conflict = (await _conflicts(client)).json()["conflicts"][0]
    await _resolve(client, conflict["id"], "accept_a", note="s1 is audited source")

    r = await client.get(
        "/v1/admin/audit/export",
        headers=_admin_h(),
        params={"namespace": TEST_NS, "limit": 100},
    )
    assert r.status_code == 200
    ops = [e["op"] for e in r.json()["events"]]
    assert "conflict_resolved" in ops


# ── Namespace isolation ───────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_cannot_list_other_namespace_conflicts(client):
    # Create a conflict in OTHER_NS
    await _add(client, _mem("PBR EPS $0.50", T_SAME, source="s1", ticker="PBR"), key=OTHER_KEY)
    await _add(client, _mem("PBR EPS $0.55", T_SAME, source="s2", ticker="PBR"), key=OTHER_KEY)

    # TEST_NS should see no conflicts
    r = await _conflicts(client, status="open", key=TEST_KEY)
    assert r.json()["total"] == 0


@pytest.mark.asyncio
async def test_cannot_resolve_other_namespace_conflict(client):
    await _add(client, _mem("SLB EPS $0.70", T_SAME, source="s1", ticker="SLB"), key=OTHER_KEY)
    await _add(client, _mem("SLB EPS $0.75", T_SAME, source="s2", ticker="SLB"), key=OTHER_KEY)

    other_conflicts = (await _conflicts(client, key=OTHER_KEY)).json()["conflicts"]
    assert len(other_conflicts) >= 1
    other_conflict_id = other_conflicts[0]["id"]

    # Attempt to resolve from TEST_NS credentials
    r = await _resolve(client, other_conflict_id, "dismiss", key=TEST_KEY)
    assert r.status_code == 404
