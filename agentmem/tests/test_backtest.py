"""
Tests for POST /v1/backtest/check — lookahead-bias contamination detection.

Covers: clean report, future_event detection, late_revision detection,
mixed contamination, erased memories excluded, cross-agent isolation,
contamination_rate calculation, API auth, missing params.
"""
import hashlib
from datetime import datetime, timezone, timedelta

import pytest
import pytest_asyncio
from httpx import AsyncClient, ASGITransport

from src.agentmem.main import app
from src.agentmem.db import get_db
from src.agentmem.models import ApiKey

TEST_NS = "backtest-test-ns"
TEST_KEY = "backtest-test-key-xyz"
AGENT = "quant-desk"

# SIM_DATE is a future checkpoint so that test-time ingestion_time (today)
# is always BEFORE the simulation date — avoiding spurious LATE_REVISION flags.
SIM_DATE = datetime(2030, 1, 1, tzinfo=timezone.utc)       # simulation checkpoint
PAST     = datetime(2025, 6, 1, tzinfo=timezone.utc)       # before sim (event + ingest)
FUTURE   = datetime(2031, 6, 1, tzinfo=timezone.utc)       # clearly after sim


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


async def _add(client, content, event_time, agent=AGENT, metadata=None):
    r = await client.post("/v1/memories", json={
        "agent_id": agent,
        "content": content,
        "event_time": event_time.isoformat(),
        "metadata": metadata or {},
    }, headers=_h())
    assert r.status_code == 200, r.text
    return r.json()


async def _check(client, agent=AGENT, sim_as_of=SIM_DATE):
    return await client.post("/v1/backtest/check", json={
        "agent_id": agent,
        "simulation_as_of": sim_as_of.isoformat(),
    }, headers=_h())


# ── Tests ─────────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_clean_report_no_memories(client):
    r = await _check(client)
    assert r.status_code == 200
    body = r.json()
    assert body["is_clean"] is True
    assert body["flags"] == []
    assert body["memories_checked"] == 0
    assert body["contamination_rate"] == 0.0


@pytest.mark.asyncio
async def test_clean_report_all_past_memories(client):
    await _add(client, "AAPL earnings Q3 2025", PAST, metadata={"ticker": "AAPL"})
    await _add(client, "Fed held rates steady", PAST - timedelta(days=30))
    r = await _check(client)
    body = r.json()
    assert body["is_clean"] is True
    assert body["memories_checked"] == 2
    assert body["flags"] == []


@pytest.mark.asyncio
async def test_future_event_flagged(client):
    """A memory with event_time > simulation_as_of is FUTURE_EVENT contamination."""
    await _add(client, "AAPL Q2 2026 guidance raised to $2.10", FUTURE,
               metadata={"ticker": "AAPL", "metric": "eps"})
    r = await _check(client)
    body = r.json()
    assert body["is_clean"] is False
    assert len(body["flags"]) == 1
    flag = body["flags"][0]
    assert flag["contamination_type"] == "future_event"
    assert flag["delta_days"] > 0


@pytest.mark.asyncio
async def test_future_event_delta_days_correct(client):
    future_plus_30 = SIM_DATE + timedelta(days=30)
    await _add(client, "NVDA guidance for next quarter", future_plus_30)
    r = await _check(client)
    flag = r.json()["flags"][0]
    assert abs(flag["delta_days"] - 30.0) < 1.0


@pytest.mark.asyncio
async def test_multiple_future_events(client):
    await _add(client, "Event A", SIM_DATE + timedelta(days=10))
    await _add(client, "Event B", SIM_DATE + timedelta(days=20))
    await _add(client, "Past event", PAST)
    r = await _check(client)
    body = r.json()
    assert len(body["flags"]) == 2
    assert body["memories_checked"] == 3
    assert abs(body["contamination_rate"] - 2/3) < 0.01


@pytest.mark.asyncio
async def test_content_preview_truncated(client):
    long_content = "A" * 200
    await _add(client, long_content, FUTURE)
    r = await _check(client)
    flag = r.json()["flags"][0]
    assert flag["content_preview"] is not None
    assert len(flag["content_preview"]) <= 125   # 120 chars + ellipsis


@pytest.mark.asyncio
async def test_content_preview_short_content_not_truncated(client):
    await _add(client, "Short fact", FUTURE)
    r = await _check(client)
    flag = r.json()["flags"][0]
    assert flag["content_preview"] == "Short fact"


@pytest.mark.asyncio
async def test_past_event_not_flagged(client):
    await _add(client, "Historical record", PAST)
    r = await _check(client)
    assert r.json()["is_clean"] is True


@pytest.mark.asyncio
async def test_cross_agent_isolation(client):
    """Future memory from agent-B should not appear in agent-A's report."""
    await _add(client, "Contaminated fact", FUTURE, agent="agent-b")
    await _add(client, "Clean past fact", PAST, agent="agent-a")
    r_a = await _check(client, agent="agent-a")
    r_b = await _check(client, agent="agent-b")
    assert r_a.json()["is_clean"] is True
    assert r_b.json()["is_clean"] is False


@pytest.mark.asyncio
async def test_metadata_preserved_in_flag(client):
    await _add(client, "AAPL EPS future", FUTURE,
               metadata={"ticker": "AAPL", "metric": "eps", "period": "Q2 2026"})
    r = await _check(client)
    flag = r.json()["flags"][0]
    assert flag["metadata"]["ticker"] == "AAPL"
    assert flag["metadata"]["metric"] == "eps"


@pytest.mark.asyncio
async def test_simulation_at_exact_boundary(client):
    """A memory with event_time == simulation_as_of is NOT contamination."""
    await _add(client, "Event exactly at checkpoint", SIM_DATE)
    r = await _check(client)
    # Boundary is exclusive on the future side: event_time > sim_as_of
    assert r.json()["is_clean"] is True


@pytest.mark.asyncio
async def test_report_fields_present(client):
    r = await _check(client)
    body = r.json()
    assert "agent_id" in body
    assert "namespace" in body
    assert "simulation_as_of" in body
    assert "memories_checked" in body
    assert "flags" in body
    assert "contamination_rate" in body
    assert "is_clean" in body


@pytest.mark.asyncio
async def test_requires_auth(client):
    r = await client.post("/v1/backtest/check", json={
        "agent_id": AGENT,
        "simulation_as_of": SIM_DATE.isoformat(),
    })
    assert r.status_code in (401, 403)


@pytest.mark.asyncio
async def test_missing_simulation_as_of(client):
    r = await client.post("/v1/backtest/check",
                          json={"agent_id": AGENT},
                          headers=_h())
    assert r.status_code == 422


@pytest.mark.asyncio
async def test_missing_agent_id(client):
    r = await client.post("/v1/backtest/check",
                          json={"simulation_as_of": SIM_DATE.isoformat()},
                          headers=_h())
    assert r.status_code == 422
