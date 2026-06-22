"""
Memory lineage API tests.

Coverage:
  - Single memory: depth=1, no edges, is_current=True
  - Two-node chain: A superseded by B
  - Three-node chain: queried from root / middle / tip all return same chain
  - Edge metadata: relation, confidence, adjudication_stage present
  - Erased memory in chain: content=None, chain still intact
  - 404 for unknown memory ID
  - Namespace isolation: cannot query another namespace's memory
  - Root/tip identity: root_id and tip_id correct regardless of query position
"""
from __future__ import annotations

import hashlib
import pytest
import pytest_asyncio
from datetime import datetime, timezone
from uuid import uuid4

from httpx import AsyncClient, ASGITransport

from src.agentmem.main import app
from src.agentmem.db import get_db
from src.agentmem.models import ApiKey

T0 = datetime(2026, 1, 1, tzinfo=timezone.utc)
T1 = datetime(2026, 4, 1, tzinfo=timezone.utc)
T2 = datetime(2026, 7, 1, tzinfo=timezone.utc)

TEST_KEY = "lineage-test-key"
TEST_NS = "lineage-ns"
AGENT = "lineage-agent"

OTHER_KEY = "lineage-other-key"
OTHER_NS = "lineage-other-ns"


# ── Fixtures ──────────────────────────────────────────────────────────────────

@pytest_asyncio.fixture
async def client(db):
    for raw_key, ns in [(TEST_KEY, TEST_NS), (OTHER_KEY, OTHER_NS)]:
        hashed = hashlib.sha256(raw_key.encode()).hexdigest()
        db.add(ApiKey(
            hashed_key=hashed, namespace=ns, scopes=["read", "write", "admin"]
        ))
    await db.commit()

    async def _override():
        yield db

    app.dependency_overrides[get_db] = _override
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
            yield ac
    finally:
        app.dependency_overrides.clear()


def _h(key: str = TEST_KEY) -> dict:
    return {"X-API-Key": key}


def _mem(content: str, event_time: datetime, *, ticker: str | None = None) -> dict:
    meta = {}
    if ticker:
        meta = {"ticker": ticker, "metric": "eps"}
    return {
        "agent_id": AGENT,
        "content": content,
        "event_time": event_time.isoformat(),
        "metadata": meta,
        "source": "test",
    }


async def _add(client, body, key=TEST_KEY) -> dict:
    r = await client.post("/v1/memories", headers=_h(key), json=body)
    assert r.status_code == 200, r.text
    return r.json()


async def _lineage(client, memory_id: str, key=TEST_KEY) -> dict:
    r = await client.get(f"/v1/memories/{memory_id}/lineage", headers=_h(key))
    return r


# ── Single memory ─────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_single_memory_lineage(client):
    mem = await _add(client, _mem("NVDA Q1 EPS $6.20", T0))
    r = await _lineage(client, mem["id"])
    assert r.status_code == 200
    data = r.json()

    assert data["depth"] == 1
    assert data["root_id"] == mem["id"]
    assert data["tip_id"] == mem["id"]
    assert data["queried_id"] == mem["id"]
    assert len(data["nodes"]) == 1
    assert len(data["edges"]) == 0

    node = data["nodes"][0]
    assert node["id"] == mem["id"]
    assert node["is_current"] is True
    assert node["content"] == "NVDA Q1 EPS $6.20"
    assert node["erased_at"] is None


# ── Two-node chain ────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_two_node_chain_from_root(client):
    a = await _add(client, _mem("NVDA Q1 EPS $6.20", T0, ticker="NVDA"))
    b = await _add(client, _mem("NVDA Q1 EPS $6.45", T1, ticker="NVDA"))

    r = await _lineage(client, a["id"])
    assert r.status_code == 200
    data = r.json()

    assert data["depth"] == 2
    assert data["root_id"] == a["id"]
    assert data["tip_id"] == b["id"]
    assert len(data["nodes"]) == 2
    assert len(data["edges"]) == 1

    assert data["nodes"][0]["id"] == a["id"]
    assert data["nodes"][0]["is_current"] is False
    assert data["nodes"][1]["id"] == b["id"]
    assert data["nodes"][1]["is_current"] is True


