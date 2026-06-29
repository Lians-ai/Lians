"""
Context-assembly endpoint + MMR reranker.
"""
from __future__ import annotations

import hashlib
import sys
import pytest
import pytest_asyncio
from datetime import datetime, timezone
from pathlib import Path

from httpx import AsyncClient, ASGITransport

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.lians.main import app
from src.lians.db import get_db
from src.lians.models import ApiKey

NS = "ctx-ns"
KEY = "ctx-key"
AGENT = "ctx-agent"
T = datetime(2026, 1, 1, tzinfo=timezone.utc)


# ── MMR unit test (pure function) ───────────────────────────────────────────────


class _FakeMem:
    def __init__(self, emb):
        self.embedding = emb


def test_mmr_promotes_diversity():
    from src.lians.ranking import mmr_rerank
    a = _FakeMem([1.0, 0.0])   # a and b are near-duplicates
    b = _FakeMem([1.0, 0.0])
    c = _FakeMem([0.0, 1.0])   # c is orthogonal (diverse)
    results = [(a, 0.90, "a"), (b, 0.85, "b"), (c, 0.50, "c")]

    out = mmr_rerank(results, lambda_=0.5)
    # Top by relevance is 'a'; MMR should then prefer the diverse 'c' over the
    # near-duplicate 'b', even though 'b' has higher raw relevance.
    assert out[0][2] == "a"
    assert out[1][2] == "c"


def test_mmr_lambda_one_is_pure_relevance():
    from src.lians.ranking import mmr_rerank
    a = _FakeMem([1.0, 0.0])
    b = _FakeMem([1.0, 0.0])
    c = _FakeMem([0.0, 1.0])
    results = [(a, 0.90, "a"), (b, 0.85, "b"), (c, 0.50, "c")]
    out = mmr_rerank(results, lambda_=1.0)
    assert [r[2] for r in out] == ["a", "b", "c"]  # strict relevance order


# ── Context endpoint (app-level) ────────────────────────────────────────────────


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


async def _add(client, content, ticker="NVDA"):
    r = await client.post("/v1/memories", headers=_h(), json={
        "agent_id": AGENT, "content": content, "event_time": T.isoformat(),
        "metadata": {"ticker": ticker, "metric": "guidance"},
    })
    assert r.status_code == 200, r.text


@pytest.mark.asyncio
async def test_context_returns_injectable_block(client):
    await _add(client, "NVDA FY2026 revenue guidance raised to $40B")
    r = await client.post("/v1/context", headers=_h(), json={
        "agent_id": AGENT, "query": "NVDA revenue guidance", "k": 5,
    })
    assert r.status_code == 200, r.text
    body = r.json()
    assert "NVDA FY2026 revenue guidance" in body["context"]
    assert body["context"].startswith("Relevant facts")
    assert body["token_estimate"] > 0
    assert len(body["memories"]) >= 1


@pytest.mark.asyncio
async def test_context_respects_token_budget(client):
    for i in range(6):
        await _add(client, f"Long fact number {i} " + ("padding " * 30), ticker=f"T{i}")
    r = await client.post("/v1/context", headers=_h(), json={
        "agent_id": AGENT, "query": "fact", "k": 10, "max_tokens": 80,
    })
    body = r.json()
    assert body["truncated"] is True
    assert body["token_estimate"] <= 80


@pytest.mark.asyncio
async def test_context_mmr_flag_ok(client):
    await _add(client, "AAPL gross margin expanded to 46%", ticker="AAPL")
    r = await client.post("/v1/context", headers=_h(), json={
        "agent_id": AGENT, "query": "AAPL margin", "k": 5, "mmr": True,
    })
    assert r.status_code == 200
    assert "AAPL" in r.json()["context"]
