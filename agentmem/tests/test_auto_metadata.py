"""
Auto-metadata extraction — auto-supersession parity (mem0 / Zep style).

Two layers under test:
  1. The deterministic finance extractor (pure) — turns free text into
     {ticker, metric}.
  2. The end-to-end path — with auto_metadata_enabled, a plain-text write with
     NO caller metadata is auto-keyed so the deterministic keyed-supersession
     fast path fires; with it disabled, nothing is inferred.
"""
from __future__ import annotations

import hashlib
from datetime import datetime, timezone

import pytest
import pytest_asyncio
from httpx import AsyncClient, ASGITransport

from src.lians.main import app
from src.lians.db import get_db
from src.lians.models import ApiKey
from src.lians.config import get_settings
from src.lians.adapters.finance import extract_finance_keys

NS = "auto-meta-ns"
KEY = "auto-meta-key"
AGENT = "auto-meta-agent"


def _ts(year, month=1, day=1):
    return datetime(year, month, day, tzinfo=timezone.utc)


# ── Layer 1: pure deterministic extractor ───────────────────────────────────────


def test_extract_ticker_and_metric_from_plain_text():
    keys = extract_finance_keys("AAPL price target raised to $250")
    assert keys == {"ticker": "AAPL", "metric": "price_target"}


def test_extract_company_name_normalizes_to_ticker():
    keys = extract_finance_keys("Nvidia guidance was lifted for the quarter")
    assert keys["ticker"] == "NVDA"
    assert keys["metric"] == "guidance"


def test_extract_cashtag():
    keys = extract_finance_keys("Big beat on $MSFT revenue this quarter")
    assert keys["ticker"] == "MSFT"
    assert keys["metric"] == "revenue"


def test_extract_eps_beats_generic_earnings():
    assert extract_finance_keys("AAPL earnings per share came in at $1.52")["metric"] == "eps"


def test_extract_partial_ticker_only():
    keys = extract_finance_keys("Tesla had an interesting day")
    assert keys == {"ticker": "TSLA"}


def test_extract_nothing_on_plain_text():
    # No known entity, no metric keyword — bare all-caps must not false-positive.
    assert extract_finance_keys("THE WEATHER WAS NICE TODAY") == {}


# ── Layer 2: end-to-end ingestion path ──────────────────────────────────────────


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


def _h():
    return {"X-API-Key": KEY}


async def _add(client, content, event_time, metadata=None):
    r = await client.post("/v1/memories", headers=_h(), json={
        "agent_id": AGENT,
        "content": content,
        "event_time": event_time.isoformat(),
        "metadata": metadata or {},
    })
    assert r.status_code == 200, r.text
    return r.json()


async def _active_contents(client, as_of):
    r = await client.get("/v1/snapshot", headers=_h(), params={
        "agent_id": AGENT, "as_of": as_of.isoformat(), "limit": 500,
    })
    assert r.status_code == 200, r.text
    return [m["content"] for m in r.json()["items"] if m["valid_to"] is None]


@pytest.mark.asyncio
async def test_enabled_auto_keys_unlock_keyed_supersession(client, monkeypatch):
    monkeypatch.setenv("AUTO_METADATA_ENABLED", "true")
    get_settings.cache_clear()

    # Two plain-text writes, no caller metadata, same ticker+metric, newer wins.
    await _add(client, "AAPL price target set to $200", _ts(2026, 1, 1))
    newer = await _add(client, "AAPL price target raised to $250", _ts(2026, 2, 1))

    # The new memory was auto-keyed and provenance-tagged.
    assert newer["metadata"]["ticker"] == "AAPL"
    assert newer["metadata"]["metric"] == "price_target"
    assert newer["metadata"]["_auto_meta"]["method"] == "rule"
    assert newer["superseded_by"] is None

    # Deterministic keyed supersession fired: only the $250 fact is live.
    active = await _active_contents(client, _ts(2026, 12))
    assert any("$250" in c for c in active)
    assert not any("$200" in c for c in active), "older fact should have been superseded"


@pytest.mark.asyncio
async def test_disabled_by_default_leaves_metadata_untouched(client, monkeypatch):
    monkeypatch.delenv("AUTO_METADATA_ENABLED", raising=False)
    get_settings.cache_clear()

    mem = await _add(client, "AAPL price target raised to $250", _ts(2026, 2, 1))
    assert "ticker" not in mem["metadata"]
    assert "_auto_meta" not in mem["metadata"]


@pytest.mark.asyncio
async def test_caller_keys_are_authoritative(client, monkeypatch):
    monkeypatch.setenv("AUTO_METADATA_ENABLED", "true")
    get_settings.cache_clear()

    # Content mentions AAPL, but the caller explicitly tagged MSFT — caller wins,
    # and no auto-extraction runs.
    mem = await _add(client, "AAPL price target raised to $250", _ts(2026, 2, 1),
                     metadata={"ticker": "MSFT", "metric": "price_target"})
    assert mem["metadata"]["ticker"] == "MSFT"
    assert "_auto_meta" not in mem["metadata"]
