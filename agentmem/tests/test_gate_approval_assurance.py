from __future__ import annotations

import base64
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from uuid import uuid4

import pytest
from lians.control_schemas import (
    GateApproval,
    GateEvaluationRequest,
    GatePolicyRuleCreate,
)
from lians.control_service import _evaluate_rule, _finalize_disposition, _verify_receipt_context
from lians.decision_receipt import build_decision_receipt, receipt_signing_public_key
from pydantic import ValidationError

EVALUATED_AT = datetime(2026, 8, 2, 12, 0, tzinfo=UTC)
MEDIATOR = "lians:principal:v1:api-key:00000000-0000-0000-0000-000000000001"


def _request() -> GateEvaluationRequest:
    return GateEvaluationRequest(
        action="release",
        target_ref="urn:lians:test:release",
        decision_id=uuid4(),
        enforcement_principal_id=MEDIATOR,
        permit_ttl_seconds=30,
        execution_request_hash="a" * 64,
        policy_name="production",
    )


def _rule(**overrides):
    values = {
        "id": uuid4(),
        "name": "recent-human-review",
        "action_on_failure": "deny",
        "required_receipt_grade": None,
        "require_trusted_issuer": False,
        "require_sources_current": False,
        "require_policy_attached": False,
        "required_principal_scopes": [],
        "minimum_approval_count": 1,
        "required_approval_roles": ["reviewer"],
        "allowed_approval_principal_types": ["human"],
        "maximum_approval_age_seconds": 300,
        "require_information_barrier_match": False,
        "block_untrusted_content": False,
        "max_untrusted_content_score": None,
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def _approval(*, principal_type: str, age_seconds: int) -> GateApproval:
    return GateApproval(
        principal_id=f"{principal_type}-principal",
        role="reviewer",
        principal_type=principal_type,
        auth_method="oidc_bearer" if principal_type != "api_key" else "api_key",
        attested_at=EVALUATED_AT - timedelta(seconds=age_seconds),
    )


def test_recent_human_approval_satisfies_assurance_policy():
    failures = _evaluate_rule(
        _rule(),
        _request(),
        {},
        [_approval(principal_type="human", age_seconds=60)],
        EVALUATED_AT,
    )

    assert failures == []


@pytest.mark.parametrize("principal_type", ["workload", "api_key"])
def test_nonhuman_approval_cannot_satisfy_human_policy(principal_type: str):
    failures = _evaluate_rule(
        _rule(),
        _request(),
        {},
        [_approval(principal_type=principal_type, age_seconds=60)],
        EVALUATED_AT,
    )

    assert {failure["code"] for failure in failures} == {
        "approvals.count_below_required",
        "approvals.roles_missing",
    }


def test_stale_human_approval_cannot_satisfy_freshness_policy():
    failures = _evaluate_rule(
        _rule(),
        _request(),
        {},
        [_approval(principal_type="human", age_seconds=301)],
        EVALUATED_AT,
    )

    assert {failure["code"] for failure in failures} == {
        "approvals.count_below_required",
        "approvals.roles_missing",
    }


def test_assurance_constraints_require_an_approval_requirement():
    with pytest.raises(ValidationError, match="require an approval count or role"):
        GatePolicyRuleCreate(
            name="ambiguous",
            allowed_approval_principal_types=["human"],
        )


def test_approval_policy_lists_reject_duplicates():
    with pytest.raises(ValidationError, match="must not contain duplicates"):
        GatePolicyRuleCreate(
            name="duplicate",
            minimum_approval_count=1,
            required_approval_roles=["reviewer", "reviewer"],
        )


@pytest.mark.parametrize("default_disposition", ["deny", "review"])
def test_restrictive_default_allows_only_after_an_explicit_rule_passes(
    default_disposition: str,
):
    no_match_reasons: list[dict] = []
    assert _finalize_disposition(default_disposition, no_match_reasons, 0) == (
        default_disposition
    )
    assert no_match_reasons[0]["code"] == "policy.default_disposition"

    matched_reasons: list[dict] = []
    assert _finalize_disposition(default_disposition, matched_reasons, 1) == "allow"
    assert matched_reasons == []


def test_failed_rule_overrides_an_allow_default():
    reasons = [{"action": "deny"}, {"action": "review"}]

    assert _finalize_disposition("allow", reasons, 2) == "deny"


def _signed_receipt(*, namespace: str, decision_id):
    private_key = base64.b64encode(bytes(range(32))).decode("ascii")
    recorded_at = "2026-08-02T12:00:00+00:00"
    receipt = build_decision_receipt(
        decision={
            "id": str(decision_id),
            "namespace": namespace,
            "agent_id": "underwriter",
            "recorded_by_principal_ref": (
                "lians:principal:v1:api-key:11111111-1111-4111-8111-111111111111"
            ),
            "recorded_by_auth_method": "api_key",
            "recorded_by_credential_ref": (
                "lians:credential:v1:sha256:" + "4" * 64
            ),
            "decision_type": "credit-decision",
            "outcome": "decline",
            "reason_codes": ["DTI_HIGH"],
            "regime": "synthetic",
            "subject_id": "subject-1",
            "session_id": "session-1",
            "model_id": "risk-model",
            "model_version": "1.0",
            "policy_version": "credit-v4",
            "decided_at": recorded_at,
            "recorded_at": recorded_at,
            "knowledge_as_of": recorded_at,
            "knowledge_recorded_as_of": recorded_at,
            "record_hash": "1" * 64,
            "record_hash_version": 2,
            "record_integrity_status": "verified",
            "supersedes_id": None,
            "input_hash": "2" * 64,
            "output_hash": "3" * 64,
            "human_review_status": "not_requested",
            "human_reviewer": None,
            "human_reviewed_at": None,
            "metadata": {},
        },
        knowledge_snapshot=[],
        cited_evidence=[],
        audit_chain={"status": "ok"},
        signing_private_key=private_key,
        signing_key_id="receipt-key-v1",
    )
    return receipt, receipt_signing_public_key(private_key)


def _receipt_request(document, decision_id) -> GateEvaluationRequest:
    return GateEvaluationRequest(
        action="issue-notice",
        target_ref="urn:lians:test:notice",
        decision_id=decision_id,
        enforcement_principal_id=MEDIATOR,
        permit_ttl_seconds=30,
        execution_request_hash="b" * 64,
        decision_type="credit-decision",
        attached_policy_version="credit-v4",
        policy_name="production",
        receipt={
            "key_id": "receipt-key-v1",
            "document": document,
        },
    )


def test_gate_binds_a_valid_receipt_to_namespace_and_decision():
    decision_id = uuid4()
    receipt, public_key = _signed_receipt(
        namespace="tenant-a", decision_id=decision_id
    )

    _, status = _verify_receipt_context(
        _receipt_request(receipt, decision_id),
        {
            "registry_trusted": True,
            "_public_key": public_key,
            "issuer": "Lians",
        },
        namespace="tenant-a",
    )

    assert status["trusted"] is True
    assert status["reason"] == "verified_signed_receipt"


@pytest.mark.parametrize("mismatch", ["namespace", "decision"])
def test_gate_rejects_a_valid_receipt_for_another_boundary(mismatch: str):
    decision_id = uuid4()
    receipt, public_key = _signed_receipt(
        namespace="tenant-a", decision_id=decision_id
    )
    request_decision_id = uuid4() if mismatch == "decision" else decision_id
    namespace = "tenant-b" if mismatch == "namespace" else "tenant-a"

    _, status = _verify_receipt_context(
        _receipt_request(receipt, request_decision_id),
        {
            "registry_trusted": True,
            "_public_key": public_key,
            "issuer": "Lians",
        },
        namespace=namespace,
    )

    assert status["trusted"] is False
    assert status["reason"] == "receipt_context_binding_failed"
    assert any(
        mismatch in error for error in status["receipt_verification_errors"]
    )
