"""Decision Envelope capture, honest grades, and proactive blast radius."""

from __future__ import annotations

import base64
import hashlib
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock

import pytest
import pytest_asyncio
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.serialization import (
    Encoding,
    NoEncryption,
    PrivateFormat,
    PublicFormat,
)
from httpx import ASGITransport, AsyncClient
from lians import AsyncLiansClient
from src.lians.config import get_settings
from src.lians.db import get_db
from src.lians.evidence_signing import verify_evidence_pack
from src.lians.main import app
from src.lians.models import ApiKey, EventLog

KEY = "decision-envelope-test-key"
NS = "decision-envelope-test"
T0 = datetime(2026, 7, 20, 12, 0, tzinfo=UTC)
H1 = "1" * 64
H2 = "2" * 64
H3 = "3" * 64
TRACE_ID = "0123456789abcdef0123456789abcdef"


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
            transport=ASGITransport(app=app),
            base_url="http://test",
        ) as ac:
            yield ac
    finally:
        app.dependency_overrides.clear()


def headers():
    return {"X-API-Key": KEY}


async def open_envelope(client, *, profile="standard", trace_id=None):
    response = await client.post(
        "/v1/decision-envelopes",
        headers=headers(),
        json={
            "agent_id": "credit-agent",
            "decision_type": "credit_application",
            "regime": "ECOA_REG_B",
            "subject_id": "applicant-42",
            "session_id": "session-1",
            "trace_id": trace_id,
            "knowledge_as_of": T0.isoformat(),
            "completeness_profile": profile,
        },
    )
    assert response.status_code == 200, response.text
    return response.json()


def otlp_payload():
    return {
        "resourceSpans": [
            {
                "resource": {
                    "attributes": [
                        {
                            "key": "service.name",
                            "value": {"stringValue": "credit-agent"},
                        }
                    ]
                },
                "scopeSpans": [
                    {
                        "scope": {"name": "lians.tests", "version": "1.0"},
                        "spans": [
                            {
                                "traceId": TRACE_ID,
                                "spanId": "0123456789abcdef",
                                "name": "credit decision",
                                "kind": 3,
                                "startTimeUnixNano": "1784900000000000000",
                                "endTimeUnixNano": "1784900001000000000",
                                "attributes": [
                                    {
                                        "key": "gen_ai.request.model",
                                        "value": {"stringValue": "credit-v3"},
                                    }
                                ],
                                "status": {"code": 1},
                            }
                        ],
                    }
                ],
            }
        ]
    }


