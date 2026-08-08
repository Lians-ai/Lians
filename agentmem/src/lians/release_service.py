"""Fail-closed release assurance, rollout evidence, and rollback records."""

from __future__ import annotations

import base64
import binascii
import hashlib
import uuid
from datetime import UTC, datetime
from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from .control_models import GateApprovalAttestation
from .immutable_attestation_service import verify_approval_attestation_integrity
from .improvement_models import (
    AgentVersion,
    Candidate,
    Comparison,
    EvalRun,
    EvaluationAttestation,
    OptimizationStudy,
)
from .improvement_service import (
    barrier_scope,
    evaluation_attestation_out,
    sha256_json,
    verify_evaluation_attestation,
    visible_by_id,
)
from .receipt_signer import ReceiptSigner, ReceiptSigningUnavailable
from .release_models import Deployment, ReleaseAttestation, ReleaseCandidate, Rollback
from .release_schemas import (
    DeploymentCreate,
    DeploymentOut,
    ReleaseAttestationCreate,
    ReleaseAttestationOut,
    ReleaseAttestationVerification,
    ReleaseCandidateCreate,
    ReleaseCandidateOut,
    RollbackCreate,
    RollbackOut,
)


class ReleaseContractError(ValueError):
    """A release claim is missing required immutable or human evidence."""


def _utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def release_target_ref(candidate_id: uuid.UUID) -> str:
    """Canonical Gate target for approving one exact release candidate."""
    return f"urn:lians:release-candidate:{candidate_id}"


def release_candidate_out(row: ReleaseCandidate) -> ReleaseCandidateOut:
    return ReleaseCandidateOut(
        id=row.id,
        namespace=row.namespace,
        barrier_group=row.barrier_group,
        name=row.name,
        version=row.version,
        agent_version_id=row.agent_version_id,
        evaluation_attestation_id=row.evaluation_attestation_id,
        optimization_study_id=row.optimization_study_id,
        environment_manifest=dict(row.environment_manifest),
        rollout_plan=dict(row.rollout_plan),
        release_hash=row.release_hash,
        created_by_principal_ref=row.created_by_principal_ref,
        created_at=row.created_at,
    )


async def create_release_candidate(
    db: AsyncSession,
    *,
    namespace: str,
    barrier_group: str | None,
    principal_ref: str,
    body: ReleaseCandidateCreate,
) -> ReleaseCandidate:
    version = await visible_by_id(
        db,
        AgentVersion,
        body.agent_version_id,
        namespace=namespace,
        barrier_group=barrier_group,
    )
    eval_attestation = await visible_by_id(
        db,
        EvaluationAttestation,
        body.evaluation_attestation_id,
        namespace=namespace,
        barrier_group=barrier_group,
    )
    verification = verify_evaluation_attestation(evaluation_attestation_out(eval_attestation))
    if not verification.valid:
        raise ReleaseContractError("evaluation attestation failed signature verification")
    comparison = await visible_by_id(
        db,
        Comparison,
        eval_attestation.comparison_id,
        namespace=namespace,
        barrier_group=barrier_group,
    )
    candidate_run = await visible_by_id(
        db,
        EvalRun,
        comparison.candidate_run_id,
        namespace=namespace,
        barrier_group=barrier_group,
    )
    payload_candidate = eval_attestation.payload.get("candidate", {})
    if (
        candidate_run.agent_version_id != version.id
        or payload_candidate.get("agent_version_id") != str(version.id)
        or payload_candidate.get("manifest_hash") != version.manifest_hash
    ):
        raise ReleaseContractError(
            "release agent version does not match the signed evaluation candidate"
        )
    if (
        comparison.verdict != "eligible_for_review"
        or not comparison.critical_invariants_passed
        or comparison.primary_improvement <= 0
    ):
        raise ReleaseContractError(
            "only candidates with verified improvement and passing invariants may be released"
        )
    required_environment_fields = {
        "image_digest",
        "dependency_lock_hash",
        "runtime_policy_hash",
    }
    missing = sorted(required_environment_fields - set(body.environment_manifest))
    if missing:
        raise ReleaseContractError("environment manifest must pin " + ", ".join(missing))
    if body.optimization_study_id is not None:
        study = await visible_by_id(
            db,
            OptimizationStudy,
            body.optimization_study_id,
            namespace=namespace,
            barrier_group=barrier_group,
        )
        candidate = (
            await db.execute(
                select(Candidate).where(
                    Candidate.namespace == namespace,
                    Candidate.study_id == study.id,
                    Candidate.agent_version_id == version.id,
                    Candidate.comparison_id == comparison.id,
                    Candidate.eligible.is_(True),
                )
            )
        ).scalar_one_or_none()
        if candidate is None:
            raise ReleaseContractError(
                "optimization study does not contain this eligible evaluated candidate"
            )
    row_id = uuid.uuid4()
    document = {
        "schema": "lians.release-candidate.v1",
        "id": str(row_id),
        "namespace": namespace,
        "barrier_scope": barrier_scope(barrier_group),
        "name": body.name,
        "version": body.version,
        "agent_version_id": str(version.id),
        "manifest_hash": version.manifest_hash,
        "evaluation_attestation_id": str(eval_attestation.id),
        "evaluation_payload_hash": eval_attestation.payload_hash,
        "optimization_study_id": (
            str(body.optimization_study_id) if body.optimization_study_id else None
        ),
        "environment_manifest": body.environment_manifest,
        "rollout_plan": body.rollout_plan.model_dump(mode="json"),
    }
    row = ReleaseCandidate(
        id=row_id,
        namespace=namespace,
        barrier_group=barrier_group,
        barrier_scope=barrier_scope(barrier_group),
        name=body.name,
        version=body.version,
        agent_version_id=version.id,
        evaluation_attestation_id=eval_attestation.id,
        optimization_study_id=body.optimization_study_id,
        environment_manifest=body.environment_manifest,
        rollout_plan=body.rollout_plan.model_dump(mode="json"),
        release_hash=sha256_json(document),
        created_by_principal_ref=principal_ref,
    )
    db.add(row)
    await db.flush()
    return row


