"""Trusted construction and resolution of immutable approval attestations."""

from __future__ import annotations

import hashlib
import json
import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from .control_models import GateApprovalAttestation, GatePolicySet
from .control_schemas import (
    GateApproval,
    GateApprovalAttestationCreate,
    GateApprovalAttestationOut,
    GateApprovalAttestationSupersede,
    GateEvaluationRequest,
)
from .secret_storage import seal_text, unseal_text

GATE_APPROVAL_STATEMENT_PURPOSE = "gate-approval-attestation-statement"


class ApprovalAttestationError(ValueError):
    """A requested approval is invalid for the evaluated action boundary."""


def utc_now() -> datetime:
    return datetime.now(UTC)


def _utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def _canonical(value: Any) -> str:
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
        default=str,
    )


def _sha256_json(value: Any) -> str:
    return hashlib.sha256(_canonical(value).encode("utf-8")).hexdigest()


def principal_ref_hash(principal_id: str) -> str:
    """Pseudonymous stable reference safe for the audit-chain payload."""
    return hashlib.sha256(
        b"lians/authenticated-principal/v1\0" + principal_id.encode("utf-8")
    ).hexdigest()


def gate_approval_context_payload(
    *,
    policy: GatePolicySet,
    action: str,
    decision_id,
    change_event_id,
    target_ref: str | None,
    target_barrier_group: str | None,
    receipt_hash: str | None,
) -> dict[str, Any]:
    """The exact execution boundary that an approval authorizes."""
    return {
        "schema": "lians.gate-approval-context.v1",
        "action": action,
        "decision_id": str(decision_id) if decision_id else None,
        "change_event_id": str(change_event_id) if change_event_id else None,
        "policy_set_id": str(policy.id),
        "policy_hash": policy.policy_hash,
        "target": {
            "ref": target_ref,
            "barrier_group": target_barrier_group,
        },
        "receipt_hash": receipt_hash.lower() if receipt_hash else None,
    }


def gate_approval_context_hash(**kwargs) -> str:
    return _sha256_json(gate_approval_context_payload(**kwargs))


def _series_key(namespace: str, context_hash: str, approval_principal_id: str) -> str:
    return _sha256_json(
        {
            "schema": "lians.gate-approval-series.v1",
            "namespace": namespace,
            "context_hash": context_hash,
            "approval_principal_ref": principal_ref_hash(approval_principal_id),
        }
    )


def _statement_hash(statement: str | None) -> str | None:
    if statement is None:
        return None
    return hashlib.sha256(statement.encode("utf-8")).hexdigest()


def _statement_context(row_id, namespace: str, context_hash: str) -> str:
    return f"{namespace}:{row_id}:{context_hash}"


def approval_attestation_payload(row: GateApprovalAttestation) -> dict[str, Any]:
    """Hash-covered fields; ciphertext and plaintext are deliberately excluded."""
    return {
        "schema": "lians.gate-approval-attestation.v1",
        "id": str(row.id),
        "namespace": row.namespace,
        "barrier_group": row.barrier_group,
        "series_key": row.series_key,
        "sequence": row.sequence,
        "approval_principal_ref": principal_ref_hash(row.approval_principal_id),
        "attester_ref": principal_ref_hash(row.attested_by),
        "principal_type": row.principal_type,
        "attester_role": row.attester_role,
        "auth_method": row.auth_method,
        "credential_ref": (
            principal_ref_hash(row.credential_id) if row.credential_id else None
        ),
        "status": row.status,
        "action": row.action,
        "decision_id": str(row.decision_id) if row.decision_id else None,
        "change_event_id": str(row.change_event_id) if row.change_event_id else None,
        "policy_set_id": str(row.policy_set_id),
        "policy_hash": row.policy_hash,
        "target_ref_hash": (
            hashlib.sha256(row.target_ref.encode("utf-8")).hexdigest()
            if row.target_ref
            else None
        ),
        "target_barrier_group": row.target_barrier_group,
        "receipt_hash": row.receipt_hash,
        "context_hash": row.context_hash,
        "statement_hash": row.statement_hash,
        "evidence_refs": sorted(row.evidence_refs or []),
        "expires_at": _utc(row.expires_at).isoformat() if row.expires_at else None,
        "supersedes_id": str(row.supersedes_id) if row.supersedes_id else None,
        "prior_attestation_hash": row.prior_attestation_hash,
        "attested_at": _utc(row.attested_at).isoformat(),
    }


