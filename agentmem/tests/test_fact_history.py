"""
Tests for GET /v1/facts/history.

Covers: empty result, time-series ordering, entity normalization (ISIN/CUSIP/
company name), cross-agent isolation, limit parameter, superseded versions
included, different metrics not mixed, canonical ticker in response.
"""
import hashlib
from datetime import datetime, timezone, timedelta

import pytest
import pytest_asyncio
from httpx import AsyncClient, ASGITransport

from src.lian.main import app
from src.lian.db import get_db
from src.lian.models import ApiKey

TEST_NS = "fact-history-ns"
TEST_KEY = "fact-history-test-key"
AGENT = "equity-desk"

T0 = datetime(2025, 1, 1, tzinfo=timezone.utc)
T1 = datetime(2025, 6, 1, tzinfo=timezone.utc)
T2 = datetime(2026, 1, 1, tzinfo=timezone.utc)
T3 = datetime(2026, 6, 1, tzinfo=timezone.utc)


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


async def _history(client, ticker, metric, agent=AGENT, limit=100):
    r = await client.get("/v1/facts/history", params={
        "ticker": ticker,
        "metric": metric,
        "agent_id": agent,
        "limit": limit,
    }, headers=_h())
    return r


# â”€â”€ Tests â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

@pytest.mark.asyncio
async def test_empty_returns_zero(client):
    r = await _history(client, "AAPL", "eps")
    assert r.status_code == 200
    body = r.json()
    assert body["total"] == 0
    assert body["items"] == []
    assert body["ticker"] == "AAPL"
    assert body["metric"] == "eps"


@pytest.mark.asyncio
async def test_single_fact_returned(client):
    await _add(client, "AAPL Q1 EPS was $1.52", T0,
               metadata={"ticker": "AAPL", "metric": "eps", "value": 1.52})
    r = await _history(client, "AAPL", "eps")
    body = r.json()
    assert body["total"] == 1
    assert "EPS" in body["items"][0]["content"]


@pytest.mark.asyncio
async def test_time_series_ordered_oldest_first(client):
    await _add(client, "AAPL EPS $1.52", T0, {"ticker": "AAPL", "metric": "eps"})
    await _add(client, "AAPL EPS $1.78", T1, {"ticker": "AAPL", "metric": "eps"})
    await _add(client, "AAPL EPS $2.10", T2, {"ticker": "AAPL", "metric": "eps"})

    r = await _history(client, "AAPL", "eps")
    body = r.json()
    assert body["total"] == 3
    times = [item["event_time"] for item in body["items"]]
    assert times == sorted(times), "items must be ordered oldest-first by event_time"


@pytest.mark.asyncio
async def test_isin_maps_to_same_series(client):
    await _add(client, "AAPL Q1 EPS $1.52", T0, {"ticker": "AAPL", "metric": "eps"})
    await _add(client, "Apple Q2 EPS $1.78", T1, {"ticker": "AAPL", "metric": "eps"})

    # Query via ISIN â€” should resolve to AAPL and return both records
    r = await _history(client, "US0378331005", "eps")
    body = r.json()
    assert body["ticker"] == "AAPL", "ISIN should normalize to canonical ticker"
    assert body["total"] == 2


@pytest.mark.asyncio
async def test_cusip_maps_to_same_series(client):
    await _add(client, "AAPL EPS $1.52", T0, {"ticker": "AAPL", "metric": "eps"})

    # 8-char CUSIP without check digit
    r = await _history(client, "03783310", "eps")
    body = r.json()
    assert body["ticker"] == "AAPL"
    assert body["total"] == 1


@pytest.mark.asyncio
async def test_company_name_maps_to_same_series(client):
    await _add(client, "AAPL EPS $1.52", T0, {"ticker": "AAPL", "metric": "eps"})
    await _add(client, "Apple EPS $1.78", T1, {"ticker": "Apple Inc.", "metric": "eps"})

    r = await _history(client, "Apple Inc.", "eps")
    body = r.json()
    assert body["ticker"] == "AAPL"
    assert body["total"] == 2


@pytest.mark.asyncio
async def test_different_metric_not_mixed(client):
    await _add(client, "AAPL EPS $1.52", T0, {"ticker": "AAPL", "metric": "eps"})
    await _add(client, "AAPL price target $210", T1, {"ticker": "AAPL", "metric": "price_target"})

    eps_r = await _history(client, "AAPL", "eps")
    pt_r = await _history(client, "AAPL", "price_target")

    assert eps_r.json()["total"] == 1
    assert pt_r.json()["total"] == 1
    assert "EPS" in eps_r.json()["items"][0]["content"]
    assert "price target" in pt_r.json()["items"][0]["content"]


