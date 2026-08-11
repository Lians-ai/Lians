"""
SSO -> barrier mapping: an API key's barrier_group (chosen by the SSO gateway from
the caller's IdP group) scopes both writes (tagging) and reads (isolation).
"""
from __future__ import annotations

import hashlib
from types import SimpleNamespace
import pytest
import pytest_asyncio
from datetime import datetime, timezone

from httpx import AsyncClient, ASGITransport

from src.lians.main import app
from src.lians.db import get_db
from src.lians.models import ApiKey

NS = "sso-ns"
AGENT = "sso-agent"
T = datetime(2026, 1, 1, tzinfo=timezone.utc)


def _sha(k):
    return hashlib.sha256(k.encode()).hexdigest()


@pytest_asyncio.fixture
async def client(db):
    # Three keys in one namespace: two walled desks + one unbarriered (compliance).
    desk_scopes = ["read", "write", "compliance", "backtest", "graph", "webhooks"]
    db.add(ApiKey(hashed_key=_sha("kA"), namespace=NS, scopes=desk_scopes, barrier_group="deskA"))
    db.add(ApiKey(hashed_key=_sha("kB"), namespace=NS, scopes=desk_scopes, barrier_group="deskB"))
    db.add(ApiKey(hashed_key=_sha("kC"), namespace=NS, scopes=["read", "write", "admin"]))  # unbarriered
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


async def _add(client, key, content):
    r = await client.post("/v1/memories", headers=_h(key),
                          json={"agent_id": AGENT, "content": content, "event_time": T.isoformat()})
    assert r.status_code == 200, r.text
    return r.json()


async def _recall(client, key):
    r = await client.post("/v1/recall", headers=_h(key),
                          json={"agent_id": AGENT, "query": "trade idea NVDA", "k": 10})
    assert r.status_code == 200
    return [(m.get("content") or "") for m in r.json()["memories"]]


@pytest.mark.asyncio
async def test_write_tagged_with_key_barrier(client):
    out = await _add(client, "kA", "deskA trade idea NVDA long")
    assert out["barrier_group"] == "deskA"


@pytest.mark.asyncio
async def test_reads_isolated_by_barrier(client):
    await _add(client, "kA", "deskA trade idea NVDA long")
    await _add(client, "kB", "deskB trade idea NVDA short")

    a_sees = await _recall(client, "kA")
    assert any("deskA" in c for c in a_sees)
    assert not any("deskB" in c for c in a_sees)   # cannot cross the wall

    b_sees = await _recall(client, "kB")
    assert any("deskB" in c for c in b_sees)
    assert not any("deskA" in c for c in b_sees)


@pytest.mark.asyncio
async def test_unbarriered_key_sees_all(client):
    await _add(client, "kA", "deskA trade idea NVDA long")
    await _add(client, "kB", "deskB trade idea NVDA short")
    c_sees = await _recall(client, "kC")
    assert any("deskA" in c for c in c_sees) and any("deskB" in c for c in c_sees)


