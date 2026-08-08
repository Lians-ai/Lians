"""Release candidate, signed approval, rollout, and rollback APIs."""

from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from ..audit_chain import chain_log
from ..db import get_db
from ..improvement_service import ImprovementNotFound, visible_by_id
from ..mutation_safety import reject_non_replayable_idempotency_key
from ..receipt_signer import (
    ReceiptSignerConfigurationError,
    ReceiptSigningUnavailable,
    get_receipt_signer,
)
from ..release_models import Deployment, ReleaseAttestation, ReleaseCandidate, Rollback
from ..release_schemas import (
    DeploymentCreate,
    DeploymentOut,
    ReleaseAttestationCreate,
    ReleaseAttestationOut,
    ReleaseAttestationVerification,
    ReleaseAttestationVerifyRequest,
    ReleaseCandidateCreate,
    ReleaseCandidateOut,
    RollbackCreate,
    RollbackOut,
)
from ..release_service import (
    ReleaseContractError,
    create_deployment,
    create_release_attestation,
    create_release_candidate,
    create_rollback,
    deployment_out,
    release_attestation_out,
    release_candidate_out,
    rollback_out,
    verify_release_attestation,
)
from .deps import AuthContext, get_auth

releases_router = APIRouter(prefix="/v1/releases", tags=["release-assurance"])
deployments_router = APIRouter(prefix="/v1/deployments", tags=["release-assurance"])
rollback_router = APIRouter(prefix="/v1/rollback", tags=["release-assurance"])


def _principal(auth: AuthContext) -> str:
    if not auth.principal_id:
        raise HTTPException(status_code=401, detail="Authenticated principal identity required")
    return auth.principal_id


async def _audit_commit(
    db: AsyncSession,
    *,
    auth: AuthContext,
    operation: str,
    resource_type: str,
    resource_id: UUID,
    content_hash: str,
) -> None:
    await chain_log(
        db,
        namespace=auth.namespace,
        agent_id=_principal(auth),
        op=operation,
        content_hash=content_hash,
        payload={"resource_type": resource_type, "resource_id": str(resource_id)},
    )
    await db.commit()


@releases_router.post(
    "",
    response_model=ReleaseCandidateOut,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(reject_non_replayable_idempotency_key)],
)
async def post_release_candidate(
    body: ReleaseCandidateCreate,
    auth: AuthContext = Depends(get_auth),
    db: AsyncSession = Depends(get_db),
) -> ReleaseCandidateOut:
    auth.require("write")
    try:
        row = await create_release_candidate(
            db,
            namespace=auth.namespace,
            barrier_group=auth.barrier_group,
            principal_ref=_principal(auth),
            body=body,
        )
        await _audit_commit(
            db,
            auth=auth,
            operation="release_candidate_created",
            resource_type="release_candidate",
            resource_id=row.id,
            content_hash=row.release_hash,
        )
    except ImprovementNotFound as exc:
        await db.rollback()
        raise HTTPException(status_code=404, detail="Release dependency not found") from exc
    except ReleaseContractError as exc:
        await db.rollback()
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except IntegrityError as exc:
        await db.rollback()
        raise HTTPException(status_code=409, detail="Release candidate already exists") from exc
    return release_candidate_out(row)


@releases_router.get("/candidates/{candidate_id}", response_model=ReleaseCandidateOut)
async def get_release_candidate(
    candidate_id: UUID,
    auth: AuthContext = Depends(get_auth),
    db: AsyncSession = Depends(get_db),
) -> ReleaseCandidateOut:
    auth.require("read")
    try:
        row = await visible_by_id(
            db,
            ReleaseCandidate,
            candidate_id,
            namespace=auth.namespace,
            barrier_group=auth.barrier_group,
        )
    except ImprovementNotFound as exc:
        raise HTTPException(status_code=404, detail="Release candidate not found") from exc
    return release_candidate_out(row)


@releases_router.post(
    "/attestations",
    response_model=ReleaseAttestationOut,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(reject_non_replayable_idempotency_key)],
)
async def post_release_attestation(
    body: ReleaseAttestationCreate,
    auth: AuthContext = Depends(get_auth),
    db: AsyncSession = Depends(get_db),
) -> ReleaseAttestationOut:
    auth.require("write")
    try:
        signer = await get_receipt_signer()
        row = await create_release_attestation(
            db,
            namespace=auth.namespace,
            barrier_group=auth.barrier_group,
            principal_ref=_principal(auth),
            body=body,
            signer=signer,
        )
        await _audit_commit(
            db,
            auth=auth,
            operation="release_attestation_signed",
            resource_type="release_attestation",
            resource_id=row.id,
            content_hash=row.payload_hash,
        )
    except ImprovementNotFound as exc:
        await db.rollback()
        raise HTTPException(status_code=404, detail="Release or approval not found") from exc
    except ReleaseContractError as exc:
        await db.rollback()
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except ReceiptSignerConfigurationError as exc:
        await db.rollback()
        raise HTTPException(status_code=500, detail="Release signer is misconfigured") from exc
    except ReceiptSigningUnavailable as exc:
        await db.rollback()
        raise HTTPException(status_code=503, detail="Release signer is unavailable") from exc
    except IntegrityError as exc:
        await db.rollback()
        raise HTTPException(status_code=409, detail="Release attestation already exists") from exc
    return release_attestation_out(row)


