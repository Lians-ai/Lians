"""
AgentMem Python SDK — unit tests.

All tests use respx to mock httpx so no real API is needed.
Validates:
  1. Correct HTTP method, path, and JSON body for each method.
  2. Timestamps serialised to ISO-8601 strings.
  3. LianError raised with status + body on non-2xx.
  4. Admin endpoints include X-Admin-Secret header.
  5. Query parameters serialised correctly for GET requests.
  6. Response models parsed correctly via Pydantic.
"""
import hashlib
import hmac
import json
from datetime import datetime, timezone

import pytest
import respx
import httpx

from lian import LianClient, LianError, verify_webhook_signature, parse_webhook_payload
from lian.types import MemoryOut, RecallResult, ContaminationReport, KnowledgeSnapshot

BASE = "https://mem.test"
KEY = "test-api-key"
ADMIN = "test-admin-secret"

MEMORY_FIXTURE = {
    "id": "00000000-0000-0000-0000-000000000001",
    "namespace": "test-ns",
    "agent_id": "agent-1",
    "content": "AAPL Q1 EPS: $1.52",
    "subject_id": None,
    "event_time": "2026-01-28T00:00:00Z",
    "ingestion_time": "2026-01-28T00:00:01Z",
    "valid_from": "2026-01-28T00:00:00Z",
    "valid_to": None,
    "superseded_by": None,
    "supersession_confidence": None,
    "barrier_group": None,
    "importance": 0.5,
    "source": None,
    "content_hash": "abc123",
    "erased_at": None,
    "metadata": {"ticker": "AAPL", "metric": "eps"},
}


@pytest.fixture
def client():
    return LianClient(BASE, KEY, admin_secret=ADMIN, http2=False)


# ── add_memory ────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_add_memory_post(client):
    with respx.mock(base_url=BASE) as mock:
        route = mock.post("/v1/memories").mock(return_value=httpx.Response(200, json=MEMORY_FIXTURE))
        mem = await client.add_memory(
            agent_id="agent-1",
            content="AAPL Q1 EPS: $1.52",
            event_time="2026-01-28T00:00:00Z",
            metadata={"ticker": "AAPL", "metric": "eps"},
        )
        assert route.called
        assert mem.agent_id == "agent-1"
        assert mem.content == "AAPL Q1 EPS: $1.52"
        assert isinstance(mem, MemoryOut)


@pytest.mark.asyncio
async def test_add_memory_datetime_serialised(client):
    with respx.mock(base_url=BASE) as mock:
        route = mock.post("/v1/memories").mock(return_value=httpx.Response(200, json=MEMORY_FIXTURE))
        dt = datetime(2026, 1, 28, tzinfo=timezone.utc)
        await client.add_memory(agent_id="a", content="c", event_time=dt)
        body = json.loads(route.calls[0].request.content)
        assert "2026-01-28" in body["event_time"]


# ── recall ────────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_recall_post(client):
    payload = {"memories": [MEMORY_FIXTURE], "as_of": None, "total_candidates": 1}
    with respx.mock(base_url=BASE) as mock:
        route = mock.post("/v1/recall").mock(return_value=httpx.Response(200, json=payload))
        result = await client.recall(agent_id="agent-1", query="AAPL earnings", k=5)
        assert route.called
        assert isinstance(result, RecallResult)
        assert len(result.memories) == 1


@pytest.mark.asyncio
async def test_recall_as_of_included(client):
    payload = {"memories": [], "as_of": "2026-03-01T00:00:00Z", "total_candidates": 0}
    with respx.mock(base_url=BASE) as mock:
        route = mock.post("/v1/recall").mock(return_value=httpx.Response(200, json=payload))
        await client.recall(agent_id="a", query="q", as_of="2026-03-01T00:00:00Z")
        body = json.loads(route.calls[0].request.content)
        assert "as_of" in body


# ── erase_subject ─────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_erase_subject_post(client):
    payload = {"subject_id": "sub-1", "memories_erased": 3, "request_ref": "GDPR-001"}
    with respx.mock(base_url=BASE) as mock:
        route = mock.post("/v1/erase").mock(return_value=httpx.Response(200, json=payload))
        result = await client.erase_subject("sub-1", "GDPR-001")
        assert route.called
        assert result.memories_erased == 3


# ── knowledge_snapshot ────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_knowledge_snapshot_get(client):
    payload = {
        "agent_id": "agent-1", "namespace": "test-ns",
        "as_of": "2026-03-01T00:00:00Z", "total": 2,
        "items": [MEMORY_FIXTURE],
    }
    with respx.mock(base_url=BASE) as mock:
        route = mock.get("/v1/snapshot").mock(return_value=httpx.Response(200, json=payload))
        snap = await client.knowledge_snapshot("agent-1", "2026-03-01T00:00:00Z")
        assert route.called
        assert isinstance(snap, KnowledgeSnapshot)
        assert snap.total == 2


@pytest.mark.asyncio
async def test_knowledge_snapshot_datetime_param(client):
    payload = {"agent_id": "a", "namespace": "n", "as_of": "2026-03-01T00:00:00+00:00", "total": 0, "items": []}
    with respx.mock(base_url=BASE) as mock:
        route = mock.get("/v1/snapshot").mock(return_value=httpx.Response(200, json=payload))
        dt = datetime(2026, 3, 1, tzinfo=timezone.utc)
        await client.knowledge_snapshot("agent-1", dt)
        url = str(route.calls[0].request.url)
        assert "as_of" in url


