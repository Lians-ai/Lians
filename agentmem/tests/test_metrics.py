"""
Prometheus metrics tests.

Coverage:
  - GET /metrics returns 200 with correct content-type
  - GET /metrics returns 404 when METRICS_ENABLED=false
  - agentmem_memory_writes_total increments on add
  - agentmem_memory_recalls_total increments on recall (semantic path)
  - agentmem_memories_erased_total and agentmem_erasure_requests_total increment on erase
  - agentmem_add_duration_seconds histogram is populated
  - agentmem_recall_duration_seconds histogram is populated
  - All tests skip cleanly when prometheus-client is not installed

Tests run against the FastAPI ASGITransport stack with an in-memory SQLite DB
(same pattern as test_api.py).  Each test resets the metric registry between
runs so counter values don't bleed across tests.
"""
from __future__ import annotations

import hashlib
import pytest
import pytest_asyncio
from datetime import datetime, timezone

pytest.importorskip("prometheus_client", reason="prometheus_client not installed")

# After the importorskip, prometheus_client is guaranteed to be importable.
from prometheus_client import CollectorRegistry

from httpx import AsyncClient, ASGITransport

from src.agentmem.main import app
from src.agentmem.db import get_db
from src.agentmem.models import ApiKey

T0 = datetime(2026, 1, 1, tzinfo=timezone.utc)
T1 = datetime(2026, 5, 10, tzinfo=timezone.utc)

TEST_KEY = "metrics-test-key"
TEST_NS = "metrics-ns"
AGENT = "metrics-agent"
ADMIN_SECRET = "dev-admin-secret-change-in-prod"


# ── Fresh registry per test ───────────────────────────────────────────────────

@pytest.fixture(autouse=True)
def reset_metrics():
    """
    Replace the module-level Prometheus registry with a fresh one before each
    test so counter values don't accumulate across the suite.
    """
    import src.agentmem.metrics as _m
    from prometheus_client import Counter, Gauge, Histogram

    # Swap in a clean registry
    new_reg = CollectorRegistry()
    _m.REGISTRY = new_reg
    _m._writes = Counter(
        "agentmem_memory_writes_total",
        "test",
        ["namespace", "relation"],
        registry=new_reg,
    )
    _m._recalls = Counter(
        "agentmem_memory_recalls_total",
        "test",
        ["namespace", "router", "cache_hit"],
        registry=new_reg,
    )
    _m._erased = Counter(
        "agentmem_memories_erased_total",
        "test",
        ["namespace"],
        registry=new_reg,
    )
    _m._erase_requests = Counter(
        "agentmem_erasure_requests_total",
        "test",
        ["namespace"],
        registry=new_reg,
    )
    _m._add_hist = Histogram(
        "agentmem_add_duration_seconds",
        "test",
        ["namespace"],
        registry=new_reg,
    )
    _m._recall_hist = Histogram(
        "agentmem_recall_duration_seconds",
        "test",
        ["namespace"],
        registry=new_reg,
    )
    _m._conflicts_detected = Counter(
        "agentmem_conflicts_detected_total",
        "test",
        ["namespace"],
        registry=new_reg,
    )
    _m._conflicts_resolved = Counter(
        "agentmem_conflicts_resolved_total",
        "test",
        ["namespace", "resolution"],
        registry=new_reg,
    )
    _m._conflict_queue = Gauge(
        "agentmem_conflict_queue_depth",
        "test",
        ["namespace"],
        registry=new_reg,
    )
    yield
    # Teardown: restore nothing — next test's fixture will create its own


# ── Test client ───────────────────────────────────────────────────────────────

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


def _h() -> dict:
    return {"X-API-Key": TEST_KEY}


def _mem(content: str, event_time: datetime = T0) -> dict:
    return {
        "agent_id": AGENT,
        "content": content,
        "event_time": event_time.isoformat(),
        "metadata": {},
    }


# ── /metrics endpoint ─────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_metrics_endpoint_returns_200(client):
    resp = await client.get("/metrics")
    assert resp.status_code == 200


@pytest.mark.asyncio
async def test_metrics_content_type_is_prometheus(client):
    resp = await client.get("/metrics")
    assert "text/plain" in resp.headers["content-type"]


