"""
WORM posture endpoint — reports the SEC 17a-4 immutability posture.
"""
from __future__ import annotations

import hashlib
from types import SimpleNamespace

import pytest
import pytest_asyncio

from httpx import AsyncClient, ASGITransport

from src.lians.main import app
from src.lians.db import get_db
from src.lians.models import ApiKey

NS = "worm-ns"
KEY = "worm-key"


@pytest_asyncio.fixture
async def client(db):
    db.add(ApiKey(hashed_key=hashlib.sha256(KEY.encode()).hexdigest(),
                  namespace=NS, scopes=["read"]))
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
async def test_worm_posture_default(client):
    r = await client.get("/v1/compliance/worm", headers=_h())
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["audit_chain_append_only"] is True       # always — Lians never mutates event_log
    assert body["audit_chain_status"] in ("ok", "unchecked")
    assert body["worm_mode"] is False                     # not attested by default
    assert body["physical_worm_attested"] is False
    assert body["standard"] == "SEC 17a-4(f)"
    assert "WORM_MODE" in body["recommendation"]


@pytest.mark.asyncio
async def test_worm_posture_attested(client, monkeypatch):
    monkeypatch.setattr("src.lians.config.get_settings", lambda: SimpleNamespace(worm_mode=True))
    r = await client.get("/v1/compliance/worm", headers=_h())
    body = r.json()
    assert body["worm_mode"] is True
    assert body["physical_worm_attested"] is True
    # Attested wording must stay factual: controls attested, compliance is
    # the operator's — the API must never self-assert "compliant".
    assert "controls attested" in body["recommendation"]
    assert "compliant posture" not in body["recommendation"]
