"""Core control-plane logic shared by HTTP routes and future protocol adapters."""

from __future__ import annotations

import base64
import binascii
import hashlib
import hmac
import json
import secrets
import uuid
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import or_, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from .control_models import (
    ControlClosureAttestation,
    GateDecisionRecord,
    GateExecutionPermit,
    GateExecutionPermitConsumption,
    GatePolicyRule,
    GatePolicySet,
    ReceiptIssuer,
    TrustedReceiptKey,
)
from .control_schemas import (
    ClosureAttestationCreate,
    ClosureAttestationOut,
    GateApproval,
    GateEvaluationRequest,
    GateExecutionPermitConsume,
)
from .decision_receipt import verify_decision_receipt
from .immutable_attestation_service import resolve_gate_approvals
from .secret_storage import (
    CONTROL_CLOSURE_STATEMENT_PURPOSE,
    seal_text,
    unseal_text,
)

GRADE_RANK = {"F": 0, "D": 1, "C": 2, "B": 3, "A": 4}
DISPOSITION_RANK = {"allow": 0, "review": 1, "deny": 2}
PERMIT_TOKEN_PREFIX = "lians_permit_v1_"
PERMIT_TOKEN_SUFFIX_LENGTH = 43
_TARGET_SELECTOR_BOUNDARIES = frozenset("/:#?")

CASE_TRANSITIONS: dict[str, set[str]] = {
    "open": {"in_review", "remediating", "resolved"},
    "in_review": {"open", "remediating", "resolved"},
    "remediating": {"in_review", "resolved"},
    "resolved": {"open", "in_review", "remediating"},
}
TASK_TRANSITIONS: dict[str, set[str]] = {
    "pending": {"in_progress", "blocked", "cancelled"},
    "in_progress": {"pending", "blocked", "cancelled"},
    "blocked": {"pending", "in_progress", "cancelled"},
    "cancelled": {"pending"},
}


def utc_now() -> datetime:
    return datetime.now(UTC)


def _utc(value: datetime) -> datetime:
    """Normalize SQLite-naive and PostgreSQL-aware timestamps for comparisons."""
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def canonical_json(value: Any) -> str:
    """Stable JSON representation for policy, request, verdict, and attestation hashes."""
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
        default=str,
    )


def sha256_json(value: Any) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def target_selector_matches(selector: str, target_ref: str) -> bool:
    """Match an exact URI or an explicitly boundary-terminated URI prefix."""
    return selector == target_ref or (
        selector[-1:] in _TARGET_SELECTOR_BOUNDARIES
        and target_ref.startswith(selector)
    )


@dataclass(frozen=True, repr=False)
class IssuedGateExecutionPermit:
    """In-memory capability returned once; its representation never includes the token."""

    row: GateExecutionPermit
    token: str


class GatePermitRedemptionError(ValueError):
    """Uniform fail-closed error that does not disclose which permit check failed."""

    _OUTCOMES = frozenset({"rejected", "expired", "replayed", "mismatched"})

    def __init__(self, outcome: str) -> None:
        self.outcome = outcome if outcome in self._OUTCOMES else "rejected"
        super().__init__("Execution permit is invalid or unusable")


def _permit_token() -> str:
    # 256 random bits, URL-safe alphabet, and no whitespace/control characters.
    return f"{PERMIT_TOKEN_PREFIX}{secrets.token_urlsafe(32)}"