def verify_approval_attestation_integrity(row: GateApprovalAttestation) -> bool:
    return row.attestation_hash == _sha256_json(approval_attestation_payload(row))


async def _serialize_series(db: AsyncSession, namespace: str, series_key: str) -> None:
    if db.get_bind().dialect.name == "postgresql":
        await db.execute(
            text("SELECT pg_advisory_xact_lock(hashtextextended(:key, 0))"),
            {"key": f"lians:gate-approval:{namespace}:{series_key}"},
        )


async def create_gate_approval_attestation(
    db: AsyncSession,
    *,
    namespace: str,
    barrier_group: str | None,
    principal_id: str,
    principal_type: str | None,
    role: str,
    auth_method: str,
    credential_id: str | None,
    policy: GatePolicySet,
    body: GateApprovalAttestationCreate,
) -> GateApprovalAttestation:
    context_hash = gate_approval_context_hash(
        policy=policy,
        action=body.action,
        decision_id=body.decision_id,
        change_event_id=body.change_event_id,
        target_ref=body.target_ref,
        target_barrier_group=body.target_barrier_group,
        receipt_hash=body.receipt_hash,
    )
    series_key = _series_key(namespace, context_hash, principal_id)
    await _serialize_series(db, namespace, series_key)
    existing = (
        await db.execute(
            select(GateApprovalAttestation.id).where(
                GateApprovalAttestation.namespace == namespace,
                GateApprovalAttestation.series_key == series_key,
            ).limit(1)
        )
    ).scalar_one_or_none()
    if existing is not None:
        raise ApprovalAttestationError(
            "An attestation series already exists for this principal and context; "
            "append a superseding attestation"
        )

    row_id = uuid.uuid4()
    now = utc_now()
    statement_hash = _statement_hash(body.statement)
    row = GateApprovalAttestation(
        id=row_id,
        namespace=namespace,
        barrier_group=barrier_group,
        series_key=series_key,
        sequence=1,
        approval_principal_id=principal_id,
        attested_by=principal_id,
        principal_type=principal_type,
        attester_role=role,
        auth_method=auth_method,
        credential_id=credential_id,
        status=body.status,
        action=body.action,
        decision_id=body.decision_id,
        change_event_id=body.change_event_id,
        policy_set_id=policy.id,
        policy_hash=policy.policy_hash,
        target_ref=body.target_ref,
        target_barrier_group=body.target_barrier_group,
        receipt_hash=body.receipt_hash.lower() if body.receipt_hash else None,
        context_hash=context_hash,
        statement_hash=statement_hash,
        evidence_refs=sorted(body.evidence_refs),
        expires_at=_utc(body.expires_at) if body.expires_at else None,
        supersedes_id=None,
        prior_attestation_hash=None,
        attested_at=now,
    )
    if body.statement is not None:
        row.statement_encrypted = seal_text(
            body.statement,
            purpose=GATE_APPROVAL_STATEMENT_PURPOSE,
            context=_statement_context(row_id, namespace, context_hash),
        )
    row.attestation_hash = _sha256_json(approval_attestation_payload(row))
    db.add(row)
    await db.flush()
    return row