@pytest.mark.asyncio
async def test_two_node_chain_from_tip(client):
    a = await _add(client, _mem("NVDA Q1 EPS $6.20", T0, ticker="NVDA"))
    b = await _add(client, _mem("NVDA Q1 EPS $6.45", T1, ticker="NVDA"))

    # Querying from the tip should return the same chain
    r = await _lineage(client, b["id"])
    assert r.status_code == 200
    data = r.json()

    assert data["depth"] == 2
    assert data["root_id"] == a["id"]
    assert data["tip_id"] == b["id"]
    assert data["queried_id"] == b["id"]


# ── Three-node chain ──────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_three_node_chain_root_tip_depth(client):
    a = await _add(client, _mem("AAPL EPS $1.50", T0, ticker="AAPL"))
    b = await _add(client, _mem("AAPL EPS $1.55", T1, ticker="AAPL"))
    c = await _add(client, _mem("AAPL EPS $1.62", T2, ticker="AAPL"))

    r = await _lineage(client, a["id"])
    data = r.json()
    assert data["depth"] == 3
    assert data["root_id"] == a["id"]
    assert data["tip_id"] == c["id"]

    ids = [n["id"] for n in data["nodes"]]
    assert ids == [a["id"], b["id"], c["id"]]


@pytest.mark.asyncio
async def test_three_node_chain_queried_from_middle(client):
    a = await _add(client, _mem("AAPL EPS $1.50", T0, ticker="AAPL"))
    b = await _add(client, _mem("AAPL EPS $1.55", T1, ticker="AAPL"))
    c = await _add(client, _mem("AAPL EPS $1.62", T2, ticker="AAPL"))

    r = await _lineage(client, b["id"])
    data = r.json()

    # Chain is identical regardless of query position
    assert data["depth"] == 3
    assert data["root_id"] == a["id"]
    assert data["tip_id"] == c["id"]
    assert data["queried_id"] == b["id"]

    ids = [n["id"] for n in data["nodes"]]
    assert ids == [a["id"], b["id"], c["id"]]


@pytest.mark.asyncio
async def test_three_node_chain_queried_from_tip(client):
    a = await _add(client, _mem("AAPL EPS $1.50", T0, ticker="AAPL"))
    b = await _add(client, _mem("AAPL EPS $1.55", T1, ticker="AAPL"))
    c = await _add(client, _mem("AAPL EPS $1.62", T2, ticker="AAPL"))

    r = await _lineage(client, c["id"])
    data = r.json()

    assert data["depth"] == 3
    assert data["root_id"] == a["id"]
    assert data["tip_id"] == c["id"]


# ── Edge metadata ─────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_edge_has_required_fields(client):
    a = await _add(client, _mem("MSFT revenue $60B", T0, ticker="MSFT"))
    await _add(client, _mem("MSFT revenue $65B", T1, ticker="MSFT"))

    r = await _lineage(client, a["id"])
    data = r.json()
    assert len(data["edges"]) == 1

    edge = data["edges"][0]
    assert edge["from_id"] == a["id"]
    assert "to_id" in edge
    assert "relation" in edge
    assert "confidence" in edge
    assert "adjudication_stage" in edge
    assert "superseded_at" in edge
    assert isinstance(edge["confidence"], float)
    assert 0.0 <= edge["confidence"] <= 1.0


@pytest.mark.asyncio
async def test_edge_relation_is_supersedes_for_keyed_update(client):
    a = await _add(client, _mem("JPM EPS $4.20", T0, ticker="JPM"))
    await _add(client, _mem("JPM EPS $4.55", T1, ticker="JPM"))

    r = await _lineage(client, a["id"])
    edge = r.json()["edges"][0]
    assert edge["relation"] == "SUPERSEDES"


@pytest.mark.asyncio
async def test_three_node_chain_has_two_edges(client):
    a = await _add(client, _mem("GS revenue $12B", T0, ticker="GS"))
    b = await _add(client, _mem("GS revenue $13B", T1, ticker="GS"))
    c = await _add(client, _mem("GS revenue $14B", T2, ticker="GS"))

    r = await _lineage(client, b["id"])
    data = r.json()

    assert len(data["edges"]) == 2
    assert data["edges"][0]["from_id"] == a["id"]
    assert data["edges"][0]["to_id"] == b["id"]
    assert data["edges"][1]["from_id"] == b["id"]
    assert data["edges"][1]["to_id"] == c["id"]