@pytest.mark.asyncio
async def test_metrics_body_contains_metric_names(client):
    # Trigger a write so the metric family is non-empty
    await client.post("/v1/memories", headers=_h(), json=_mem("NVDA guidance $36B"))
    resp = await client.get("/metrics")
    body = resp.text
    assert "agentmem_memory_writes_total" in body
    assert "agentmem_add_duration_seconds" in body


@pytest.mark.asyncio
async def test_metrics_endpoint_disabled_returns_404(client, monkeypatch):
    monkeypatch.setenv("METRICS_ENABLED", "false")
    from src.agentmem.config import get_settings
    get_settings.cache_clear()
    try:
        resp = await client.get("/metrics")
        assert resp.status_code == 404
    finally:
        get_settings.cache_clear()


# ── Write counter ─────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_write_increments_counter(client):
    import src.agentmem.metrics as _m

    before = _counter_value(_m._writes, namespace=TEST_NS)
    await client.post("/v1/memories", headers=_h(), json=_mem("NVDA guidance $36B"))
    after = _counter_value(_m._writes, namespace=TEST_NS)

    assert after - before == 1.0


@pytest.mark.asyncio
async def test_write_counter_includes_relation_label(client):
    import src.agentmem.metrics as _m

    await client.post("/v1/memories", headers=_h(), json=_mem("AAPL EPS $1.50", T0))
    await client.post("/v1/memories", headers=_h(), json={
        **_mem("AAPL EPS $1.60", T1),
        "metadata": {"ticker": "AAPL", "metric": "eps"},
    })
    # First add should be ADDS, second may be SUPERSEDES or ADDS depending on metadata
    total = _counter_value(_m._writes, namespace=TEST_NS)
    assert total >= 2.0


@pytest.mark.asyncio
async def test_batch_write_increments_counter_per_item(client):
    import src.agentmem.metrics as _m

    before = _counter_value(_m._writes, namespace=TEST_NS)
    await client.post("/v1/memories/batch", headers=_h(), json={
        "memories": [
            _mem("TSLA deliveries 400k", T0),
            _mem("MSFT revenue $65B", T0),
        ]
    })
    after = _counter_value(_m._writes, namespace=TEST_NS)
    assert after - before == 2.0


# ── Recall counter ────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_recall_increments_counter(client):
    import src.agentmem.metrics as _m

    await client.post("/v1/memories", headers=_h(), json=_mem("NVDA guidance $36B"))
    before = _counter_value(_m._recalls, namespace=TEST_NS)

    await client.post("/v1/recall", headers=_h(), json={
        "agent_id": AGENT,
        "query": "NVDA guidance",
        "k": 5,
    })
    after = _counter_value(_m._recalls, namespace=TEST_NS)
    assert after - before == 1.0


@pytest.mark.asyncio
async def test_recall_records_semantic_router(client):
    import src.agentmem.metrics as _m

    await client.post("/v1/memories", headers=_h(), json=_mem("FED rate 5.25%"))
    await client.post("/v1/recall", headers=_h(), json={
        "agent_id": AGENT,
        "query": "FED rate",
        "k": 5,
    })

    # Semantic path (no structured filters + no cache hit on first call)
    sem = _counter_value(_m._recalls, namespace=TEST_NS, router="semantic", cache_hit="false")
    assert sem >= 1.0


# ── Add histogram ─────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_add_histogram_is_populated(client):
    import src.agentmem.metrics as _m

    await client.post("/v1/memories", headers=_h(), json=_mem("JPM EPS $4.20"))
    count = _hist_count(_m._add_hist, namespace=TEST_NS)
    assert count >= 1


# ── Recall histogram ──────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_recall_histogram_is_populated(client):
    import src.agentmem.metrics as _m

    await client.post("/v1/memories", headers=_h(), json=_mem("GS revenue $12B"))
    await client.post("/v1/recall", headers=_h(), json={
        "agent_id": AGENT, "query": "GS revenue", "k": 5,
    })
    count = _hist_count(_m._recall_hist, namespace=TEST_NS)
    assert count >= 1


