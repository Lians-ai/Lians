"""Focused DecisionRecord v3 authenticity and binding tests."""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

import pytest
from lians.audit_chain import chain_log
from lians.decision_record_integrity import (
    DECISION_RECORD_HASH_VERSION,
    VERIFIED_INTEGRITY_STATUS,
    DecisionRecordIntegrityError,
    assert_decision_record_hash,
    assert_decision_record_integrity,
    authenticated_recorder_authorization_snapshot,
    authenticated_recorder_provenance,
    compute_decision_record_hash,
    decision_record_binding_payload,
)
from lians.models import DecisionRecord


def _record() -> DecisionRecord:
    principal, method, credential = authenticated_recorder_provenance(
        principal_ref=(
            "lians:principal:v1:api-key:00000000-0000-0000-0000-000000000042"
        ),
        auth_method="api_key",
        credential_id="00000000-0000-0000-0000-000000000042",
    )
    principal_type, role, scopes = authenticated_recorder_authorization_snapshot(
        principal_type="api_key",
        role="analyst",
        effective_scopes=["write", "read"],
    )
    now = datetime(2026, 8, 2, tzinfo=UTC)
    row = DecisionRecord(
        id=uuid4(),
        namespace="integrity-test",
        barrier_group=None,
        agent_id="caller-claimed-agent",
        recorded_by_principal_ref=principal,
        recorded_by_auth_method=method,
        recorded_by_credential_ref=credential,
        recorded_by_principal_type=principal_type,
        recorded_by_role=role,
        recorded_by_scopes=scopes,
        decision_type="approval",
        outcome="allow",
        reason_codes=["POLICY_MATCH"],
        regime="internal",
        subject_id=None,
        session_id="session-42",
        model_id="model-42",
        model_version="2026-08",
        policy_version="policy-42",
        decided_at=now,
        recorded_at=now,
        knowledge_as_of=now,
        knowledge_recorded_as_of=now,
        evidence_memory_ids=[],
        input_hash="a" * 64,
        output_hash="b" * 64,
        supersedes_id=None,
        metadata_={"risk_level": "high"},
        record_hash_version=DECISION_RECORD_HASH_VERSION,
        record_integrity_status=VERIFIED_INTEGRITY_STATUS,
        record_hash="",
    )
    row.record_hash = compute_decision_record_hash(row)
    return row


def test_v3_hash_binds_authentication_authorization_and_claimed_agent() -> None:
    row = _record()
    assert_decision_record_hash(row)

    original_hash = row.record_hash
    row.agent_id = "impersonated-label"
    with pytest.raises(DecisionRecordIntegrityError, match="hash verification"):
        assert_decision_record_hash(row)
    row.agent_id = "caller-claimed-agent"

    row.recorded_by_principal_ref = (
        "lians:principal:v1:api-key:00000000-0000-0000-0000-000000000099"
    )
    with pytest.raises(DecisionRecordIntegrityError, match="hash verification"):
        assert_decision_record_hash(row)
    row.recorded_by_principal_ref = (
        "lians:principal:v1:api-key:00000000-0000-0000-0000-000000000042"
    )

    row.recorded_by_scopes = ["read", "write", "admin"]
    with pytest.raises(DecisionRecordIntegrityError, match="hash verification"):
        assert_decision_record_hash(row)
    assert row.record_hash == original_hash


def test_review_projection_is_not_part_of_decision_record_hash() -> None:
    row = _record()
    original_hash = row.record_hash
    row.human_review_status = "affirmed"
    row.human_reviewer = "lians:principal:v1:api-key:reviewer"
    row.human_reviewed_at = datetime(2026, 8, 3, tzinfo=UTC)
    assert compute_decision_record_hash(row) == original_hash


def test_legacy_provenance_cannot_be_treated_as_verified() -> None:
    row = _record()
    row.record_hash_version = 1
    row.record_integrity_status = "legacy_unverified"
    row.recorded_by_principal_ref = "lians:principal:v1:legacy-unverified"
    row.recorded_by_auth_method = "legacy_unverified"
    row.recorded_by_credential_ref = None
    with pytest.raises(DecisionRecordIntegrityError, match="legacy or otherwise unverified"):
        assert_decision_record_hash(row)


def test_audit_binding_payload_is_minimal_and_non_pii() -> None:
    row = _record()
    assert decision_record_binding_payload(row) == {
        "schema": "lians.decision-record-binding.v1",
        "decision_id": str(row.id),
        "record_hash": row.record_hash,
    }


@pytest.mark.asyncio
async def test_integrity_requires_original_authenticated_audit_binding(db) -> None:
    row = _record()
    db.add(row)
    await db.flush()
    with pytest.raises(DecisionRecordIntegrityError, match="no unique authenticated"):
        await assert_decision_record_integrity(db, row)

    await chain_log(
        db,
        row.namespace,
        row.recorded_by_principal_ref,
        "decision_recorded",
        content_hash=row.record_hash,
        payload=decision_record_binding_payload(row),
    )
    binding = await assert_decision_record_integrity(db, row)
    assert binding.agent_id == row.recorded_by_principal_ref
