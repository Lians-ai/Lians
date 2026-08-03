"""
AgentMem Python SDK — unit tests.

All tests use respx to mock httpx so no real API is needed.
Validates:
  1. Correct HTTP method, path, and JSON body for each method.
  2. Timestamps serialised to ISO-8601 strings.
  3. LiansError raised with status + body on non-2xx.
  4. Admin endpoints include X-Admin-Secret header.
  5. Query parameters serialised correctly for GET requests.
  6. Response models parsed correctly via Pydantic.
"""
import hashlib
import hmac
import json
from datetime import datetime, timezone

import httpx
import pytest
import respx
from lians import LiansClient, LiansError, parse_webhook_payload, verify_webhook_signature
from lians.types import ContaminationReport, KnowledgeSnapshot, MemoryOut, RecallResult

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
    return LiansClient(BASE, KEY, admin_secret=ADMIN, http2=False)


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
    payload = {
        "job_id": "00000000-0000-0000-0000-000000000901",
        "namespace": "test-ns",
        "subject_ref": "lians:subject:v2:hmac-sha256:key:opaque",
        "request_ref": "lians:erasure-request:v1:hmac-sha256:opaque",
        "status": "pending",
        "phase": "memories",
        "key_destroyed_at": "2026-08-02T12:00:00Z",
        "cache_fenced_at": "2026-08-02T12:00:00Z",
        "snapshot": {
            "memories": 3,
            "live_facts": 0,
            "relationships": 0,
            "pending_admissions": 0,
            "total_rows": 3,
        },
        "progress": {
            "memories": 0,
            "live_facts": 0,
            "relationships": 0,
            "pending_admissions": 0,
            "rows_scrubbed": 0,
            "pages_completed": 0,
            "ratio": 0.0,
        },
        "processing_attempts": 0,
        "next_attempt_at": "2026-08-02T12:00:00Z",
        "last_error_code": None,
        "last_error_digest": None,
        "failure_code": None,
        "created_at": "2026-08-02T12:00:00Z",
        "started_at": None,
        "updated_at": "2026-08-02T12:00:00Z",
        "completed_at": None,
        "replayed": False,
    }
    with respx.mock(base_url=BASE) as mock:
        route = mock.post("/v1/erase").mock(return_value=httpx.Response(200, json=payload))
        result = await client.erase_subject("sub-1", "GDPR-001")
        assert route.called
        assert result.snapshot.memories == 3
        assert result.status == "pending"
        assert "subject_id" not in result.model_dump()