def release_attestation_out(row: ReleaseAttestation) -> ReleaseAttestationOut:
    return ReleaseAttestationOut(
        id=row.id,
        namespace=row.namespace,
        barrier_group=row.barrier_group,
        schema_version=row.schema_version,
        release_candidate_id=row.release_candidate_id,
        evaluation_attestation_id=row.evaluation_attestation_id,
        approval_attestation_ids=[uuid.UUID(value) for value in row.approval_attestation_ids],
        payload=dict(row.payload),
        payload_hash=row.payload_hash,
        signature_algorithm=row.signature_algorithm,
        signing_key_id=row.signing_key_id,
        signing_public_key=row.signing_public_key,
        signature=row.signature,
        attested_by_principal_ref=row.attested_by_principal_ref,
        attested_at=row.attested_at,
    )


async def _validated_release_approvals(
    db: AsyncSession,
    *,
    namespace: str,
    barrier_group: str | None,
    candidate: ReleaseCandidate,
    approval_ids: list[uuid.UUID],
    at: datetime,
) -> list[GateApprovalAttestation]:
    expected_target = release_target_ref(candidate.id)
    rows: list[GateApprovalAttestation] = []
    principals: set[str] = set()
    for approval_id in approval_ids:
        approval = await visible_by_id(
            db,
            GateApprovalAttestation,
            approval_id,
            namespace=namespace,
            barrier_group=barrier_group,
        )
        latest = (
            await db.execute(
                select(GateApprovalAttestation)
                .where(
                    GateApprovalAttestation.namespace == namespace,
                    GateApprovalAttestation.series_key == approval.series_key,
                )
                .order_by(GateApprovalAttestation.sequence.desc())
                .limit(1)
            )
        ).scalar_one()
        if latest.id != approval.id:
            raise ReleaseContractError("release approval has been superseded")
        if not verify_approval_attestation_integrity(approval):
            raise ReleaseContractError("release approval failed integrity verification")
        if approval.status != "approved":
            raise ReleaseContractError("release approval is not approved")
        if approval.expires_at is not None and _utc(approval.expires_at) <= at:
            raise ReleaseContractError("release approval has expired")
        if approval.action != "release.deploy" or approval.target_ref != expected_target:
            raise ReleaseContractError(
                "release approval must authorize release.deploy for the exact candidate"
            )
        if approval.target_barrier_group != candidate.barrier_group:
            raise ReleaseContractError("release approval crosses an information barrier")
        if approval.approval_principal_id in principals:
            raise ReleaseContractError("release approvals must come from distinct principals")
        principals.add(approval.approval_principal_id)
        rows.append(approval)
    return rows


