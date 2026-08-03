"""Decision Receipt v0.1 and change-impact API contract tests."""

import base64
import hashlib
from datetime import UTC, datetime
from uuid import UUID

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select
from lians.config import get_settings
from lians.db import get_db
from lians.decision_receipt import verify_decision_receipt
from lians.main import app
from lians.models import ApiKey, LedgerEvent

KEY = "receipt-api-test-key"
NS = "receipt-api-test"
T0 = datetime(2026, 7, 1, 14, 30, tzinfo=UTC)


@pytest_asyncio.fixture
async def client(db):
    db.add(
        ApiKey(
            hashed_key=hashlib.sha256(KEY.encode()).hexdigest(),
            namespace=NS,
            scopes=["read", "write", "admin"],
        )
    )
    await db.commit()

    async def override():
        yield db

    app.dependency_overrides[get_db] = override
    try:
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as http:
            yield http
    finally:
        app.dependency_overrides.clear()


def headers():
    return {"X-API-Key": KEY}


async def _memory(client, *, content="Verified annual income is $72,000"):
    response = await client.post(
        "/v1/memories",
        headers=headers(),
        json={
            "agent_id": "underwriter-1",
            "content": content,
            "event_time": T0.isoformat(),
            "subject_id": "application-8127",
            "source": "income-provider",
            "metadata": {"field": "income", "source_version": "inc-2026-07-01"},
        },
    )
    assert response.status_code == 200, response.text
    return response.json()


async def _decision(client, memory_id, **overrides):
    body = {
        "agent_id": "underwriter-1",
        "decision_type": "credit_application",
        "outcome": "declined",
        "reason_codes": ["DTI_HIGH"],
        "regime": "ECOA_REG_B",
        "subject_id": "application-8127",
        "session_id": "sess-8127",
        "model_id": "credit-risk",
        "model_version": "3.2.1",
        "policy_version": "4.2",
        "decided_at": T0.isoformat(),
        "knowledge_as_of": T0.isoformat(),
        "evidence_memory_ids": memory_id if isinstance(memory_id, list) else [memory_id],
        "input_hash": "a" * 64,
        "output_hash": "b" * 64,
        "metadata": {
            "model_provider": "openai",
            "system_instruction_hash": "c" * 64,
            "principal": {
                "id": "svc-underwriter",
                "type": "service",
                "scopes": ["applications:read", "decisions:write"],
            },
            "authorization": {
                "decision": "allow",
                "scopes": ["applications:read", "decisions:write"],
            },
            "policy_evaluation": {
                "decision": "allow",
                "rule_ids": ["dti-threshold"],
            },
            "tools": [
                {
                    "name": "income-provider",
                    "call_id": "call-8127",
                    "definition_hash": "d" * 64,
                    "result_hash": "e" * 64,
                }
            ],
            "trace_id": "1234567890abcdef1234567890abcdef",
            "risk_level": "high",
        },
    }
    body.update(overrides)
    response = await client.post("/v1/decisions", headers=headers(), json=body)
    assert response.status_code == 200, response.text
    return response.json()