# ── knowledge_snapshot ────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_knowledge_snapshot_get(client):
    payload = {
        "agent_id": "agent-1", "namespace": "test-ns",
        "as_of": "2026-03-01T00:00:00Z",
        "recorded_as_of": "2026-03-02T00:00:00Z", "total": 2,
        "returned": 1, "complete": False, "has_more": True,
        "next_event_time": MEMORY_FIXTURE["event_time"],
        "next_id": MEMORY_FIXTURE["id"],
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
    payload = {
        "agent_id": "a", "namespace": "n",
        "as_of": "2026-03-01T00:00:00+00:00",
        "recorded_as_of": "2026-03-02T00:00:00+00:00",
        "total": 0, "returned": 0, "complete": True, "has_more": False,
        "next_event_time": None, "next_id": None, "items": [],
    }
    with respx.mock(base_url=BASE) as mock:
        route = mock.get("/v1/snapshot").mock(return_value=httpx.Response(200, json=payload))
        dt = datetime(2026, 3, 1, tzinfo=timezone.utc)
        await client.knowledge_snapshot("agent-1", dt)
        url = str(route.calls[0].request.url)
        assert "as_of" in url


# ── Decision evidence ────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_record_decision_and_export_receipt(client):
    decision = {
        "id": "00000000-0000-0000-0000-000000000812",
        "agent_id": "underwriter",
        "decision_type": "credit_application",
        "outcome": "declined",
    }
    receipt = {
        "$schema": "https://lians.ai/specs/decision-receipt/v0.1/schema.json",
        "receipt_version": "0.1",
        "decision": {"id": decision["id"]},
        "completeness": {"grade": "A"},
        "integrity": {"receipt_hash": "a" * 64},
    }
    with respx.mock(base_url=BASE) as mock:
        create = mock.post("/v1/decisions").mock(
            return_value=httpx.Response(200, json=decision)
        )
        export = mock.get(f"/v1/decisions/{decision['id']}/receipt").mock(
            return_value=httpx.Response(200, json=receipt)
        )
        created = await client.record_decision(
            agent_id="underwriter",
            decision_type="credit_application",
            outcome="declined",
            decided_at="2026-07-01T14:30:00Z",
            knowledge_as_of=datetime(2026, 7, 1, 14, 30, tzinfo=timezone.utc),
            knowledge_recorded_as_of=datetime(
                2026, 7, 1, 14, 31, tzinfo=timezone.utc
            ),
            policy_version="4.2",
        )
        exported = await client.decision_receipt(decision["id"])

        assert create.called and export.called
        assert created["outcome"] == "declined"
        assert exported["completeness"]["grade"] == "A"
        body = json.loads(create.calls[0].request.content)
        assert body["policy_version"] == "4.2"
        assert body["knowledge_as_of"] == "2026-07-01T14:30:00+00:00"
        assert body["knowledge_recorded_as_of"] == "2026-07-01T14:31:00+00:00"


@pytest.mark.asyncio
async def test_verify_receipt_and_assess_impact(client):
    verification = {"valid": True, "hash_valid": True, "signature_valid": True}
    impact = {
        "dependency": {"kind": "source", "value": "source-1"},
        "total": 3,
        "items": [],
    }
    with respx.mock(base_url=BASE) as mock:
        verify_route = mock.post("/v1/receipts/verify").mock(
            return_value=httpx.Response(200, json=verification)
        )
        impact_route = mock.post("/v1/decisions/impact").mock(
            return_value=httpx.Response(200, json=impact)
        )
        verified = await client.verify_decision_receipt(
            {"receipt_version": "0.1"}, require_signature=True
        )
        assessed = await client.assess_decision_impact(
            "source",
            "source-1",
            change_type="corrected",
            agent_id="receipt-impact-monitor",
        )

        assert verify_route.called and impact_route.called
        assert verified["valid"] is True
        assert assessed["total"] == 3
        impact_body = json.loads(impact_route.calls[0].request.content)
        assert impact_body["change_type"] == "corrected"
        assert impact_body["agent_id"] == "receipt-impact-monitor"


# ── backtest_check ────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_backtest_check_post(client):
    payload = {
        "agent_id": "agent-1", "namespace": "test-ns",
        "simulation_as_of": "2026-01-01T00:00:00Z",
        "memories_checked": 5, "flags_total": 0, "flags_returned": 0,
        "flags_complete": True, "has_more": False, "flags": [],
        "contamination_rate": 0.0, "is_clean": True,
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
        "memories_checked": 3, "flags_total": 1, "flags_returned": 1,
        "flags_complete": True, "has_more": False,
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
        "total_rows": 0, "returned_rows": 0, "has_more": False,
        "complete": True, "next_chain_position": None,
        "snapshot_max_chain_position": 0,
        "chain_status": "ok", "chain_violations": None,
        "chain_rows_checked": 0, "chain_truncated": False,
        "chain_tip": "0" * 64, "events": [],
    }
    with respx.mock(base_url=BASE) as mock:
        route = mock.get("/v1/admin/audit/export").mock(return_value=httpx.Response(200, json=payload))
        await client.audit_export(namespace="test-ns")
        assert route.calls[0].request.headers.get("x-admin-secret") == ADMIN


@pytest.mark.asyncio
async def test_verify_chain_sends_admin_secret(client):
    with respx.mock(base_url=BASE) as mock:
        route = mock.get("/v1/admin/audit/verify").mock(
            return_value=httpx.Response(
                200,
                json={
                    "namespace": "test-ns",
                    "status": "ok",
                    "rows_checked": 100,
                    "truncated": False,
                    "chain_tip": "a" * 64,
                    "violations": [],
                },
            )
        )
        await client.verify_chain("test-ns")
        assert route.calls[0].request.headers.get("x-admin-secret") == ADMIN


# ── LiansError ─────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_error_on_4xx(client):
    with respx.mock(base_url=BASE) as mock:
        mock.post("/v1/memories").mock(return_value=httpx.Response(401, text="Unauthorized"))
        with pytest.raises(LiansError) as exc_info:
            await client.add_memory(agent_id="a", content="c", event_time="2026-01-01T00:00:00Z")
        assert exc_info.value.status == 401
        assert "Unauthorized" in exc_info.value.body


@pytest.mark.asyncio
async def test_error_on_500(client):
    with respx.mock(base_url=BASE) as mock:
        mock.post("/v1/recall").mock(return_value=httpx.Response(500, text="Internal Server Error"))
        with pytest.raises(LiansError) as exc_info:
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
    async with LiansClient(BASE, KEY, http2=False) as client:
        with respx.mock(base_url=BASE) as mock:
            mock.post("/v1/recall").mock(
                return_value=httpx.Response(200, json={"memories": [], "as_of": None, "total_candidates": 0})
            )
            result = await client.recall(agent_id="a", query="q")
            assert result.memories == []


@pytest.mark.asyncio
async def test_webhook_mutations_send_required_updated_at_precondition(client):
    expected = datetime(2026, 8, 2, 12, 30, tzinfo=timezone.utc)
    endpoint = {
        "id": "00000000-0000-0000-0000-000000000010",
        "namespace": "test-ns",
        "url": "https://receiver.example/hook",
        "events": ["memory.erased"],
        "enabled": False,
        "description": None,
        "created_at": "2026-08-01T12:30:00Z",
        "updated_at": expected.isoformat(),
    }
    with respx.mock(base_url=BASE) as mock:
        patch_route = mock.patch("/v1/webhooks/webhook-1").mock(
            return_value=httpx.Response(200, json=endpoint)
        )
        delete_route = mock.delete(
            "/v1/webhooks/webhook-1",
            params={"expected_updated_at": expected.isoformat()},
        ).mock(return_value=httpx.Response(200, json={}))

        await client.update_webhook(
            "webhook-1",
            expected_updated_at=expected,
            enabled=False,
        )
        await client.delete_webhook(
            "webhook-1",
            expected_updated_at=expected,
        )

        assert json.loads(patch_route.calls[0].request.content) == {
            "expected_updated_at": expected.isoformat(),
            "enabled": False,
        }
        assert delete_route.called
