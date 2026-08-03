"""
HTTP-level tests for the relationship-graph routes (/v1/graph/*).

Confirms the endpoints, auth scoping, and namespace isolation work end-to-end
through the FastAPI app — complementing the service-level tests in test_graph.py.
"""
from __future__ import annotations

import hashlib
import pytest
import pytest_asyncio
from datetime import datetime, timezone

from httpx import AsyncClient, ASGITransport

from lians.main import app
from lians.db import get_db
from lians.models import ApiKey

NS = "graph-ns"
KEY = "graph-key"
OTHER_NS = "graph-other-ns"
OTHER_KEY = "graph-other-key"
AGENT = "ga"
T = datetime(2026, 1, 1, tzinfo=timezone.utc)


@pytest_asyncio.fixture
async def client(db):
    for raw, ns in [(KEY, NS), (OTHER_KEY, OTHER_NS)]:
        db.add(ApiKey(hashed_key=hashlib.sha256(raw.encode()).hexdigest(),
                      namespace=ns, scopes=["read", "write", "admin"]))
    await db.commit()

    async def _override():
        yield db

    app.dependency_overrides[get_db] = _override
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
            yield ac
    finally:
        app.dependency_overrides.clear()


def _h(key=KEY):
    return {"X-API-Key": key}


async def _relate(client, src, rel, dst, key=KEY, exclusive=False):
    return await client.post("/v1/graph/relate", headers=_h(key), json={
        "agent_id": AGENT, "src_entity": src, "rel_type": rel, "dst_entity": dst,
        "event_time": T.isoformat(), "exclusive": exclusive,
    })


@pytest.mark.asyncio
async def test_relate_and_neighbors(client):
    r = await _relate(client, "FundA", "owns", "IssuerX")
    assert r.status_code == 200, r.text

    n = await client.get("/v1/graph/neighbors", headers=_h(),
                         params={"entity": "FundA", "agent_id": AGENT, "depth": 1})
    assert n.status_code == 200
    assert "IssuerX" in {x["entity"] for x in n.json()["neighbors"]}


@pytest.mark.asyncio
async def test_path_conflict_of_interest(client):
    await _relate(client, "Attorney", "represented", "ClientX")
    await _relate(client, "ClientX", "adverse_to", "PartyY")

    p = await client.get("/v1/graph/path", headers=_h(),
                        params={"src": "Attorney", "dst": "PartyY", "agent_id": AGENT})
    assert p.status_code == 200
    body = p.json()
    assert body["connected"] is True
    assert body["hops"] == 2


@pytest.mark.asyncio
async def test_unrelate(client):
    await _relate(client, "A", "r", "B")
    u = await client.post("/v1/graph/unrelate", headers=_h(), json={
        "agent_id": AGENT, "src_entity": "A", "rel_type": "r", "dst_entity": "B",
    })
    assert u.status_code == 200
    assert u.json()["invalidated"] == 1


@pytest.mark.asyncio
async def test_namespace_isolation(client):
    # Edge created in OTHER_NS must be invisible to NS.
    await _relate(client, "Secret", "links", "Hidden", key=OTHER_KEY)
    n = await client.get("/v1/graph/neighbors", headers=_h(KEY),
                        params={"entity": "Secret", "agent_id": AGENT})
    assert n.status_code == 200
    assert n.json()["neighbors"] == []


@pytest.mark.asyncio
async def test_relate_requires_write_scope(client, db):
    db.add(ApiKey(hashed_key=hashlib.sha256(b"ro-key").hexdigest(),
                  namespace=NS, scopes=["read"]))
    await db.commit()
    r = await _relate(client, "A", "r", "B", key="ro-key")
    assert r.status_code == 403