@pytest.mark.asyncio
async def test_signed_decision_receipt_is_complete_and_verifiable(client, monkeypatch):
    private_key = bytes(range(32))
    monkeypatch.setenv(
        "RECEIPT_SIGNING_PRIVATE_KEY", base64.b64encode(private_key).decode("ascii")
    )
    monkeypatch.setenv("RECEIPT_SIGNING_KEY_ID", "test-key-2026")
    get_settings.cache_clear()

    memory = await _memory(client)
    decision = await _decision(client, memory["id"])
    response = await client.get(
        f"/v1/decisions/{decision['id']}/receipt", headers=headers()
    )
    assert response.status_code == 200, response.text
    receipt = response.json()

    assert receipt["receipt_version"] == "0.1"
    assert receipt["decision"]["id"] == decision["id"]
    assert decision["record_hash_version"] == 3
    assert decision["recorded_by_principal_type"] == "api_key"
    assert decision["recorded_by_role"] is None
    assert decision["recorded_by_scopes"] == ["admin", "read", "write"]
    assert receipt["actor"]["recorded_by"]["authorization_snapshot_verified"] is True
    assert receipt["authorization"]["recording_write"] == {
        "verified": True,
        "decision": "allowed",
        "action": "decision.record",
        "principal_ref": decision["recorded_by_principal_ref"],
        "principal_type": "api_key",
        "role": None,
        "scopes": ["admin", "read", "write"],
        "auth_method": "api_key",
        "credential_ref": decision["recorded_by_credential_ref"],
    }
    assert receipt["authorization"]["declared_workflow_context"] == {
        "verified": False,
        "source": "caller_supplied_decision_metadata",
        "authorization": {
            "decision": "allow",
            "scopes": ["applications:read", "decisions:write"],
        },
    }
    assert receipt["sources"][0]["content_hash"] == memory["content_hash"]
    assert receipt["completeness"]["score"] == 100
    assert receipt["completeness"]["grade"] == "A"
    assert receipt["audit_chain"]["status"] == "ok"
    manifest = receipt["audit_chain"]["lians_evidence_graph"]
    assert manifest["complete"] is True
    assert manifest["decision_id"] == decision["id"]
    assert manifest["links_total"] == len(manifest["entries"])
    assert manifest["normalization"]["normalized_complete"] is True
    assert receipt["integrity"]["signature"]["key_id"] == "test-key-2026"
    assert verify_decision_receipt(receipt, require_signature=True)["valid"] is True

    api_verification = await client.post(
        "/v1/receipts/verify",
        headers=headers(),
        json={"receipt": receipt, "require_signature": True},
    )
    assert api_verification.status_code == 200
    assert api_verification.json()["valid"] is True

    receipt["decision"]["outcome"] = "approved"
    tampered = await client.post(
        "/v1/receipts/verify",
        headers=headers(),
        json={"receipt": receipt, "require_signature": True},
    )
    assert tampered.status_code == 200
    assert tampered.json()["valid"] is False
    assert tampered.json()["hash_valid"] is False


@pytest.mark.asyncio
async def test_unsigned_partial_receipt_names_missing_evidence(client, monkeypatch):
    monkeypatch.delenv("RECEIPT_SIGNING_PRIVATE_KEY", raising=False)
    get_settings.cache_clear()
    response = await client.post(
        "/v1/decisions",
        headers=headers(),
        json={
            "agent_id": "screening-agent",
            "decision_type": "screening",
            "outcome": "manual_review",
            "decided_at": T0.isoformat(),
        },
    )
    assert response.status_code == 200
    receipt_response = await client.get(
        f"/v1/decisions/{response.json()['id']}/receipt", headers=headers()
    )
    assert receipt_response.status_code == 200, receipt_response.text
    receipt = receipt_response.json()
    assert receipt["completeness"]["status"] == "incomplete"
    assert "sources.provenance" in receipt["completeness"]["missing"]
    assert "integrity.signature" in receipt["completeness"]["missing"]
    assert receipt["integrity"]["signature"] is None


@pytest.mark.asyncio
async def test_source_change_returns_direct_and_reachable_blast_radius(client, db):
    memory = await _memory(client)
    first = await _decision(client, memory["id"])
    second = await _decision(
        client,
        memory["id"],
        subject_id="application-8134",
        session_id="sess-8134",
    )
    third = await _decision(
        client,
        [],
        subject_id="application-8191",
        session_id="sess-8191",
        metadata={"risk_level": "medium", "reachable_dependencies": [memory["id"]]},
    )

    response = await client.post(
        "/v1/decisions/impact",
        headers=headers(),
        json={
            "dependency_kind": "source",
            "dependency_value": memory["id"],
            "change_type": "corrected",
            "note": "Provider corrected the verified-income record.",
        },
    )
    assert response.status_code == 200, response.text
    impact = response.json()
    assert impact["total"] == 3
    assert impact["direct_count"] == 2
    assert impact["reachable_count"] == 1
    assert {item["decision"]["id"] for item in impact["items"]} == {
        first["id"],
        second["id"],
        third["id"],
    }
    assert impact["change_event_id"] is not None
    event = await db.scalar(
        select(LedgerEvent).where(LedgerEvent.id == UUID(impact["change_event_id"]))
    )
    assert event is not None
    assert event.event_type == "system_change"
    assert len(event.payload["affected_decision_ids"]) == 3