@pytest.mark.asyncio
async def test_recall_binds_to_envelope_and_seals_as_verifiable(client):
    envelope = await open_envelope(client)
    memory = (
        await client.post(
            "/v1/memories",
            headers=headers(),
            json={
                "agent_id": "credit-agent",
                "content": "Verified income is 72000",
                "event_time": T0.isoformat(),
                "subject_id": "applicant-42",
                "metadata": {"field": "income"},
            },
        )
    ).json()

    recalled = await client.post(
        "/v1/recall",
        headers=headers(),
        json={
            "agent_id": "credit-agent",
            "query": "verified income",
            "k": 5,
            "decision_envelope_id": envelope["id"],
        },
    )
    assert recalled.status_code == 200, recalled.text
    receipt = recalled.json()
    assert len(receipt["receipt_sha256"]) == 64

    sealed = await client.post(
        f"/v1/decision-envelopes/{envelope['id']}/seal",
        headers=headers(),
        json={
            "outcome": "declined",
            "reason_codes": ["DTI_HIGH"],
            "decided_at": (T0 + timedelta(seconds=2)).isoformat(),
            "model_id": "credit-v3",
            "model_version": "3.2.1",
            "model_artifact_hash": H1,
            "evidence_memory_ids": [memory["id"]],
            "input_hash": H2,
            "output_hash": H3,
        },
    )
    assert sealed.status_code == 200, sealed.text
    payload = sealed.json()
    assert payload["completeness"]["grade"] == "verifiable"
    assert payload["completeness"]["next_grade"] == "replayable"
    assert not any(
        gap["code"] == "influence_evidence"
        for gap in payload["completeness"]["gaps"]
    )
    types = {item["evidence_type"] for item in payload["evidence"]}
    assert {"recall_receipt", "memory", "model", "input", "output"} <= types

    reconstruction = await client.get(
        f"/v1/decisions/{payload['decision']['id']}/reconstruction",
        headers=headers(),
    )
    assert reconstruction.status_code == 200, reconstruction.text
    reconstructed = reconstruction.json()
    assert reconstructed["schema"].endswith("decision-reconstruction/v2")
    assert reconstructed["completeness"]["grade"] == "verifiable"
    assert reconstructed["knowledge_snapshot"][0]["id"] == memory["id"]
    assert any(item["kind"] == "decision" for item in reconstructed["timeline"])
    assert any(item["kind"] == "evidence" for item in reconstructed["timeline"])

    readiness = await client.get(
        "/v1/integrations/validmind/evidence-readiness",
        headers=headers(),
    )
    assert readiness.status_code == 200, readiness.text
    summary = readiness.json()["summary"]
    assert summary["grades"]["verifiable"] == 1
    assert summary["verifiable_rate"] == 1.0

    private_key = Ed25519PrivateKey.generate()
    private_key_b64 = base64.b64encode(
        private_key.private_bytes(Encoding.Raw, PrivateFormat.Raw, NoEncryption())
    ).decode()
    settings = get_settings()
    prior_key = settings.evidence_signing_private_key
    prior_key_id = settings.evidence_signing_key_id
    settings.evidence_signing_private_key = private_key_b64
    settings.evidence_signing_key_id = "test-evidence-key"
    try:
        pack_response = await client.get(
            f"/v1/decisions/{payload['decision']['id']}/evidence-pack",
            headers=headers(),
            params={"version": "v2"},
        )
    finally:
        settings.evidence_signing_private_key = prior_key
        settings.evidence_signing_key_id = prior_key_id
    assert pack_response.status_code == 200, pack_response.text
    pack = pack_response.json()
    assert pack["schema"].endswith("evidence-pack/v2")
    assert pack["signature"]["status"] == "signed"
    label_verification = verify_evidence_pack(
        pack,
        expected_key_id="test-evidence-key",
    )
    assert label_verification["accepted"] is True
    assert label_verification["signature_valid"] is True
    assert label_verification["key_id_matched"] is True
    assert label_verification["identity_verified"] is False

    trusted_public_key_b64 = base64.b64encode(
        private_key.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw)
    ).decode()
    trusted_verification = verify_evidence_pack(
        pack,
        trusted_public_key_b64=trusted_public_key_b64,
        expected_key_id="test-evidence-key",
    )
    assert trusted_verification["accepted"] is True
    assert trusted_verification["signature_valid"] is True
    assert trusted_verification["identity_verified"] is True

    wrong_public_key_b64 = base64.b64encode(
        Ed25519PrivateKey.generate()
        .public_key()
        .public_bytes(Encoding.Raw, PublicFormat.Raw)
    ).decode()
    wrong_identity = verify_evidence_pack(
        pack,
        trusted_public_key_b64=wrong_public_key_b64,
    )
    assert wrong_identity["accepted"] is False
    assert wrong_identity["signature_valid"] is True
    assert wrong_identity["identity_verified"] is False

    tampered = dict(pack)
    tampered["decision"] = {**pack["decision"], "outcome": "tampered"}
    tamper_verification = verify_evidence_pack(
        tampered,
        trusted_public_key_b64=trusted_public_key_b64,
    )
    assert tamper_verification["accepted"] is False
    assert tamper_verification["manifest_hash_valid"] is False