async def create_release_attestation(
    db: AsyncSession,
    *,
    namespace: str,
    barrier_group: str | None,
    principal_ref: str,
    body: ReleaseAttestationCreate,
    signer: ReceiptSigner | None,
) -> ReleaseAttestation:
    if signer is None:
        raise ReceiptSigningUnavailable(
            "a configured Ed25519 signer is required for release attestations"
        )
    candidate = await visible_by_id(
        db,
        ReleaseCandidate,
        body.release_candidate_id,
        namespace=namespace,
        barrier_group=barrier_group,
    )
    eval_attestation = await visible_by_id(
        db,
        EvaluationAttestation,
        candidate.evaluation_attestation_id,
        namespace=namespace,
        barrier_group=barrier_group,
    )
    if not verify_evaluation_attestation(evaluation_attestation_out(eval_attestation)).valid:
        raise ReleaseContractError("evaluation attestation failed signature verification")
    attested_at = datetime.now(UTC)
    approvals = await _validated_release_approvals(
        db,
        namespace=namespace,
        barrier_group=barrier_group,
        candidate=candidate,
        approval_ids=body.approval_attestation_ids,
        at=attested_at,
    )
    row_id = uuid.uuid4()
    payload = {
        "schema": "lians.release-attestation.v0.1",
        "id": str(row_id),
        "namespace": namespace,
        "barrier_scope": barrier_scope(barrier_group),
        "release_candidate": {
            "id": str(candidate.id),
            "release_hash": candidate.release_hash,
            "agent_version_id": str(candidate.agent_version_id),
        },
        "evaluation_attestation": {
            "id": str(eval_attestation.id),
            "payload_hash": eval_attestation.payload_hash,
        },
        "gate": {
            "action": "release.deploy",
            "target_ref": release_target_ref(candidate.id),
            "approvals": [
                {
                    "id": str(approval.id),
                    "attestation_hash": approval.attestation_hash,
                    "policy_hash": approval.policy_hash,
                    "role": approval.attester_role,
                    "principal_ref_hash": hashlib.sha256(
                        approval.approval_principal_id.encode("utf-8")
                    ).hexdigest(),
                }
                for approval in approvals
            ],
        },
        "automatic_deployment_authorized": False,
        "attested_at": _utc(attested_at).isoformat(),
    }
    payload_hash = sha256_json(payload)
    signature = await signer.sign_digest(bytes.fromhex(payload_hash))
    if (
        signature.algorithm != "ed25519"
        or signature.key_id != signer.key_id
        or signature.public_key != signer.public_key
    ):
        raise ReceiptSigningUnavailable("release signer returned inconsistent trust metadata")
    row = ReleaseAttestation(
        id=row_id,
        namespace=namespace,
        barrier_group=barrier_group,
        barrier_scope=barrier_scope(barrier_group),
        schema_version="0.1",
        release_candidate_id=candidate.id,
        evaluation_attestation_id=eval_attestation.id,
        approval_attestation_ids=[str(approval.id) for approval in approvals],
        payload=payload,
        payload_hash=payload_hash,
        signature_algorithm=signature.algorithm,
        signing_key_id=signature.key_id,
        signing_public_key=signature.public_key,
        signature=signature.value,
        attested_by_principal_ref=principal_ref,
        attested_at=attested_at,
    )
    db.add(row)
    await db.flush()
    return row