def _token_digest(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def _permit_token_shape_valid(token: str) -> bool:
    """Validate the fixed v1 wire shape without ever echoing token material."""
    if len(token) != len(PERMIT_TOKEN_PREFIX) + PERMIT_TOKEN_SUFFIX_LENGTH:
        return False
    if not token.startswith(PERMIT_TOKEN_PREFIX):
        return False
    suffix = token[len(PERMIT_TOKEN_PREFIX) :]
    return suffix.isascii() and all(
        character.isalnum() or character in "-_" for character in suffix
    )


def _permit_grant_payload(row: GateExecutionPermit) -> dict[str, Any]:
    return {
        "schema": "lians.gate-execution-permit.v1",
        "id": str(row.id),
        "namespace": row.namespace,
        "barrier_group": row.barrier_group,
        "evaluation_id": str(row.evaluation_id),
        "policy_set_id": str(row.policy_set_id),
        "decision_id": str(row.decision_id),
        "enforcement_principal_id": row.enforcement_principal_id,
        "action": row.action,
        "target_ref": row.target_ref,
        "execution_request_hash": row.execution_request_hash,
        "token_digest": row.token_digest,
        "issued_at": _utc(row.issued_at).isoformat(),
        "expires_at": _utc(row.expires_at).isoformat(),
    }


def _permit_consumption_payload(
    row: GateExecutionPermitConsumption,
) -> dict[str, Any]:
    return {
        "schema": "lians.gate-execution-permit-consumption.v1",
        "id": str(row.id),
        "namespace": row.namespace,
        "barrier_group": row.barrier_group,
        "permit_id": str(row.permit_id),
        "evaluation_id": str(row.evaluation_id),
        "policy_set_id": str(row.policy_set_id),
        "decision_id": str(row.decision_id),
        "consuming_principal_id": row.consuming_principal_id,
        "action": row.action,
        "target_ref": row.target_ref,
        "execution_request_hash": row.execution_request_hash,
        "grant_hash": row.grant_hash,
        "token_digest": row.token_digest,
        "consumed_at": _utc(row.consumed_at).isoformat(),
    }


def _closure_statement_context(row: ControlClosureAttestation) -> str:
    return f"{row.namespace}:{row.id}:{row.resource_type}:{row.resource_id}"


def closure_statement(row: ControlClosureAttestation) -> str:
    """Resolve a closure statement without exposing its storage representation."""
    if row.statement_encrypted:
        return unseal_text(
            row.statement_encrypted,
            purpose=CONTROL_CLOSURE_STATEMENT_PURPOSE,
            context=_closure_statement_context(row),
        )
    if row.statement is None:
        raise ValueError("Closure attestation has no protected or legacy statement")
    return row.statement


def closure_attestation_payload(row: ControlClosureAttestation) -> dict[str, Any]:
    """Return the versioned immutable payload covered by ``attestation_hash``."""
    common = {
        "id": str(row.id),
        "namespace": row.namespace,
        "barrier_group": row.barrier_group,
        "resource_type": row.resource_type,
        "resource_id": str(row.resource_id),
        "attested_by": row.attested_by,
        "evidence_refs": sorted(row.evidence_refs or []),
        "decision_id": str(row.decision_id) if row.decision_id else None,
        "change_event_id": str(row.change_event_id) if row.change_event_id else None,
        "attested_at": _utc(row.attested_at).isoformat(),
    }
    if int(row.hash_version or 1) == 1:
        # Preserve the exact v1 payload so protecting an old plaintext row does
        # not rewrite its original immutable attestation hash.
        return {**common, "statement": closure_statement(row)}
    return {
        "schema": "lians.control-closure-attestation.v2",
        **common,
        "statement_hash": row.statement_hash,
    }


def verify_closure_attestation_integrity(row: ControlClosureAttestation) -> bool:
    try:
        return row.attestation_hash == sha256_json(closure_attestation_payload(row))
    except (TypeError, ValueError):
        return False


def closure_attestation_out(
    row: ControlClosureAttestation, *, include_statement: bool = False
) -> ClosureAttestationOut:
    statement = closure_statement(row) if include_statement else None
    statement_hash = row.statement_hash
    if statement_hash is None:
        # Read compatibility for v1 rows created before statement hashes were
        # stored. This does not mutate the append-only row.
        statement_hash = hashlib.sha256(closure_statement(row).encode("utf-8")).hexdigest()
    return ClosureAttestationOut(
        id=row.id,
        namespace=row.namespace,
        barrier_group=row.barrier_group,
        resource_type=row.resource_type,
        resource_id=row.resource_id,
        attested_by=row.attested_by,
        statement=statement,
        statement_hash=statement_hash,
        hash_version=int(row.hash_version or 1),
        evidence_refs=list(row.evidence_refs or []),
        decision_id=row.decision_id,
        change_event_id=row.change_event_id,
        attestation_hash=row.attestation_hash,
        attested_at=row.attested_at,
    )


def normalize_ed25519_public_key(value: str) -> tuple[str, str]:
    """Validate and normalize a raw Ed25519 public key.

    The registry deliberately accepts only public verification material. The
    canonical stored form is base64 and the returned fingerprint is SHA-256 of
    the raw 32 bytes.
    """
    candidate = value.strip()
    raw = b""
    if len(candidate) == 64:
        try:
            raw = bytes.fromhex(candidate)
        except ValueError:
            raw = b""
    if not raw:
        try:
            raw = base64.b64decode(candidate, validate=True)
        except (binascii.Error, ValueError) as exc:
            raise ValueError(
                "Ed25519 public_key must be raw 32-byte base64 or hexadecimal"
            ) from exc
    if len(raw) != 32:
        raise ValueError("Ed25519 public_key must decode to exactly 32 bytes")
    normalized = base64.b64encode(raw).decode("ascii")
    return normalized, hashlib.sha256(raw).hexdigest()


def effective_barrier(requested: str | None, caller_barrier: str | None) -> str | None:
    """Return a safe barrier for a new record or reject a cross-wall write."""
    if caller_barrier is None:
        return requested
    if requested is not None and requested != caller_barrier:
        raise PermissionError("Cannot create or evaluate a record across information barriers")
    return caller_barrier


def barrier_visible(row_barrier: str | None, caller_barrier: str | None) -> bool:
    return caller_barrier is None or row_barrier is None or row_barrier == caller_barrier


def add_barrier_filter(filters: list[Any], column, caller_barrier: str | None) -> None:
    if caller_barrier is not None:
        filters.append(or_(column.is_(None), column == caller_barrier))


def validate_transition(
    current: str,
    requested: str,
    transitions: dict[str, set[str]],
    resource: str,
) -> None:
    if current == requested:
        return
    if requested not in transitions.get(current, set()):
        raise ValueError(f"Invalid {resource} status transition: {current} -> {requested}")


def policy_definition_payload(body, barrier_group: str | None) -> dict[str, Any]:
    """Hash-covered semantic policy definition; author identity is audit metadata."""
    dumped = body.model_dump(mode="json")
    dumped.pop("actor_id", None)
    return {
        "name": dumped["name"],
        "version": dumped["version"],
        "description": dumped.get("description"),
        "barrier_group": barrier_group,
        "default_disposition": dumped["default_disposition"],
        "protected_actions": sorted(dumped["protected_actions"]),
        "target_ref_prefixes": sorted(dumped["target_ref_prefixes"]),
        "enforcement_principal_ids": sorted(dumped["enforcement_principal_ids"]),
        "maximum_permit_ttl_seconds": dumped["maximum_permit_ttl_seconds"],
        "rules": sorted(dumped["rules"], key=lambda rule: (rule["priority"], rule["name"])),
        "metadata": dumped.get("metadata") or {},
    }


def rule_snapshot(rule: GatePolicyRule) -> dict[str, Any]:
    return {
        "id": str(rule.id),
        "name": rule.name,
        "description": rule.description,
        "priority": rule.priority,
        "enabled": rule.enabled,
        "action_on_failure": rule.action_on_failure,
        "applies_to_decision_types": list(rule.applies_to_decision_types or []),
        "applies_to_risk_levels": list(rule.applies_to_risk_levels or []),
        "required_receipt_grade": rule.required_receipt_grade,
        "require_trusted_issuer": rule.require_trusted_issuer,
        "require_sources_current": rule.require_sources_current,
        "require_policy_attached": rule.require_policy_attached,
        "required_principal_scopes": list(rule.required_principal_scopes or []),
        "minimum_approval_count": rule.minimum_approval_count,
        "required_approval_roles": list(rule.required_approval_roles or []),
        "allowed_approval_principal_types": list(
            rule.allowed_approval_principal_types or []
        ),
        "maximum_approval_age_seconds": rule.maximum_approval_age_seconds,
        "require_information_barrier_match": rule.require_information_barrier_match,
        "block_untrusted_content": rule.block_untrusted_content,
        "max_untrusted_content_score": rule.max_untrusted_content_score,
    }


def _applies(rule: GatePolicyRule, request: GateEvaluationRequest) -> bool:
    decision_types = set(rule.applies_to_decision_types or [])
    risk_levels = set(rule.applies_to_risk_levels or [])
    if decision_types and request.decision_type not in decision_types:
        return False
    if risk_levels and request.risk_level not in risk_levels:
        return False
    return bool(rule.enabled)


def _barriers_match(principal: str | None, target: str | None) -> bool:
    # Unbarriered principals are compliance/cross-wall principals. NULL targets
    # are shared records. Scoped principals can otherwise enter only their wall.
    return principal is None or target is None or principal == target


def _failure(
    rule: GatePolicyRule,
    code: str,
    message: str,
    expected: Any,
    actual: Any,
) -> dict[str, Any]:
    return {
        "code": code,
        "message": message,
        "rule_id": str(rule.id),
        "rule_name": rule.name,
        "action": rule.action_on_failure,
        "expected": expected,
        "actual": actual,
    }


async def receipt_registry_status(
    db: AsyncSession,
    *,
    namespace: str,
    caller_barrier: str | None,
    issuer_id,
    key_id: str | None,
    at: datetime,
) -> dict[str, Any]:
    """Resolve whether a receipt key is trusted at evaluation time."""
    if issuer_id is None or not key_id:
        return {
            "trusted": False,
            "registry_trusted": False,
            "receipt_verified": False,
            "reason": "issuer_or_key_missing",
        }

    filters: list[Any] = [
        TrustedReceiptKey.namespace == namespace,
        TrustedReceiptKey.issuer_id == issuer_id,
        TrustedReceiptKey.key_id == key_id,
    ]
    add_barrier_filter(filters, TrustedReceiptKey.barrier_group, caller_barrier)
    result = await db.execute(select(TrustedReceiptKey).where(*filters))
    key = result.scalar_one_or_none()
    if key is None:
        return {
            "trusted": False,
            "registry_trusted": False,
            "receipt_verified": False,
            "reason": "key_not_registered",
        }

    issuer_filters: list[Any] = [
        ReceiptIssuer.id == issuer_id,
        ReceiptIssuer.namespace == namespace,
    ]
    add_barrier_filter(issuer_filters, ReceiptIssuer.barrier_group, caller_barrier)
    issuer_result = await db.execute(select(ReceiptIssuer).where(*issuer_filters))
    issuer = issuer_result.scalar_one_or_none()
    if issuer is None:
        return {
            "trusted": False,
            "registry_trusted": False,
            "receipt_verified": False,
            "reason": "issuer_not_registered",
        }
    if issuer.status != "active":
        return {
            "trusted": False,
            "registry_trusted": False,
            "receipt_verified": False,
            "reason": "issuer_revoked",
            "issuer": issuer.name,
        }
    if key.status != "active":
        return {
            "trusted": False,
            "registry_trusted": False,
            "receipt_verified": False,
            "reason": "key_revoked",
            "issuer": issuer.name,
            "fingerprint_sha256": key.fingerprint_sha256,
        }
    if _utc(key.valid_from) > _utc(at):
        return {
            "trusted": False,
            "registry_trusted": False,
            "receipt_verified": False,
            "reason": "key_not_yet_valid",
            "issuer": issuer.name,
        }
    if key.valid_until is not None and _utc(key.valid_until) < _utc(at):
        return {
            "trusted": False,
            "registry_trusted": False,
            "receipt_verified": False,
            "reason": "key_expired",
            "issuer": issuer.name,
        }
    return {
        # Key registration alone is not receipt authentication. The private
        # field is consumed below and removed before the immutable snapshot.
        "trusted": False,
        "registry_trusted": True,
        "receipt_verified": False,
        "reason": "receipt_document_missing",
        "issuer": issuer.name,
        "issuer_id": str(issuer.id),
        "key_id": key.key_id,
        "algorithm": key.algorithm,
        "fingerprint_sha256": key.fingerprint_sha256,
        "_public_key": key.public_key,
    }


def _verify_receipt_context(
    request: GateEvaluationRequest,
    status: dict[str, Any],
    *,
    namespace: str,
):
    """Authenticate a receipt envelope and derive its hash/grade from protected data."""
    public_key = status.pop("_public_key", None)
    document = request.receipt.document
    if not status.get("registry_trusted") or public_key is None:
        return request, status
    if document is None:
        return request, status

    report = verify_decision_receipt(
        document,
        trusted_public_key=public_key,
        require_signature=True,
    )
    expected_hash = report.get("receipt_hash")
    document_issuer = document.get("issuer") if isinstance(document, dict) else None
    document_key_id = (
        document_issuer.get("key_id") if isinstance(document_issuer, dict) else None
    )
    document_issuer_name = (
        document_issuer.get("name") if isinstance(document_issuer, dict) else None
    )
    document_decision = document.get("decision") if isinstance(document, dict) else None
    document_decision_id = (
        document_decision.get("id") if isinstance(document_decision, dict) else None
    )
    document_namespace = (
        document_decision.get("namespace")
        if isinstance(document_decision, dict)
        else None
    )
    document_decision_type = (
        document_decision.get("type")
        if isinstance(document_decision, dict)
        else None
    )
    document_policy = document.get("policy") if isinstance(document, dict) else None
    document_policy_version = (
        document_policy.get("version") if isinstance(document_policy, dict) else None
    )
    completeness = document.get("completeness") if isinstance(document, dict) else None
    document_grade = completeness.get("grade") if isinstance(completeness, dict) else None
    context_errors: list[str] = []
    if (
        request.receipt.receipt_hash is not None
        and request.receipt.receipt_hash != expected_hash
    ):
        context_errors.append("declared receipt hash does not match the signed envelope")
    if document_key_id != request.receipt.key_id:
        context_errors.append("receipt key ID does not match the selected trust key")
    if document_issuer_name not in {None, status.get("issuer")}:
        context_errors.append("receipt issuer name does not match the selected issuer")
    if document_namespace != namespace:
        context_errors.append("receipt namespace does not match the authenticated namespace")
    if request.decision_id is not None and document_decision_id != str(request.decision_id):
        context_errors.append("receipt decision ID does not match the evaluated decision")
    if (
        request.decision_type is not None
        and document_decision_type != request.decision_type
    ):
        context_errors.append("receipt decision type does not match the evaluated decision")
    if (
        request.attached_policy_version is not None
        and document_policy_version != request.attached_policy_version
    ):
        context_errors.append("receipt policy version does not match the evaluated decision")
    if document_grade not in GRADE_RANK:
        context_errors.append("receipt completeness grade is invalid")
    elif request.receipt.grade is not None and request.receipt.grade != document_grade:
        context_errors.append("declared receipt grade does not match the signed envelope")
    context_matches = not context_errors
    verified = bool(report.get("valid") and context_matches and expected_hash)
    status.update(
        {
            "trusted": verified,
            "receipt_verified": verified,
            "reason": (
                "verified_signed_receipt"
                if verified
                else (
                    "receipt_context_binding_failed"
                    if report.get("valid") and context_errors
                    else "receipt_cryptographic_verification_failed"
                )
            ),
            "receipt_hash": expected_hash,
            "receipt_verification_errors": [
                *list(report.get("errors") or []),
                *context_errors,
            ],
        }
    )
    if not verified:
        return request, status
    receipt = request.receipt.model_copy(
        update={"receipt_hash": expected_hash, "grade": document_grade}
    )
    return request.model_copy(
        update={
            "receipt": receipt,
            "decision_type": document_decision_type or request.decision_type,
            "attached_policy_version": (
                document_policy_version or request.attached_policy_version
            ),
        }
    ), status


def _evaluate_rule(
    rule: GatePolicyRule,
    request: GateEvaluationRequest,
    registry_status: dict[str, Any],
    approvals: list[GateApproval],
    evaluated_at: datetime,
) -> list[dict[str, Any]]:
    failures: list[dict[str, Any]] = []
    receipt = request.receipt

    required_grade = rule.required_receipt_grade
    actual_grade = receipt.grade
    if required_grade is not None and not registry_status.get("receipt_verified"):
        failures.append(
            _failure(
                rule,
                "receipt.grade_unverified",
                "Receipt grade must come from a cryptographically verified envelope",
                "verified signed receipt",
                registry_status.get("reason"),
            )
        )
    if required_grade is not None and (
        actual_grade is None or GRADE_RANK[actual_grade] < GRADE_RANK[required_grade]
    ):
        failures.append(
            _failure(
                rule,
                "receipt.grade_below_required",
                "Decision Receipt completeness grade is below the policy minimum",
                required_grade,
                actual_grade,
            )
        )
    if rule.require_trusted_issuer and not registry_status.get("trusted"):
        failures.append(
            _failure(
                rule,
                "receipt.issuer_untrusted",
                "Receipt issuer/key is not active in the trusted-key registry",
                "active_trusted_key",
                registry_status.get("reason"),
            )
        )
    if rule.require_sources_current and request.sources_current is not True:
        failures.append(
            _failure(
                rule,
                "sources.not_current",
                "All decision sources must be current at execution time",
                True,
                request.sources_current,
            )
        )
    if rule.require_policy_attached and not request.attached_policy_version:
        failures.append(
            _failure(
                rule,
                "policy.not_attached",
                "A versioned runtime policy must be attached to the action",
                "non-empty policy version",
                request.attached_policy_version,
            )
        )

    required_scopes = set(rule.required_principal_scopes or [])
    missing_scopes = sorted(required_scopes - set(request.principal_scopes))
    if missing_scopes:
        failures.append(
            _failure(
                rule,
                "principal.scopes_missing",
                "Principal lacks scopes required for this action",
                sorted(required_scopes),
                sorted(request.principal_scopes),
            )
        )

    # These are resolved from immutable IDs after namespace, barrier, context,
    # expiry, supersession, and integrity checks.  No body-supplied role or
    # approval count reaches this evaluator.
    approved = [approval for approval in approvals if approval.status == "approved"]
    allowed_principal_types = set(rule.allowed_approval_principal_types or [])
    eligible = [
        approval
        for approval in approved
        if not allowed_principal_types
        or approval.principal_type in allowed_principal_types
    ]
    maximum_age = rule.maximum_approval_age_seconds
    if maximum_age is not None:
        cutoff = _utc(evaluated_at).timestamp() - maximum_age
        eligible = [
            approval
            for approval in eligible
            if approval.attested_at is not None
            and _utc(approval.attested_at).timestamp() >= cutoff
        ]
    if len(eligible) < rule.minimum_approval_count:
        failures.append(
            _failure(
                rule,
                "approvals.count_below_required",
                "The action has not reached its eligible approval threshold",
                {
                    "minimum_count": rule.minimum_approval_count,
                    "allowed_principal_types": sorted(allowed_principal_types),
                    "maximum_age_seconds": maximum_age,
                },
                {"eligible_count": len(eligible), "submitted_count": len(approved)},
            )
        )
    required_roles = set(rule.required_approval_roles or [])
    approved_roles = {approval.role for approval in eligible}
    missing_roles = sorted(required_roles - approved_roles)
    if missing_roles:
        failures.append(
            _failure(
                rule,
                "approvals.roles_missing",
                "Required approval roles have not attested",
                sorted(required_roles),
                sorted(approved_roles),
            )
        )

    if rule.require_information_barrier_match and not _barriers_match(
        request.principal_barrier_group, request.target_barrier_group
    ):
        failures.append(
            _failure(
                rule,
                "barrier.mismatch",
                "Principal and target are separated by an information barrier",
                request.target_barrier_group,
                request.principal_barrier_group,
            )
        )

    untrusted = [signal for signal in request.untrusted_content_signals if not signal.trusted]
    if rule.block_untrusted_content and untrusted:
        failures.append(
            _failure(
                rule,
                "content.untrusted",
                "Untrusted content signals are present in the action context",
                0,
                len(untrusted),
            )
        )
    if rule.max_untrusted_content_score is not None:
        max_score = max((signal.score for signal in untrusted), default=0)
        if max_score > rule.max_untrusted_content_score:
            failures.append(
                _failure(
                    rule,
                    "content.risk_score_exceeded",
                    "Untrusted-content risk exceeds the policy threshold",
                    rule.max_untrusted_content_score,
                    max_score,
                )
            )
    return failures


def _finalize_disposition(
    default_disposition: str,
    reasons: list[dict[str, Any]],
    matched_rule_count: int,
) -> str:
    """Resolve failures, explicit rule satisfaction, and the no-match default."""
    if reasons:
        return max(
            (reason["action"] for reason in reasons),
            key=lambda value: DISPOSITION_RANK[value],
        )
    if matched_rule_count:
        return "allow"
    if default_disposition != "allow":
        reasons.append(
            {
                "code": "policy.default_disposition",
                "message": "No rule matched the action, so the restrictive default applies",
                "rule_id": None,
                "rule_name": None,
                "action": default_disposition,
                "expected": "at least one applicable rule",
                "actual": "no rule matched",
            }
        )
    return default_disposition


async def evaluate_gate(
    db: AsyncSession,
    *,
    namespace: str,
    barrier_group: str | None,
    policy: GatePolicySet,
    rules: Iterable[GatePolicyRule],
    request: GateEvaluationRequest,
) -> tuple[GateDecisionRecord, IssuedGateExecutionPermit | None]:
    """Append one verdict and, only for allow, one atomic one-time permit."""
    policy_covers_request = (
        policy.namespace == namespace
        and (policy.barrier_group is None or policy.barrier_group == barrier_group)
        and request.action in set(policy.protected_actions or [])
        and any(
            target_selector_matches(selector, request.target_ref)
            for selector in policy.target_ref_prefixes or []
        )
    )
    if policy.status != "active" or not policy_covers_request:
        raise ValueError(
            "Gate evaluation requires an active policy in the request boundary that "
            "authoritatively covers the exact action and target"
        )
    if not request.principal_id:
        raise ValueError("Gate evaluation requires an authenticated principal_id")
    if request.enforcement_principal_id == request.principal_id:
        raise ValueError(
            "Gate evaluator and enforcement mediator must be separate identities"
        )
    enforcement_principals = set(policy.enforcement_principal_ids or [])
    if request.enforcement_principal_id not in enforcement_principals:
        raise ValueError(
            "Requested enforcement principal is not authorized by the immutable policy"
        )
    maximum_ttl = int(policy.maximum_permit_ttl_seconds or 0)
    if not 1 <= request.permit_ttl_seconds <= maximum_ttl <= 300:
        raise ValueError(
            "Requested execution-permit TTL exceeds the immutable policy maximum"
        )
    now = utc_now()
    registry_status = await receipt_registry_status(
        db,
        namespace=namespace,
        caller_barrier=barrier_group,
        issuer_id=request.receipt.issuer_id,
        key_id=request.receipt.key_id,
        at=now,
    )
    request, registry_status = _verify_receipt_context(
        request, registry_status, namespace=namespace
    )
    approvals, approval_snapshot = await resolve_gate_approvals(
        db,
        namespace=namespace,
        barrier_group=barrier_group,
        policy=policy,
        request=request,
        at=now,
    )

    reasons: list[dict[str, Any]] = []
    applied_rules: list[dict[str, Any]] = []
    matched_rule_count = 0
    for rule in sorted(rules, key=lambda item: (item.priority, item.name)):
        snapshot = rule_snapshot(rule)
        matched = _applies(rule, request)
        if matched:
            matched_rule_count += 1
        failures = (
            _evaluate_rule(rule, request, registry_status, approvals, now)
            if matched
            else []
        )
        reasons.extend(failures)
        applied_rules.append(
            {
                **snapshot,
                "matched": matched,
                "failure_codes": [failure["code"] for failure in failures],
            }
        )

    # A restrictive default is the no-match posture. Once one or more explicitly
    # applicable rules all pass, the action is allowed. Any failure still wins
    # with the strictest configured deny/review disposition.
    disposition = _finalize_disposition(
        policy.default_disposition, reasons, matched_rule_count
    )

    input_snapshot = request.model_dump(mode="json")
    input_snapshot["resolved_approvals"] = approval_snapshot
    if input_snapshot.get("receipt", {}).get("document") is not None:
        input_snapshot["receipt"]["document"] = {
            "$captured": "verified_hash_reference",
            "$sha256": request.receipt.receipt_hash,
        }
    input_snapshot["receipt_registry"] = registry_status
    request_hash = sha256_json(input_snapshot)
    record_id = uuid.uuid4()
    evaluation_payload = {
        "id": str(record_id),
        "namespace": namespace,
        "barrier_group": barrier_group,
        "policy_set_id": str(policy.id),
        "policy_hash": policy.policy_hash,
        "action": request.action,
        "target_ref": request.target_ref,
        "decision_id": str(request.decision_id),
        "enforcement_principal_id": request.enforcement_principal_id,
        "execution_request_hash": request.execution_request_hash,
        "request_hash": request_hash,
        "disposition": disposition,
        "reasons": reasons,
        "applied_rules": applied_rules,
        "evaluated_at": now.isoformat(),
    }
    row = GateDecisionRecord(
        id=record_id,
        namespace=namespace,
        barrier_group=barrier_group,
        policy_set_id=policy.id,
        policy_name=policy.name,
        policy_version=policy.version,
        policy_hash=policy.policy_hash,
        principal_id=request.principal_id,
        action=request.action,
        target_ref=request.target_ref,
        enforcement_principal_id=request.enforcement_principal_id,
        execution_request_hash=request.execution_request_hash,
        decision_id=request.decision_id,
        change_event_id=request.change_event_id,
        receipt_hash=request.receipt.receipt_hash,
        disposition=disposition,
        reasons=reasons,
        applied_rules=applied_rules,
        input_snapshot=input_snapshot,
        request_hash=request_hash,
        evaluation_hash=sha256_json(evaluation_payload),
        evaluated_at=now,
    )
    db.add(row)
    await db.flush()
    if disposition != "allow":
        return row, None

    # Issuance shares the evaluation transaction.  A unique evaluation_id on
    # the grant table makes this exactly-once even under retries or races; any
    # grant failure aborts the verdict transaction rather than returning a
    # cooperative/advisory allow without an enforceable capability.
    raw_token = _permit_token()
    permit_id = uuid.uuid4()
    permit = GateExecutionPermit(
        id=permit_id,
        namespace=namespace,
        barrier_group=barrier_group,
        evaluation_id=row.id,
        policy_set_id=policy.id,
        decision_id=request.decision_id,
        enforcement_principal_id=request.enforcement_principal_id,
        action=request.action,
        target_ref=request.target_ref,
        execution_request_hash=request.execution_request_hash,
        token_digest=_token_digest(raw_token),
        issued_at=now,
        expires_at=now + timedelta(seconds=request.permit_ttl_seconds),
        grant_hash="",
    )
    permit.grant_hash = sha256_json(_permit_grant_payload(permit))
    db.add(permit)
    await db.flush()
    return row, IssuedGateExecutionPermit(row=permit, token=raw_token)


def _constant_text_equal(left: str | None, right: str) -> bool:
    """Constant-time comparison with a fixed dummy value for missing records."""
    right_bytes = right.encode("utf-8")
    expected = (
        left.encode("utf-8")
        if left is not None
        else (b"0" * max(64, len(right_bytes)))
    )
    return hmac.compare_digest(expected, right_bytes) and left is not None


async def consume_gate_execution_permit(
    db: AsyncSession,
    *,
    namespace: str,
    caller_barrier: str | None,
    principal_id: str,
    body: GateExecutionPermitConsume,
) -> GateExecutionPermitConsumption:
    """Redeem one exact capability and append its immutable consumption proof.

    All invalid, expired, replayed, or claim-mismatched permits raise the same
    error so this endpoint cannot be used as a token or claim oracle.
    """
    if db.get_bind().dialect.name == "postgresql":
        await db.execute(
            text("SELECT pg_advisory_xact_lock(hashtextextended(:lock_key, 0))"),
            {"lock_key": f"lians:gate-permit:{body.permit_id}"},
        )

    permit_filters = [
        GateExecutionPermit.id == body.permit_id,
        GateExecutionPermit.namespace == namespace,
    ]
    add_barrier_filter(
        permit_filters, GateExecutionPermit.barrier_group, caller_barrier
    )
    result = await db.execute(
        select(GateExecutionPermit).where(*permit_filters).with_for_update()
    )
    permit = result.scalar_one_or_none()
    presented_token = body.token.get_secret_value()
    token_shape_valid = _permit_token_shape_valid(presented_token)
    # Hash only a bounded value. Malformed or oversized strings use a fixed
    # dummy, then continue through the same non-oracular lookup path.
    digest_input = (
        presented_token
        if token_shape_valid
        else f"{PERMIT_TOKEN_PREFIX}{'0' * PERMIT_TOKEN_SUFFIX_LENGTH}"
    )
    presented_digest = _token_digest(digest_input)
    digest_matches = _constant_text_equal(
        permit.token_digest if permit is not None else None, presented_digest
    )
    digest_valid = token_shape_valid and digest_matches
    # Keep the database-query shape stable even for an unknown permit ID so the
    # endpoint does not become a practical existence oracle through timing.
    evaluation_id = permit.evaluation_id if permit is not None else uuid.UUID(int=0)
    evaluation_result = await db.execute(
        select(GateDecisionRecord)
        .where(
            GateDecisionRecord.id == evaluation_id,
            GateDecisionRecord.namespace == namespace,
        )
        .with_for_update()
    )
    evaluation = evaluation_result.scalar_one_or_none()
    consumed_result = await db.execute(
        select(GateExecutionPermitConsumption.id).where(
            GateExecutionPermitConsumption.permit_id == body.permit_id
        )
    )
    already_consumed = consumed_result.scalar_one_or_none() is not None
    now = utc_now()
    if permit is None:
        raise GatePermitRedemptionError("rejected")

    request_bindings_match = all(
        (
            _constant_text_equal(permit.enforcement_principal_id, principal_id),
            _constant_text_equal(permit.action, body.action),
            _constant_text_equal(permit.target_ref, body.target_ref),
            permit.decision_id == body.decision_id,
            _constant_text_equal(
                permit.execution_request_hash, body.execution_request_hash
            ),
        )
    )
    evaluation_bindings_match = bool(
        evaluation is not None
        and evaluation.disposition == "allow"
        and evaluation.namespace == permit.namespace
        and evaluation.barrier_group == permit.barrier_group
        and evaluation.policy_set_id == permit.policy_set_id
        and evaluation.decision_id == permit.decision_id
        and evaluation.action == permit.action
        and evaluation.target_ref == permit.target_ref
        and evaluation.enforcement_principal_id == permit.enforcement_principal_id
        and evaluation.execution_request_hash == permit.execution_request_hash
    )
    expired = _utc(permit.expires_at) <= _utc(now)
    if (
        not digest_valid
        or not request_bindings_match
        or not evaluation_bindings_match
        or already_consumed
        or expired
    ):
        # Only a valid capability digest may reveal a more specific outcome to
        # the internal aggregate metric. Externally every branch stays the same
        # non-oracular 403 response.
        outcome = "rejected"
        if digest_valid:
            if already_consumed:
                outcome = "replayed"
            elif expired:
                outcome = "expired"
            elif not request_bindings_match or not evaluation_bindings_match:
                outcome = "mismatched"
        raise GatePermitRedemptionError(outcome)

    consumption = GateExecutionPermitConsumption(
        id=uuid.uuid4(),
        namespace=permit.namespace,
        barrier_group=permit.barrier_group,
        permit_id=permit.id,
        evaluation_id=permit.evaluation_id,
        policy_set_id=permit.policy_set_id,
        decision_id=permit.decision_id,
        consuming_principal_id=principal_id,
        action=body.action,
        target_ref=body.target_ref,
        execution_request_hash=body.execution_request_hash,
        grant_hash=permit.grant_hash,
        token_digest=presented_digest,
        consumed_at=now,
        consumption_hash="",
    )
    consumption.consumption_hash = sha256_json(
        _permit_consumption_payload(consumption)
    )
    db.add(consumption)
    await db.flush()
    return consumption


async def create_closure_attestation(
    db: AsyncSession,
    *,
    namespace: str,
    barrier_group: str | None,
    resource_type: str,
    resource_id,
    decision_id,
    change_event_id,
    body: ClosureAttestationCreate,
) -> ControlClosureAttestation:
    if not body.actor_id:
        raise ValueError("Closure attestation requires an authenticated actor_id")
    now = utc_now()
    attestation_id = uuid.uuid4()
    statement_hash = hashlib.sha256(body.statement.encode("utf-8")).hexdigest()
    row = ControlClosureAttestation(
        id=attestation_id,
        namespace=namespace,
        barrier_group=barrier_group,
        resource_type=resource_type,
        resource_id=resource_id,
        attested_by=body.actor_id,
        statement=None,
        statement_encrypted=seal_text(
            body.statement,
            purpose=CONTROL_CLOSURE_STATEMENT_PURPOSE,
            context=f"{namespace}:{attestation_id}:{resource_type}:{resource_id}",
        ),
        statement_hash=statement_hash,
        hash_version=2,
        evidence_refs=sorted(body.evidence_refs),
        decision_id=decision_id,
        change_event_id=change_event_id,
        attestation_hash="",
        attested_at=now,
    )
    row.attestation_hash = sha256_json(closure_attestation_payload(row))
    db.add(row)
    await db.flush()
    return row
