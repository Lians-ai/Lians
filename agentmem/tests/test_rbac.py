"""
RBAC: an API key's named role expands to a scope set at auth time.
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

NS = "rbac-ns"
AGENT = "rbac-agent"
T = datetime(2026, 1, 1, tzinfo=timezone.utc)


def _sha(k):
    return hashlib.sha256(k.encode()).hexdigest()


@pytest_asyncio.fixture
async def client(db):
    db.add(ApiKey(hashed_key=_sha("ro"), namespace=NS, scopes=[], role="readonly"))
    db.add(ApiKey(hashed_key=_sha("an"), namespace=NS, scopes=[], role="analyst"))
    db.add(ApiKey(hashed_key=_sha("co"), namespace=NS, scopes=[], role="compliance"))
    await db.commit()

    async def _override():
        yield db

    app.dependency_overrides[get_db] = _override
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
            yield ac
    finally:
        app.dependency_overrides.clear()


def _h(key):
    return {"X-API-Key": key}


def _mem():
    return {"agent_id": AGENT, "content": "x", "event_time": T.isoformat()}


@pytest.mark.asyncio
async def test_readonly_can_read_not_write(client):
    rd = await client.post("/v1/recall", headers=_h("ro"),
                           json={"agent_id": AGENT, "query": "x", "k": 5})
    assert rd.status_code == 200
    wr = await client.post("/v1/memories", headers=_h("ro"), json=_mem())
    assert wr.status_code == 403


@pytest.mark.asyncio
async def test_analyst_can_write(client):
    wr = await client.post("/v1/memories", headers=_h("an"), json=_mem())
    assert wr.status_code == 200


@pytest.mark.asyncio
async def test_compliance_can_read_not_write(client):
    rd = await client.post("/v1/recall", headers=_h("co"),
                           json={"agent_id": AGENT, "query": "x", "k": 5})
    assert rd.status_code == 200
    wr = await client.post("/v1/memories", headers=_h("co"), json=_mem())
    assert wr.status_code == 403  # compliance inspects/certifies; it does not author