@pytest.mark.asyncio
async def test_incomplete_decision_is_only_recorded_and_names_gaps(client):
    envelope = await open_envelope(client, profile="regulated_recordkeeping")
    sealed = await client.post(
        f"/v1/decision-envelopes/{envelope['id']}/seal",
        headers=headers(),
        json={
            "outcome": "manual_review",
            "decided_at": T0.isoformat(),
        },
    )
    assert sealed.status_code == 200, sealed.text
    completeness = sealed.json()["completeness"]
    assert completeness["grade"] == "recorded"
    assert completeness["next_grade"] == "reconstructable"
    gaps = {item["code"]: item["blocks"] for item in completeness["gaps"]}
    assert gaps["influence_evidence"] == "reconstructable"
    assert gaps["trace_context"] == "reconstructable"
    assert gaps["policy_context"] == "reconstructable"
    assert gaps["input_integrity"] == "verifiable"
    assert gaps["replay_manifest"] == "replayable"


@pytest.mark.asyncio
async def test_otlp_span_automatically_satisfies_trace_context(client):
    envelope = await open_envelope(
        client,
        profile="regulated_recordkeeping",
        trace_id=TRACE_ID,
    )
    received = await client.post(
        "/v1/traces",
        headers={**headers(), "Content-Type": "application/json"},
        json=otlp_payload(),
    )
    assert received.status_code == 200, received.text
    assert received.json()["linkedDecisionEvidence"] == 1

    sealed = await client.post(
        f"/v1/decision-envelopes/{envelope['id']}/seal",
        headers=headers(),
        json={
            "outcome": "approve",
            "decided_at": T0.isoformat(),
            "model_id": "credit-v3",
            "model_version": "3.2.1",
            "model_artifact_hash": H1,
            "policy_version": "credit-policy-2026-07",
            "policy_artifact_hash": H2,
            "input_hash": H2,
            "output_hash": H3,
        },
    )
    assert sealed.status_code == 200, sealed.text
    payload = sealed.json()
    assert payload["completeness"]["grade"] == "verifiable"
    assert payload["completeness"]["checks"]["trace_context"] is True
    trace_links = [
        item
        for item in payload["evidence"]
        if item["evidence_type"] == "otel_span"
    ]
    assert len(trace_links) == 1
    assert trace_links[0]["source_id"] == f"{TRACE_ID}:0123456789abcdef"


@pytest.mark.asyncio
async def test_source_change_returns_and_emits_blast_radius(client, db):
    envelope = await open_envelope(client)
    await client.post(
        f"/v1/decision-envelopes/{envelope['id']}/evidence",
        headers=headers(),
        json={
            "evidence": [
                {
                    "evidence_type": "external",
                    "role": "used",
                    "source_id": "vendor-risk-feed",
                    "source_version": "2026-07-20",
                    "artifact_hash": H1,
                    "occurred_at": T0.isoformat(),
                }
            ]
        },
    )
    sealed = (
        await client.post(
            f"/v1/decision-envelopes/{envelope['id']}/seal",
            headers=headers(),
            json={
                "outcome": "approve",
                "decided_at": T0.isoformat(),
                "input_hash": H2,
                "output_hash": H3,
            },
        )
    ).json()

    radius = await client.get(
        "/v1/evidence/blast-radius",
        headers=headers(),
        params={
            "evidence_type": "external",
            "source_id": "vendor-risk-feed",
            "source_version": "2026-07-20",
        },
    )
    assert radius.status_code == 200, radius.text
    assert radius.json()["impacted_decisions"] == 1
    assert radius.json()["decisions"][0]["decision"]["id"] == sealed["decision"]["id"]

    changed = await client.post(
        "/v1/evidence/changes",
        headers=headers(),
        json={
            "evidence_type": "external",
            "source_id": "vendor-risk-feed",
            "source_version": "2026-07-20",
            "artifact_hash": H1,
            "new_source_version": "2026-07-21",
            "new_artifact_hash": H2,
            "change_kind": "retracted",
            "severity": "high",
            "changed_at": (T0 + timedelta(days=1)).isoformat(),
            "reason": "Provider restated the source file.",
        },
    )
    assert changed.status_code == 200, changed.text
    result = changed.json()
    assert result["change_event"]["event_type"] == "source_change"
    assert result["blast_radius"]["impacted_decisions"] == 1

    ops = [
        row.op
        for row in (
            await db.execute(
                EventLog.__table__.select().where(EventLog.namespace == NS)
            )
        ).all()
    ]
    assert "evidence_change_recorded" in ops