def verify_release_attestation(
    attestation: ReleaseAttestationOut,
    *,
    trusted_public_key: str | None = None,
) -> ReleaseAttestationVerification:
    errors: list[str] = []
    payload_hash_valid = sha256_json(attestation.payload) == attestation.payload_hash
    if not payload_hash_valid:
        errors.append("payload_hash does not match the canonical release payload")
    public_key_text = trusted_public_key or attestation.signing_public_key
    if trusted_public_key is not None and trusted_public_key != attestation.signing_public_key:
        errors.append("embedded signing key does not match the trusted public key")
    signature_valid = False
    try:
        public_key_raw = base64.b64decode(public_key_text, validate=True)
        signature_raw = base64.b64decode(attestation.signature, validate=True)
        if len(public_key_raw) != 32 or len(signature_raw) != 64:
            raise ValueError("invalid Ed25519 material length")
        Ed25519PublicKey.from_public_bytes(public_key_raw).verify(
            signature_raw, bytes.fromhex(attestation.payload_hash)
        )
        signature_valid = True
    except (binascii.Error, InvalidSignature, ValueError):
        errors.append("Ed25519 signature is invalid")
    return ReleaseAttestationVerification(
        valid=payload_hash_valid and signature_valid and not errors,
        payload_hash_valid=payload_hash_valid,
        signature_valid=signature_valid,
        errors=errors,
    )


def deployment_out(row: Deployment) -> DeploymentOut:
    return DeploymentOut(
        id=row.id,
        release_attestation_id=row.release_attestation_id,
        stage=row.stage,
        traffic_percentage=row.traffic_percentage,
        environment=row.environment,
        external_deployment_ref_hash=row.external_deployment_ref_hash,
        prior_deployment_id=row.prior_deployment_id,
        evidence=dict(row.evidence),
        status=row.status,
        deployment_hash=row.deployment_hash,
        recorded_by_principal_ref=row.recorded_by_principal_ref,
        deployed_at=row.deployed_at,
        recorded_at=row.recorded_at,
    )


async def create_deployment(
    db: AsyncSession,
    *,
    namespace: str,
    barrier_group: str | None,
    principal_ref: str,
    body: DeploymentCreate,
) -> Deployment:
    attestation = await visible_by_id(
        db,
        ReleaseAttestation,
        body.release_attestation_id,
        namespace=namespace,
        barrier_group=barrier_group,
    )
    if not verify_release_attestation(release_attestation_out(attestation)).valid:
        raise ReleaseContractError("release attestation failed signature verification")
    candidate = await visible_by_id(
        db,
        ReleaseCandidate,
        attestation.release_candidate_id,
        namespace=namespace,
        barrier_group=barrier_group,
    )
    plan = dict(candidate.rollout_plan)
    prior = None
    if body.prior_deployment_id is not None:
        prior = await visible_by_id(
            db,
            Deployment,
            body.prior_deployment_id,
            namespace=namespace,
            barrier_group=barrier_group,
        )
        if prior.release_attestation_id != attestation.id or prior.environment != body.environment:
            raise ReleaseContractError("prior deployment belongs to a different release boundary")
        if prior.status != "healthy" or _utc(prior.deployed_at) > _utc(body.deployed_at):
            raise ReleaseContractError("prior deployment must be healthy and earlier")
    if body.stage == "shadow" and prior is not None:
        raise ReleaseContractError("shadow is the first rollout stage")
    if body.stage == "canary":
        if plan.get("require_shadow", True) and (prior is None or prior.stage != "shadow"):
            raise ReleaseContractError("a healthy shadow deployment is required before canary")
        expected = float(plan.get("canary_percentage", 5))
        if body.traffic_percentage != expected:
            raise ReleaseContractError("canary traffic does not match the immutable rollout plan")
    if body.stage == "production":
        required_prior = "canary" if plan.get("require_canary", True) else "shadow"
        if (plan.get("require_canary", True) or plan.get("require_shadow", True)) and (
            prior is None or prior.stage != required_prior
        ):
            raise ReleaseContractError(
                f"a healthy {required_prior} deployment is required before production"
            )
    row_id = uuid.uuid4()
    external_hash = hashlib.sha256(body.external_deployment_reference.encode("utf-8")).hexdigest()
    evidence = dict(body.evidence)
    document = {
        "schema": "lians.deployment-evidence.v1",
        "id": str(row_id),
        "release_attestation_id": str(attestation.id),
        "release_attestation_hash": attestation.payload_hash,
        "stage": body.stage,
        "traffic_percentage": body.traffic_percentage,
        "environment": body.environment,
        "external_deployment_ref_hash": external_hash,
        "prior_deployment_id": str(prior.id) if prior else None,
        "prior_deployment_hash": prior.deployment_hash if prior else None,
        "evidence": evidence,
        "status": body.status,
        "deployed_at": _utc(body.deployed_at),
    }
    row = Deployment(
        id=row_id,
        namespace=namespace,
        barrier_group=barrier_group,
        barrier_scope=barrier_scope(barrier_group),
        release_attestation_id=attestation.id,
        stage=body.stage,
        traffic_percentage=body.traffic_percentage,
        environment=body.environment,
        external_deployment_ref_hash=external_hash,
        prior_deployment_id=prior.id if prior else None,
        evidence=evidence,
        status=body.status,
        deployment_hash=sha256_json(document),
        recorded_by_principal_ref=principal_ref,
        deployed_at=_utc(body.deployed_at),
    )
    db.add(row)
    await db.flush()
    return row