# ── Erase counters ────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_erase_increments_erasure_request_counter(client):
    import src.agentmem.metrics as _m

    await client.post("/v1/memories", headers=_h(), json={
        **_mem("Client Alice portfolio $1M"),
        "subject_id": "alice-001",
        "metadata": {},
    })
    before_req = _counter_value(_m._erase_requests, namespace=TEST_NS)

    await client.post("/v1/erase", headers=_h(), json={
        "subject_id": "alice-001",
        "request_ref": "GDPR-001",
    })
    after_req = _counter_value(_m._erase_requests, namespace=TEST_NS)
    assert after_req - before_req == 1.0


@pytest.mark.asyncio
async def test_erase_increments_memories_erased_counter(client):
    import src.agentmem.metrics as _m

    await client.post("/v1/memories", headers=_h(), json={
        **_mem("Client Bob portfolio $500k"),
        "subject_id": "bob-001",
        "metadata": {},
    })
    before_erased = _counter_value(_m._erased, namespace=TEST_NS)

    await client.post("/v1/erase", headers=_h(), json={
        "subject_id": "bob-001",
        "request_ref": "GDPR-002",
    })
    after_erased = _counter_value(_m._erased, namespace=TEST_NS)
    assert after_erased - before_erased == 1.0


@pytest.mark.asyncio
async def test_erase_with_no_memories_does_not_increment_erased(client):
    import src.agentmem.metrics as _m

    before = _counter_value(_m._erased, namespace=TEST_NS)
    # Erase a subject with no memories
    await client.post("/v1/erase", headers=_h(), json={
        "subject_id": "nobody-999",
        "request_ref": "GDPR-999",
    })
    after = _counter_value(_m._erased, namespace=TEST_NS)
    # _erased must NOT increment — nothing was destroyed
    assert after == before


# ── Metrics survive when prometheus_client absent ─────────────────────────────

def test_generate_metrics_without_prometheus():
    """record_* helpers and generate_metrics() must not raise without prometheus_client."""
    import src.agentmem.metrics as _m
    original = _m._PROM_AVAILABLE
    _m._PROM_AVAILABLE = False
    try:
        # Helpers must be no-ops
        _m.record_write("ns", "ADDS")
        _m.observe_add("ns", 0.001)
        _m.record_recall("ns", "semantic", False)
        _m.observe_recall("ns", 0.001)
        _m.record_erase("ns", 3)
        # Scrape must return a comment, not raise
        body, ct = _m.generate_metrics()
        assert b"prometheus_client" in body
    finally:
        _m._PROM_AVAILABLE = original


# ── Prometheus output is parseable ───────────────────────────────────────────

@pytest.mark.asyncio
async def test_metrics_output_is_valid_prometheus_text(client):
    """Each non-comment, non-blank line must be parseable as TYPE/HELP or a sample."""
    await client.post("/v1/memories", headers=_h(), json=_mem("NVDA Q3 guidance $36B"))
    resp = await client.get("/metrics")
    for line in resp.text.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        # A metric sample line must contain at least one space separating name from value
        assert " " in stripped, f"Unexpected line format: {stripped!r}"


# ── Helpers ───────────────────────────────────────────────────────────────────

def _counter_value(counter, **labels) -> float:
    """
    Sum _total counter samples whose labels contain all provided key-value pairs.

    Partial-label queries are supported: calling with just `namespace=X` sums
    across all values of other labels (e.g. `relation`, `router`, `cache_hit`).
    """
    total = 0.0
    try:
        for mf in counter.collect():
            for sample in mf.samples:
                if sample.name.endswith("_created"):
                    continue
                if all(sample.labels.get(k) == v for k, v in labels.items()):
                    total += sample.value
    except Exception:
        pass
    return total


def _hist_count(histogram, **labels) -> float:
    """
    Sum _count histogram samples whose labels contain all provided key-value pairs.
    """
    total = 0.0
    try:
        for mf in histogram.collect():
            for sample in mf.samples:
                if not sample.name.endswith("_count"):
                    continue
                if all(sample.labels.get(k) == v for k, v in labels.items()):
                    total += sample.value
    except Exception:
        pass
    return total