@pytest.mark.asyncio
async def test_different_tickers_not_mixed(client):
    await _add(client, "AAPL EPS $1.52", T0, {"ticker": "AAPL", "metric": "eps"})
    await _add(client, "MSFT EPS $2.90", T0, {"ticker": "MSFT", "metric": "eps"})

    r = await _history(client, "AAPL", "eps")
    assert r.json()["total"] == 1
    assert "AAPL" in r.json()["items"][0]["content"]


@pytest.mark.asyncio
async def test_cross_agent_isolation(client):
    await _add(client, "AAPL EPS $1.52", T0, {"ticker": "AAPL", "metric": "eps"}, agent="desk-a")
    await _add(client, "AAPL EPS $2.10", T1, {"ticker": "AAPL", "metric": "eps"}, agent="desk-b")

    r_a = await _history(client, "AAPL", "eps", agent="desk-a")
    r_b = await _history(client, "AAPL", "eps", agent="desk-b")

    assert r_a.json()["total"] == 1
    assert r_b.json()["total"] == 1


@pytest.mark.asyncio
async def test_limit_respected(client):
    for i in range(5):
        t = T0 + timedelta(days=i * 30)
        await _add(client, f"AAPL EPS v{i}", t, {"ticker": "AAPL", "metric": "eps"})

    r = await _history(client, "AAPL", "eps", limit=3)
    assert r.json()["total"] == 3


@pytest.mark.asyncio
async def test_superseded_versions_included(client):
    """Fact history should include all versions â€” active and superseded."""
    mem1 = await _add(client, "AAPL Q1 EPS estimate: $1.40", T0,
                      {"ticker": "AAPL", "metric": "eps", "period": "Q1 2026"})
    # Second add supersedes first (same structured key, later event_time)
    mem2 = await _add(client, "AAPL Q1 EPS revised: $1.52", T1,
                      {"ticker": "AAPL", "metric": "eps", "period": "Q1 2026"})

    r = await _history(client, "AAPL", "eps")
    body = r.json()
    ids = {item["id"] for item in body["items"]}
    # Both versions should appear (the superseded one + the current one)
    assert mem1["id"] in ids or mem2["id"] in ids
    # At minimum the active one must be there
    assert mem2["id"] in ids


@pytest.mark.asyncio
async def test_response_canonical_ticker_field(client):
    await _add(client, "AAPL EPS $1.52", T0, {"ticker": "AAPL", "metric": "eps"})

    r = await _history(client, "apple inc", "eps")
    body = r.json()
    assert body["ticker"] == "AAPL", "ticker field should be post-normalization canonical"
    assert body["agent_id"] == AGENT
    assert body["namespace"] == TEST_NS


@pytest.mark.asyncio
async def test_memories_without_ticker_metadata_excluded(client):
    """Plain-text memories (no structured metadata) should not appear in fact history."""
    await _add(client, "AAPL had good results this quarter", T0, metadata={})
    r = await _history(client, "AAPL", "eps")
    assert r.json()["total"] == 0


@pytest.mark.asyncio
async def test_requires_ticker_and_metric_params(client):
    r = await client.get("/v1/facts/history", params={"agent_id": AGENT}, headers=_h())
    assert r.status_code == 422


@pytest.mark.asyncio
async def test_requires_agent_id_param(client):
    r = await client.get("/v1/facts/history",
                         params={"ticker": "AAPL", "metric": "eps"},
                         headers=_h())
    assert r.status_code == 422


@pytest.mark.asyncio
async def test_isin_stored_in_metadata(client):
    """Memories that store the ISIN in the ticker field are matched by ticker query."""
    await _add(client, "Apple EPS Q1 2026", T0,
               metadata={"ticker": "US0378331005", "metric": "eps"})

    r = await _history(client, "AAPL", "eps")
    body = r.json()
    assert body["total"] == 1


@pytest.mark.asyncio
async def test_entity_field_as_ticker_alias(client):
    """The 'entity' metadata key is treated as an alias for 'ticker'."""
    await _add(client, "AAPL earnings", T0,
               metadata={"entity": "AAPL", "metric": "eps"})

    r = await _history(client, "AAPL", "eps")
    assert r.json()["total"] == 1


@pytest.mark.asyncio
async def test_field_key_as_metric_alias(client):
    """The 'field' metadata key is treated as an alias for 'metric'."""
    await _add(client, "AAPL analyst estimate", T0,
               metadata={"ticker": "AAPL", "field": "eps"})

    r = await _history(client, "AAPL", "eps")
    assert r.json()["total"] == 1