def rollback_out(row: Rollback) -> RollbackOut:
    return RollbackOut(
        id=row.id,
        deployment_id=row.deployment_id,
        target_deployment_id=row.target_deployment_id,
        reason_code=row.reason_code,
        evidence=dict(row.evidence),
        external_rollback_ref_hash=row.external_rollback_ref_hash,
        rollback_hash=row.rollback_hash,
        recorded_by_principal_ref=row.recorded_by_principal_ref,
        rolled_back_at=row.rolled_back_at,
        recorded_at=row.recorded_at,
    )


async def create_rollback(
    db: AsyncSession,
    *,
    namespace: str,
    barrier_group: str | None,
    principal_ref: str,
    body: RollbackCreate,
) -> Rollback:
    source = await visible_by_id(
        db,
        Deployment,
        body.deployment_id,
        namespace=namespace,
        barrier_group=barrier_group,
    )
    target = await visible_by_id(
        db,
        Deployment,
        body.target_deployment_id,
        namespace=namespace,
        barrier_group=barrier_group,
    )
    if source.environment != target.environment:
        raise ReleaseContractError("rollback target belongs to a different environment")
    if source.stage != target.stage or source.traffic_percentage != target.traffic_percentage:
        raise ReleaseContractError("rollback target must restore the same rollout stage")
    if target.status != "healthy":
        raise ReleaseContractError("rollback target must be a previously healthy deployment")
    if _utc(target.deployed_at) >= _utc(source.deployed_at):
        raise ReleaseContractError("rollback target must predate the deployment being rolled back")
    if _utc(body.rolled_back_at) < _utc(source.deployed_at):
        raise ReleaseContractError("rollback time cannot predate the source deployment")
    row_id = uuid.uuid4()
    external_hash = hashlib.sha256(body.external_rollback_reference.encode("utf-8")).hexdigest()
    document = {
        "schema": "lians.rollback-evidence.v1",
        "id": str(row_id),
        "deployment_id": str(source.id),
        "deployment_hash": source.deployment_hash,
        "target_deployment_id": str(target.id),
        "target_deployment_hash": target.deployment_hash,
        "reason_code": body.reason_code,
        "evidence": body.evidence,
        "external_rollback_ref_hash": external_hash,
        "rolled_back_at": _utc(body.rolled_back_at),
    }
    row = Rollback(
        id=row_id,
        namespace=namespace,
        barrier_group=barrier_group,
        barrier_scope=barrier_scope(barrier_group),
        deployment_id=source.id,
        target_deployment_id=target.id,
        reason_code=body.reason_code,
        evidence=body.evidence,
        external_rollback_ref_hash=external_hash,
        rollback_hash=sha256_json(document),
        recorded_by_principal_ref=principal_ref,
        rolled_back_at=_utc(body.rolled_back_at),
    )
    db.add(row)
    await db.flush()
    return row


__all__ = [
    "ReleaseContractError",
    "create_deployment",
    "create_release_attestation",
    "create_release_candidate",
    "create_rollback",
    "deployment_out",
    "release_attestation_out",
    "release_candidate_out",
    "release_target_ref",
    "rollback_out",
    "verify_release_attestation",
]
