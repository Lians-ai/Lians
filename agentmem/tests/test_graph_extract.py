"""
Graph extraction: deterministic rule-based triplet extraction, and the
/v1/graph/extract endpoint that turns text into auditable edges.
"""
from __future__ import annotations

import hashlib
import pytest
import pytest_asyncio
from datetime import datetime, timezone

from httpx import AsyncClient, ASGITransport

from src.lians.main import app
from src.lians.db import get_db
from src.lians.models import ApiKey
from src.lians.graph_extract import extract_rule_based

NS = "ext-ns"
KEY = "ext-key"
AGENT = "ext-agent"
T = datetime(2026, 1, 1, tzinfo=timezone.utc)


# ── Rule-based extractor (pure) ─────────────────────────────────────────────────


def test_extract_employment_ownership_representation():
    text = ("Alice works at Acme. Fund A owns Issuer X. "
            "Attorney represents ClientX.")
    trips = extract_rule_based(text)
    assert ("Alice", "works_at", "Acme") in trips
    assert ("Fund A", "owns", "Issuer X") in trips
    assert ("Attorney", "represents", "ClientX") in trips


def test_extract_does_not_span_sentences():
    # "Acme" must not be swallowed into the next sentence's subject.
    trips = extract_rule_based("Bob works at Acme. Globex owns Initech.")
    assert ("Bob", "works_at", "Acme") in trips
    assert ("Globex", "owns", "Initech") in trips
    assert all("Acme Globex" not in t[0] for t in trips)


def test_extract_empty_on_plain_text():
    assert extract_rule_based("the weather was nice today") == []


# ── Endpoint ────────────────────────────────────────────────────────────────────


@pytest_asyncio.fixture
async def client(db):
    db.add(ApiKey(hashed_key=hashlib.sha256(KEY.encode()).hexdigest(),
                  namespace=NS, scopes=["read", "write"]))
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


@pytest.mark.asyncio
async def test_extract_writes_edges_and_path_connects(client):
    r = await client.post("/v1/graph/extract", headers=_h(), json={
        "agent_id": AGENT,
        "text": "Attorney represents ClientX. ClientX is adverse to PartyY.",
        "event_time": T.isoformat(),
    })
    assert r.status_code == 200, r.text
    body = r.json()
    rels = {(t["src"], t["rel_type"], t["dst"]) for t in body["extracted"]}
    assert ("Attorney", "represents", "ClientX") in rels
    assert ("ClientX", "adverse_to", "PartyY") in rels
    assert len(body["edges"]) == 2

    # The extracted edges are real graph edges — COI path resolves.
    p = await client.get("/v1/graph/path", headers=_h(),
                        params={"src": "Attorney", "dst": "PartyY", "agent_id": AGENT})
    assert p.status_code == 200
    assert p.json()["connected"] is True
    assert p.json()["hops"] == 2


# ── LLM extractor (extract_triplets) ────────────────────────────────────────────

import json as _json
from unittest.mock import AsyncMock, MagicMock, patch

from src.lians.config import get_settings
from src.lians.llm_adjudication import extract_triplets, _TRIPLET_CACHE
from src.lians.graph_extract import extract_llm, extract_relationships


def _mock_triplet_response(triplets):
    block = MagicMock()
    block.text = _json.dumps(triplets)
    msg = MagicMock()
    msg.content = [block]
    return msg


@pytest.fixture(autouse=True)
def _clear_triplet_cache():
    _TRIPLET_CACHE.clear()
    yield
    _TRIPLET_CACHE.clear()


@pytest.mark.asyncio
async def test_extract_triplets_parses_and_normalizes():
    with patch("anthropic.AsyncAnthropic") as MockCls:
        inst = AsyncMock()
        MockCls.return_value = inst
        inst.messages.create = AsyncMock(return_value=_mock_triplet_response(
            [["Alice", "works at", "Acme"], ["Fund A", "OWNS", "Issuer X"]]
        ))
        trips = await extract_triplets("Alice works at Acme. Fund A owns Issuer X.")

    # Relations lowercased + snake_cased ("works at" → "works_at").
    assert ("Alice", "works_at", "Acme") in trips
    assert ("Fund A", "owns", "Issuer X") in trips


@pytest.mark.asyncio
async def test_extract_triplets_caches_per_text():
    with patch("anthropic.AsyncAnthropic") as MockCls:
        inst = AsyncMock()
        MockCls.return_value = inst
        inst.messages.create = AsyncMock(return_value=_mock_triplet_response(
            [["A", "advises", "B"]]
        ))
        await extract_triplets("A advises B.")
        await extract_triplets("A advises B.")

    assert inst.messages.create.call_count == 1


@pytest.mark.asyncio
async def test_extract_llm_uses_triplets_when_enabled(monkeypatch):
    monkeypatch.setenv("GRAPH_EXTRACT_LLM", "true")
    get_settings.cache_clear()
    try:
        with patch("anthropic.AsyncAnthropic") as MockCls:
            inst = AsyncMock()
            MockCls.return_value = inst
            # A relationship the rule-based extractor would MISS, proving the LLM ran.
            inst.messages.create = AsyncMock(return_value=_mock_triplet_response(
                [["Zeta Capital", "backs", "Orion Labs"]]
            ))
            trips = await extract_relationships(
                "Zeta Capital quietly backs Orion Labs.", use_llm=True
            )
        assert ("Zeta Capital", "backs", "Orion Labs") in trips
    finally:
        get_settings.cache_clear()


@pytest.mark.asyncio
async def test_extract_llm_falls_back_to_rules_on_api_error(monkeypatch):
    monkeypatch.setenv("GRAPH_EXTRACT_LLM", "true")
    get_settings.cache_clear()
    try:
        with patch("anthropic.AsyncAnthropic") as MockCls:
            inst = AsyncMock()
            MockCls.return_value = inst
            inst.messages.create = AsyncMock(side_effect=RuntimeError("connection refused"))
            # extract_triplets raises → extract_llm falls back to the rule-based extractor.
            trips = await extract_llm("Alice works at Acme.")
        assert ("Alice", "works_at", "Acme") in trips
    finally:
        get_settings.cache_clear()