async def supersede_gate_approval_attestation(
    db: AsyncSession,
    *,
    prior: GateApprovalAttestation,
    actor_principal_id: str,
    actor_principal_type: str | None,
    actor_role: str,
    auth_method: str,
    credential_id: str | None,
    allow_revoke_other: bool,
    body: GateApprovalAttestationSupersede,
) -> GateApprovalAttestation:
    await _serialize_series(db, prior.namespace, prior.series_key)
    latest = (
        await db.execute(
            select(GateApprovalAttestation)
            .where(
                GateApprovalAttestation.namespace == prior.namespace,
                GateApprovalAttestation.series_key == prior.series_key,
            )
            .order_by(GateApprovalAttestation.sequence.desc())
            .limit(1)
        )
    ).scalar_one()
    if latest.id != prior.id:
        raise ApprovalAttestationError(
            f"Attestation is superseded; latest attestation is {latest.id}"
        )
    if not verify_approval_attestation_integrity(latest):
        raise ApprovalAttestationError("Prior attestation failed integrity verification")
    if latest.status == "revoked":
        raise ApprovalAttestationError(
            "A revoked approval series is terminal; attest to a new action boundary"
        )
    same_principal = latest.approval_principal_id == actor_principal_id
    if body.status == "revoked":
        if not same_principal and not allow_revoke_other:
            raise PermissionError("Only the approver or an authorized owner may revoke")
    elif not same_principal:
        raise PermissionError("Only the original approver may supersede this approval")

    row_id = uuid.uuid4()
    now = utc_now()
    statement_hash = _statement_hash(body.statement)
    row = GateApprovalAttestation(
        id=row_id,
        namespace=latest.namespace,
        barrier_group=latest.barrier_group,
        series_key=latest.series_key,
        sequence=latest.sequence + 1,
        approval_principal_id=latest.approval_principal_id,
        attested_by=actor_principal_id,
        principal_type=actor_principal_type,
        attester_role=actor_role,
        auth_method=auth_method,
        credential_id=credential_id,
        status=body.status,
        action=latest.action,
        decision_id=latest.decision_id,
        change_event_id=latest.change_event_id,
        policy_set_id=latest.policy_set_id,
        policy_hash=latest.policy_hash,
        target_ref=latest.target_ref,
        target_barrier_group=latest.target_barrier_group,
        receipt_hash=latest.receipt_hash,
        context_hash=latest.context_hash,
        statement_hash=statement_hash,
        evidence_refs=sorted(body.evidence_refs),
        expires_at=_utc(body.expires_at) if body.expires_at else None,
        supersedes_id=latest.id,
        prior_attestation_hash=latest.attestation_hash,
        attested_at=now,
    )
    if body.statement is not None:
        row.statement_encrypted = seal_text(
            body.statement,
            purpose=GATE_APPROVAL_STATEMENT_PURPOSE,
            context=_statement_context(row_id, row.namespace, row.context_hash),
        )
    row.attestation_hash = _sha256_json(approval_attestation_payload(row))
    db.add(row)
    await db.flush()
    return row


def gate_approval_out(
    row: GateApprovalAttestation, *, include_statement: bool = True
) -> GateApprovalAttestationOut:
    statement = None
    if include_statement and row.statement_encrypted:
        statement = unseal_text(
            row.statement_encrypted,
            purpose=GATE_APPROVAL_STATEMENT_PURPOSE,
            context=_statement_context(row.id, row.namespace, row.context_hash),
        )
    return GateApprovalAttestationOut(
        id=row.id,
        namespace=row.namespace,
        barrier_group=row.barrier_group,
        series_key=row.series_key,
        sequence=row.sequence,
        approval_principal_id=row.approval_principal_id,
        attested_by=row.attested_by,
        principal_type=row.principal_type,
        attester_role=row.attester_role,
        auth_method=row.auth_method,
        credential_id=row.credential_id,
        status=row.status,
        action=row.action,
        decision_id=row.decision_id,
        change_event_id=row.change_event_id,
        policy_set_id=row.policy_set_id,
        policy_hash=row.policy_hash,
        target_ref=row.target_ref,
        target_barrier_group=row.target_barrier_group,
        receipt_hash=row.receipt_hash,
        context_hash=row.context_hash,
        statement=statement,
        statement_hash=row.statement_hash,
        evidence_refs=list(row.evidence_refs or []),
        expires_at=row.expires_at,
        supersedes_id=row.supersedes_id,
        prior_attestation_hash=row.prior_attestation_hash,
        attestation_hash=row.attestation_hash,
        attested_at=row.attested_at,
    )