@releases_router.get("/attestations/{attestation_id}", response_model=ReleaseAttestationOut)
async def get_release_attestation(
    attestation_id: UUID,
    auth: AuthContext = Depends(get_auth),
    db: AsyncSession = Depends(get_db),
) -> ReleaseAttestationOut:
    auth.require("read")
    try:
        row = await visible_by_id(
            db,
            ReleaseAttestation,
            attestation_id,
            namespace=auth.namespace,
            barrier_group=auth.barrier_group,
        )
    except ImprovementNotFound as exc:
        raise HTTPException(status_code=404, detail="Release attestation not found") from exc
    return release_attestation_out(row)


@releases_router.post("/attestations/verify", response_model=ReleaseAttestationVerification)
async def post_release_attestation_verify(
    body: ReleaseAttestationVerifyRequest,
    auth: AuthContext = Depends(get_auth),
) -> ReleaseAttestationVerification:
    auth.require("read")
    return verify_release_attestation(body.attestation, trusted_public_key=body.trusted_public_key)


@deployments_router.post(
    "",
    response_model=DeploymentOut,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(reject_non_replayable_idempotency_key)],
)
async def post_deployment(
    body: DeploymentCreate,
    auth: AuthContext = Depends(get_auth),
    db: AsyncSession = Depends(get_db),
) -> DeploymentOut:
    auth.require("write")
    try:
        row = await create_deployment(
            db,
            namespace=auth.namespace,
            barrier_group=auth.barrier_group,
            principal_ref=_principal(auth),
            body=body,
        )
        await _audit_commit(
            db,
            auth=auth,
            operation="deployment_evidence_recorded",
            resource_type="improvement_deployment",
            resource_id=row.id,
            content_hash=row.deployment_hash,
        )
    except ImprovementNotFound as exc:
        await db.rollback()
        raise HTTPException(status_code=404, detail="Deployment dependency not found") from exc
    except ReleaseContractError as exc:
        await db.rollback()
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except IntegrityError as exc:
        await db.rollback()
        raise HTTPException(status_code=409, detail="Deployment evidence already exists") from exc
    return deployment_out(row)


@deployments_router.get("/{deployment_id}", response_model=DeploymentOut)
async def get_deployment(
    deployment_id: UUID,
    auth: AuthContext = Depends(get_auth),
    db: AsyncSession = Depends(get_db),
) -> DeploymentOut:
    auth.require("read")
    try:
        row = await visible_by_id(
            db,
            Deployment,
            deployment_id,
            namespace=auth.namespace,
            barrier_group=auth.barrier_group,
        )
    except ImprovementNotFound as exc:
        raise HTTPException(status_code=404, detail="Deployment not found") from exc
    return deployment_out(row)


@rollback_router.post(
    "",
    response_model=RollbackOut,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(reject_non_replayable_idempotency_key)],
)
async def post_rollback(
    body: RollbackCreate,
    auth: AuthContext = Depends(get_auth),
    db: AsyncSession = Depends(get_db),
) -> RollbackOut:
    auth.require("write")
    try:
        row = await create_rollback(
            db,
            namespace=auth.namespace,
            barrier_group=auth.barrier_group,
            principal_ref=_principal(auth),
            body=body,
        )
        await _audit_commit(
            db,
            auth=auth,
            operation="deployment_rollback_recorded",
            resource_type="improvement_rollback",
            resource_id=row.id,
            content_hash=row.rollback_hash,
        )
    except ImprovementNotFound as exc:
        await db.rollback()
        raise HTTPException(status_code=404, detail="Rollback deployment not found") from exc
    except ReleaseContractError as exc:
        await db.rollback()
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except IntegrityError as exc:
        await db.rollback()
        raise HTTPException(status_code=409, detail="Deployment was already rolled back") from exc
    return rollback_out(row)


@rollback_router.get("/{rollback_id}", response_model=RollbackOut)
async def get_rollback(
    rollback_id: UUID,
    auth: AuthContext = Depends(get_auth),
    db: AsyncSession = Depends(get_db),
) -> RollbackOut:
    auth.require("read")
    try:
        row = await visible_by_id(
            db, Rollback, rollback_id, namespace=auth.namespace, barrier_group=auth.barrier_group
        )
    except ImprovementNotFound as exc:
        raise HTTPException(status_code=404, detail="Rollback not found") from exc
    return rollback_out(row)


__all__ = ["deployments_router", "releases_router", "rollback_router"]
