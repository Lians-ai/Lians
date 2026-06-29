"""
Idempotency-Key + readiness probe tests.

- A retried POST /v1/memories with the same Idempotency-Key returns the original
  memory (exactly-once write), while a different key creates a new one.
- /livez is a cheap liveness probe; /readyz is the deep readiness check.
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

NS = "idem-ns"
KEY = "idem-key"
AGENT = "idem-agent"
T = datetime(2026, 1, 1, tzinfo=timezone.utc)


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


def _h(extra=None):
    h = {"X-API-Key": KEY}
    if extra:
        h.update(extra)
    return h


def _body(content):
    return {"agent_id": AGENT, "content": content, "event_time": T.isoformat(),
            "metadata": {"ticker": "NVDA", "metric": "eps"}}


@pytest.mark.asyncio
async def test_same_idempotency_key_returns_original(client):
    r1 = await client.post("/v1/memories", headers=_h({"Idempotency-Key": "abc-123"}),
                           json=_body("NVDA EPS $6.20"))
    assert r1.status_code == 200, r1.text
    # Retry with the SAME key but even a different body — must return the original.
    r2 = await client.post("/v1/memories", headers=_h({"Idempotency-Key": "abc-123"}),
                           json=_body("NVDA EPS $9.99"))
    assert r2.status_code == 200
    assert r1.json()["id"] == r2.json()["id"]
    assert r2.json()["content"] == "NVDA EPS $6.20"  # original, not the retry body


@pytest.mark.asyncio
async def test_different_idempotency_key_creates_new(client):
    r1 = await client.post("/v1/memories", headers=_h({"Idempotency-Key": "k1"}),
                           json=_body("AAPL EPS $1.50"))
    r2 = await client.post("/v1/memories", headers=_h({"Idempotency-Key": "k2"}),
                           json=_body("AAPL EPS $1.62"))
    assert r1.json()["id"] != r2.json()["id"]


@pytest.mark.asyncio
async def test_no_key_still_works(client):
    r = await client.post("/v1/memories", headers=_h(), json=_body("MSFT cloud $25B"))
    assert r.status_code == 200
    assert r.json()["id"]


@pytest.mark.asyncio
async def test_livez_is_cheap_and_alive(client):
    r = await client.get("/livez")
    assert r.status_code == 200
    assert r.json()["status"] == "alive"


@pytest.mark.asyncio
async def test_readyz_deep_check(client):
    r = await client.get("/readyz")
    assert r.status_code in (200, 503)          # deep check; shape always present
    assert "checks" in r.json()
    assert r.json()["checks"]["db"] == "ok"