# ── Content and node fields ───────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_node_content_is_present(client):
    a = await _add(client, _mem("TSLA deliveries 400k", T0, ticker="TSLA"))
    r = await _lineage(client, a["id"])
    node = r.json()["nodes"][0]
    assert node["content"] == "TSLA deliveries 400k"
    assert node["content_hash"] is not None
    assert node["source"] == "test"


@pytest.mark.asyncio
async def test_node_metadata_present(client):
    a = await _add(client, _mem("META DAU 3.2B", T0, ticker="META"))
    await _add(client, _mem("META DAU 3.3B", T1, ticker="META"))

    r = await _lineage(client, a["id"])
    data = r.json()
    # All nodes should have metadata populated
    for node in data["nodes"]:
        assert isinstance(node["metadata"], dict)


@pytest.mark.asyncio
async def test_superseded_node_has_valid_to_set(client):
    a = await _add(client, _mem("AMZN revenue $150B", T0, ticker="AMZN"))
    await _add(client, _mem("AMZN revenue $155B", T1, ticker="AMZN"))

    r = await _lineage(client, a["id"])
    nodes = r.json()["nodes"]

    # Root node must have valid_to set (it was superseded)
    assert nodes[0]["valid_to"] is not None
    # Tip node must have valid_to=None (still live)
    assert nodes[1]["valid_to"] is None


# ── Erased memory in chain ────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_erased_node_content_is_none(client):
    """An erased memory in the chain returns content=None but stays in the chain."""
    a = await _add(client, _mem("Client X portfolio $500k", T0), )
    # Add with subject_id so we can erase it
    r = await client.post("/v1/memories", headers=_h(), json={
        **_mem("Client X portfolio $600k", T1),
        "subject_id": "client-x-erase",
    })
    assert r.status_code == 200

    # Erase the subject (crypto-shred)
    er = await client.post("/v1/erase", headers=_h(), json={
        "subject_id": "client-x-erase",
        "request_ref": "GDPR-TEST",
    })
    assert er.status_code == 200

    # Chain for the erased memory should still be accessible,
    # but erased_at should be set and content should be None
    erased_id = r.json()["id"]
    lin = await _lineage(client, erased_id)
    assert lin.status_code == 200
    data = lin.json()

    erased_node = next(n for n in data["nodes"] if n["id"] == erased_id)
    assert erased_node["content"] is None
    assert erased_node["erased_at"] is not None


# ── Error cases ───────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_unknown_memory_id_returns_404(client):
    r = await _lineage(client, str(uuid4()))
    assert r.status_code == 404


@pytest.mark.asyncio
async def test_namespace_isolation(client):
    """A memory in OTHER_NS must not be reachable from TEST_NS credentials."""
    mem = await _add(client, _mem("Private data", T0), key=OTHER_KEY)
    r = await _lineage(client, mem["id"], key=TEST_KEY)
    # The memory exists but belongs to a different namespace
    assert r.status_code == 404


# ── Agent isolation ───────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_different_agents_do_not_merge_chains(client):
    """Memories from different agents must not be linked even if similar content."""
    body_a = {**_mem("FED rate 5.25%", T0, ticker=None), "agent_id": "agent-alpha"}
    body_b = {**_mem("FED rate 5.25%", T1, ticker=None), "agent_id": "agent-beta"}

    ma = await _add(client, body_a)
    mb = await _add(client, body_b)

    # Each should form an independent single-node chain
    ra = await _lineage(client, ma["id"])
    rb = await _lineage(client, mb["id"])

    assert ra.json()["depth"] == 1
    assert rb.json()["depth"] == 1
    assert ra.json()["tip_id"] == ma["id"]
    assert rb.json()["tip_id"] == mb["id"]


# ── Response structure ────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_lineage_response_has_all_top_level_fields(client):
    mem = await _add(client, _mem("BRK EPS $10.00", T0))
    data = (await _lineage(client, mem["id"])).json()

    for field in ("agent_id", "namespace", "queried_id", "root_id", "tip_id", "depth", "nodes", "edges"):
        assert field in data, f"Missing field: {field}"

    assert data["agent_id"] == AGENT
    assert data["namespace"] == TEST_NS