@pytest.mark.asyncio
async def test_record_event_can_bind_tool_evidence_to_open_envelope(client):
    envelope = await open_envelope(client)
    event = await client.post(
        "/v1/records/events",
        headers=headers(),
        json={
            "event_type": "tool_result",
            "agent_id": "credit-agent",
            "occurred_at": T0.isoformat(),
            "decision_envelope_id": envelope["id"],
            "payload": {"tool": "credit_bureau", "result": "score:740"},
        },
    )
    assert event.status_code == 200, event.text
    links = await client.get(
        f"/v1/decision-envelopes/{envelope['id']}/evidence",
        headers=headers(),
    )
    assert links.status_code == 200
    tool = next(item for item in links.json() if item["evidence_type"] == "tool_result")
    assert tool["source_id"] == event.json()["id"]
    assert len(tool["artifact_hash"]) == 64

    sealed = await client.post(
        f"/v1/decision-envelopes/{envelope['id']}/seal",
        headers=headers(),
        json={
            "outcome": "manual_review",
            "decided_at": (T0 + timedelta(seconds=1)).isoformat(),
        },
    )
    decision_id = sealed.json()["decision"]["id"]
    reconstruction = await client.get(
        f"/v1/decisions/{decision_id}/reconstruction",
        headers=headers(),
    )
    assert reconstruction.status_code == 200, reconstruction.text
    assert [item["id"] for item in reconstruction.json()["ledger_events"]] == [
        event.json()["id"]
    ]

    after_seal = await client.post(
        "/v1/records/events",
        headers=headers(),
        json={
            "event_type": "human_oversight",
            "agent_id": "reviewer-1",
            "occurred_at": (T0 + timedelta(seconds=2)).isoformat(),
            "decision_envelope_id": envelope["id"],
            "payload": {"status": "affirmed"},
        },
    )
    assert after_seal.status_code == 200, after_seal.text
    assert after_seal.json()["decision_id"] == decision_id


@pytest.mark.asyncio
async def test_unknown_custom_completeness_check_is_rejected(client):
    response = await client.post(
        "/v1/decision-envelopes",
        headers=headers(),
        json={
            "agent_id": "agent",
            "decision_type": "screening",
            "required_checks": {"verifiable": ["trust_me"]},
        },
    )
    assert response.status_code == 422


@pytest.mark.asyncio
async def test_python_sdk_serializes_decision_evidence_contract():
    sdk = AsyncLiansClient(base_url="https://api.example.com", api_key="key")
    sdk._req = AsyncMock(return_value={"id": "envelope-1"})

    await sdk.open_decision_envelope(
        agent_id="underwriter-1",
        decision_type="credit_application",
        knowledge_as_of=T0,
        completeness_profile="regulated_recordkeeping",
        trace_id=TRACE_ID,
    )
    sdk._req.assert_awaited_once_with(
        "POST",
        "/v1/decision-envelopes",
        json={
            "agent_id": "underwriter-1",
            "decision_type": "credit_application",
            "knowledge_as_of": T0.isoformat(),
            "completeness_profile": "regulated_recordkeeping",
            "trace_id": TRACE_ID,
        },
    )

    sdk._req.reset_mock(return_value=True)
    await sdk.record_evidence_change(
        evidence_type="external",
        source_id="vendor-risk-feed",
        change_kind="revised",
        changed_at=T0,
        new_source_version="2026-07-21",
    )
    sdk._req.assert_awaited_once_with(
        "POST",
        "/v1/evidence/changes",
        json={
            "evidence_type": "external",
            "source_id": "vendor-risk-feed",
            "change_kind": "revised",
            "changed_at": T0.isoformat(),
            "new_source_version": "2026-07-21",
        },
    )