async def resolve_gate_approvals(
    db: AsyncSession,
    *,
    namespace: str,
    barrier_group: str | None,
    policy: GatePolicySet,
    request: GateEvaluationRequest,
    at: datetime,
) -> tuple[list[GateApproval], list[dict[str, Any]]]:
    """Resolve IDs to current, approved, context-matching trusted attestations."""
    ids = list(request.approval_ids)
    if not ids:
        return [], []
    exact_barrier = (
        GateApprovalAttestation.barrier_group.is_(None)
        if barrier_group is None
        else GateApprovalAttestation.barrier_group == barrier_group
    )
    rows = list(
        (
            await db.execute(
                select(GateApprovalAttestation).where(
                    GateApprovalAttestation.namespace == namespace,
                    GateApprovalAttestation.id.in_(ids),
                    exact_barrier,
                )
            )
        ).scalars().all()
    )
    by_id = {row.id: row for row in rows}
    if set(ids) != set(by_id):
        raise ApprovalAttestationError(
            "One or more approval IDs are absent or outside the authenticated namespace"
        )

    expected_context_hash = gate_approval_context_hash(
        policy=policy,
        action=request.action,
        decision_id=request.decision_id,
        change_event_id=request.change_event_id,
        target_ref=request.target_ref,
        target_barrier_group=request.target_barrier_group,
        receipt_hash=request.receipt.receipt_hash,
    )
    series = {row.series_key for row in rows}
    latest_sequence = dict(
        (
            await db.execute(
                select(
                    GateApprovalAttestation.series_key,
                    func.max(GateApprovalAttestation.sequence),
                )
                .where(
                    GateApprovalAttestation.namespace == namespace,
                    GateApprovalAttestation.series_key.in_(series),
                    exact_barrier,
                )
                .group_by(GateApprovalAttestation.series_key)
            )
        ).all()
    )

    approvals: list[GateApproval] = []
    snapshot: list[dict[str, Any]] = []
    seen_principals: set[str] = set()
    for approval_id in ids:
        row = by_id[approval_id]
        if row.barrier_group != barrier_group:
            raise ApprovalAttestationError(
                f"Approval {row.id} belongs to a different information barrier"
            )
        if row.context_hash != expected_context_hash:
            raise ApprovalAttestationError(
                f"Approval {row.id} does not authorize this exact action boundary"
            )
        if row.policy_set_id != policy.id or row.policy_hash != policy.policy_hash:
            raise ApprovalAttestationError(
                f"Approval {row.id} does not bind the selected policy version"
            )
        if latest_sequence.get(row.series_key) != row.sequence:
            raise ApprovalAttestationError(f"Approval {row.id} has been superseded")
        if row.status != "approved":
            raise ApprovalAttestationError(
                f"Approval {row.id} has terminal status {row.status!r}, not 'approved'"
            )
        if row.expires_at is not None and _utc(row.expires_at) <= _utc(at):
            raise ApprovalAttestationError(f"Approval {row.id} has expired")
        if not verify_approval_attestation_integrity(row):
            raise ApprovalAttestationError(f"Approval {row.id} failed integrity verification")
        if row.approval_principal_id in seen_principals:
            raise ApprovalAttestationError(
                "A principal can contribute at most one approval to an evaluation"
            )
        seen_principals.add(row.approval_principal_id)
        approvals.append(
            GateApproval(
                principal_id=row.approval_principal_id,
                role=row.attester_role,
                status="approved",
                attestation_ref=str(row.id),
                principal_type=row.principal_type,
                auth_method=row.auth_method,
                attested_at=_utc(row.attested_at),
            )
        )
        snapshot.append(
            {
                "id": str(row.id),
                "series_key": row.series_key,
                "sequence": row.sequence,
                "principal_ref": principal_ref_hash(row.approval_principal_id),
                "role": row.attester_role,
                "principal_type": row.principal_type,
                "auth_method": row.auth_method,
                "status": row.status,
                "context_hash": row.context_hash,
                "attestation_hash": row.attestation_hash,
                "expires_at": _utc(row.expires_at).isoformat() if row.expires_at else None,
            }
        )
    return approvals, snapshot