# ── backtest_check ────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_backtest_check_post(client):
    payload = {
        "agent_id": "agent-1", "namespace": "test-ns",
        "simulation_as_of": "2026-01-01T00:00:00Z",
        "memories_checked": 5, "flags": [], "contamination_rate": 0.0, "is_clean": True,
    }
    with respx.mock(base_url=BASE) as mock:
        route = mock.post("/v1/backtest/check").mock(return_value=httpx.Response(200, json=payload))
        report = await client.backtest_check("agent-1", "2026-01-01T00:00:00Z")
        assert route.called
        assert isinstance(report, ContaminationReport)
        assert report.is_clean is True


@pytest.mark.asyncio
async def test_backtest_check_contaminated(client):
    payload = {
        "agent_id": "agent-1", "namespace": "test-ns",
        "simulation_as_of": "2026-01-01T00:00:00Z",
        "memories_checked": 3,
        "flags": [{
            "memory_id": "00000000-0000-0000-0000-000000000002",
            "event_time": "2026-06-01T00:00:00Z",
            "ingestion_time": "2026-06-01T00:00:00Z",
            "contamination_type": "future_event",
            "delta_days": 151.0,
            "content_preview": "Future fact",
            "source": None,
            "metadata": {},
        }],
        "contamination_rate": 0.333,
        "is_clean": False,
    }
    with respx.mock(base_url=BASE) as mock:
        mock.post("/v1/backtest/check").mock(return_value=httpx.Response(200, json=payload))
        report = await client.backtest_check("agent-1", "2026-01-01T00:00:00Z")
        assert report.is_clean is False
        assert len(report.flags) == 1
        assert report.flags[0].contamination_type == "future_event"


# ── fact_history ──────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_fact_history_get(client):
    payload = {
        "ticker": "AAPL", "metric": "eps", "agent_id": "agent-1",
        "namespace": "test-ns", "total": 1, "items": [MEMORY_FIXTURE],
    }
    with respx.mock(base_url=BASE) as mock:
        route = mock.get("/v1/facts/history").mock(return_value=httpx.Response(200, json=payload))
        result = await client.fact_history("agent-1", "AAPL", "eps")
        assert route.called
        assert result.ticker == "AAPL"
        assert result.total == 1


# ── admin endpoints include X-Admin-Secret ────────────────────────────────────

@pytest.mark.asyncio
async def test_audit_export_sends_admin_secret(client):
    payload = {
        "namespace": "test-ns", "from_": None, "to": None,
        "total_rows": 0, "chain_status": "ok", "chain_violations": None, "events": [],
    }
    with respx.mock(base_url=BASE) as mock:
        route = mock.get("/v1/admin/audit/export").mock(return_value=httpx.Response(200, json=payload))
        await client.audit_export(namespace="test-ns")
        assert route.calls[0].request.headers.get("x-admin-secret") == ADMIN


@pytest.mark.asyncio
async def test_verify_chain_sends_admin_secret(client):
    with respx.mock(base_url=BASE) as mock:
        route = mock.get("/v1/admin/audit/verify").mock(
            return_value=httpx.Response(200, json={"status": "ok", "rows_checked": 100})
        )
        await client.verify_chain("test-ns")
        assert route.calls[0].request.headers.get("x-admin-secret") == ADMIN


# ── LianError ─────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_error_on_4xx(client):
    with respx.mock(base_url=BASE) as mock:
        mock.post("/v1/memories").mock(return_value=httpx.Response(401, text="Unauthorized"))
        with pytest.raises(LianError) as exc_info:
            await client.add_memory(agent_id="a", content="c", event_time="2026-01-01T00:00:00Z")
        assert exc_info.value.status == 401
        assert "Unauthorized" in exc_info.value.body


@pytest.mark.asyncio
async def test_error_on_500(client):
    with respx.mock(base_url=BASE) as mock:
        mock.post("/v1/recall").mock(return_value=httpx.Response(500, text="Internal Server Error"))
        with pytest.raises(LianError) as exc_info:
            await client.recall(agent_id="a", query="q")
        assert exc_info.value.status == 500


# ── Webhook signature verification ───────────────────────────────────────────

def test_verify_valid_signature():
    secret = "test-webhook-secret"
    body = b'{"event": "memory.superseded"}'
    sig = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    header = f"sha256={sig}"
    assert verify_webhook_signature(body, header, secret) is True


def test_verify_invalid_signature():
    assert verify_webhook_signature(b"body", "sha256=wrong", "secret") is False


def test_verify_missing_prefix():
    assert verify_webhook_signature(b"body", "not-sha256=abc", "secret") is False


def test_parse_webhook_payload_valid():
    secret = "my-secret"
    data = {"event": "memory.erased", "namespace": "ns"}
    body = json.dumps(data).encode()
    sig = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    parsed = parse_webhook_payload(body, f"sha256={sig}", secret)
    assert parsed["event"] == "memory.erased"


def test_parse_webhook_payload_bad_sig():
    with pytest.raises(ValueError, match="signature verification failed"):
        parse_webhook_payload(b'{"x":1}', "sha256=bad", "secret")


# ── Context manager ───────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_context_manager():
    async with LianClient(BASE, KEY, http2=False) as client:
        with respx.mock(base_url=BASE) as mock:
            mock.post("/v1/recall").mock(
                return_value=httpx.Response(200, json={"memories": [], "as_of": None, "total_candidates": 0})
            )
            result = await client.recall(agent_id="a", query="q")
            assert result.memories == []