@pytest.mark.asyncio
async def test_batch_write_is_tagged_with_key_barrier(client):
    resp = await client.post(
        "/v1/memories/batch",
        headers=_h("kA"),
        json={"memories": [{
            "agent_id": AGENT,
            "content": "deskA batch-only secret",
            "event_time": T.isoformat(),
        }]},
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["memories"][0]["barrier_group"] == "deskA"


@pytest.mark.asyncio
async def test_snapshot_cannot_cross_barrier(client):
    await _add(client, "kA", "deskA snapshot secret")
    await _add(client, "kB", "deskB snapshot secret")

    resp = await client.get(
        "/v1/snapshot",
        headers=_h("kA"),
        params={"agent_id": AGENT, "as_of": "2026-02-01T00:00:00Z"},
    )
    assert resp.status_code == 200, resp.text
    contents = [(m.get("content") or "") for m in resp.json()["items"]]
    assert any("deskA" in c for c in contents)
    assert not any("deskB" in c for c in contents)


@pytest.mark.asyncio
async def test_lineage_cannot_cross_barrier(client):
    desk_b = await _add(client, "kB", "deskB lineage secret")
    resp = await client.get(
        f"/v1/memories/{desk_b['id']}/lineage",
        headers=_h("kA"),
    )
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_audit_reconstruction_cannot_cross_barrier(client):
    await _add(client, "kB", "deskB audit secret")
    resp = await client.get(
        "/v1/audit/reconstruct",
        headers=_h("kA"),
        params={
            "agent_id": AGENT,
            "as_of": "2026-02-01T00:00:00Z",
            "query": "deskB audit secret",
        },
    )
    assert resp.status_code == 200, resp.text
    contents = [(m.get("content") or "") for m in resp.json()["memories"]]
    assert not any("deskB" in c for c in contents)
    assert resp.json()["event_trail"] == []


@pytest.mark.asyncio
async def test_backtest_cannot_count_or_preview_other_barrier(client):
    resp = await client.post(
        "/v1/memories",
        headers=_h("kB"),
        json={
            "agent_id": AGENT,
            "content": "deskB future backtest secret",
            "event_time": "2026-03-01T00:00:00Z",
        },
    )
    assert resp.status_code == 200, resp.text

    report = await client.post(
        "/v1/backtest/check",
        headers=_h("kA"),
        json={"agent_id": AGENT, "simulation_as_of": "2026-02-01T00:00:00Z"},
    )
    assert report.status_code == 200, report.text
    assert report.json()["memories_checked"] == 0
    assert report.json()["flags"] == []


@pytest.mark.asyncio
async def test_graph_cannot_cross_barrier(client):
    created = await client.post(
        "/v1/graph/relate",
        headers=_h("kB"),
        json={
            "agent_id": AGENT,
            "src_entity": "DeskBClient",
            "rel_type": "owns",
            "dst_entity": "DeskBAsset",
            "event_time": T.isoformat(),
        },
    )
    assert created.status_code == 200, created.text

    resp = await client.get(
        "/v1/graph/neighbors",
        headers=_h("kA"),
        params={"agent_id": AGENT, "entity": "DeskBClient"},
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["neighbors"] == []
    assert resp.json()["direct_edges"] == []


@pytest.mark.asyncio
async def test_webhook_configuration_cannot_cross_barrier(client):
    created = await client.post(
        "/v1/webhooks",
        headers=_h("kB"),
        json={
            "url": "https://hooks.example.com/lians",
            "events": ["memory.conflict"],
            "secret": "desk-b-webhook-secret-1234",
        },
    )
    assert created.status_code == 201, created.text

    listed = await client.get("/v1/webhooks", headers=_h("kA"))
    assert listed.status_code == 200, listed.text
    assert listed.json() == []


@pytest.mark.asyncio
async def test_unbarriered_reviewer_preserves_held_items_barrier(
    client, monkeypatch
):
    monkeypatch.setattr(
        "src.lians.api.routes_memory.get_settings",
        lambda: SimpleNamespace(
            admission_mode="enforce",
            admission_blocked_sources="",
        ),
    )
    held = await client.post(
        "/v1/memories",
        headers=_h("kB"),
        json={
            "agent_id": AGENT,
            "content": "deskB client SSN 123-45-6789 under review",
            "event_time": T.isoformat(),
        },
    )
    assert held.status_code == 202, held.text

    approved = await client.post(
        f"/v1/admissions/{held.json()['pending_id']}/resolve",
        headers=_h("kC"),
        json={"action": "approve", "note": "approved by compliance"},
    )
    assert approved.status_code == 200, approved.text
    memory_id = approved.json()["memory_id"]

    denied = await client.get(
        f"/v1/memories/{memory_id}/lineage", headers=_h("kA")
    )
    allowed = await client.get(
        f"/v1/memories/{memory_id}/lineage", headers=_h("kB")
    )
    assert denied.status_code == 404
    assert allowed.status_code == 200, allowed.text
