"""Bounded, durable data-subject erasure APIs."""

from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends, Header, HTTPException, Path, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from ..db import get_db
from ..schemas import EraseRequest, EraseResult, ErasureCertificate
from ..subject_erasure_service import (
    SubjectErasureInvariantError,
    SubjectErasureNotComplete,
    enqueue_subject_erasure,
    erasure_certificate_dict,
    get_subject_erasure_job,
    get_subject_erasure_job_for_subject,
    retry_subject_erasure_job,
    subject_erasure_job_dict,
)
from .deps import AuthContext, get_auth

router = APIRouter(prefix="/v1", tags=["privacy"])


def _require_privacy_admin(auth: AuthContext) -> None:
    auth.require("admin")
    auth.require_unbarriered()


def _not_complete_error(exc: SubjectErasureNotComplete) -> HTTPException:
    job = exc.job
    return HTTPException(
        status_code=status.HTTP_409_CONFLICT,
        detail={
            "code": "subject_erasure_not_complete",
            "message": "The DEK is destroyed; bounded derivative-store scrubbing is incomplete",
            "job_id": str(job.id),
            "status": job.status,
            "phase": job.phase,
            "failure_code": job.failure_code,
        },
    )


@router.post("/erase", response_model=EraseResult, status_code=status.HTTP_202_ACCEPTED)
async def erase_subject(
    req: EraseRequest,
    auth: AuthContext = Depends(get_auth),
    db: AsyncSession = Depends(get_db),
    idempotency_key: str | None = Header(
        default=None,
        alias="Idempotency-Key",
        min_length=1,
        max_length=512,
    ),
):
    """Destroy the subject DEK and enqueue exact bounded physical scrubbing."""

    _require_privacy_admin(auth)
    # request_ref is the durable business idempotency reference. The optional
    # HTTP key is bound into that opaque reference without ever being persisted.
    effective_request_ref = (
        f"{req.request_ref}\0{idempotency_key}"
        if idempotency_key is not None
        else req.request_ref
    )
    job, replayed = await enqueue_subject_erasure(
        db,
        namespace=auth.namespace,
        subject_id=req.subject_id,
        request_ref=effective_request_ref,
        principal_ref=(
            auth.principal_id or "lians:principal:v1:legacy-unverified"
        ),
        auth_method=auth.auth_method,
    )
    return EraseResult(**subject_erasure_job_dict(job, replayed=replayed))


@router.get("/erase/jobs/{job_id}", response_model=EraseResult)
async def subject_erasure_job_status(
    job_id: UUID,
    auth: AuthContext = Depends(get_auth),
    db: AsyncSession = Depends(get_db),
):
    _require_privacy_admin(auth)
    job = await get_subject_erasure_job(db, namespace=auth.namespace, job_id=job_id)
    if job is None:
        raise HTTPException(
            status_code=404,
            detail={
                "code": "subject_erasure_job_not_found",
                "message": "Subject-erasure job was not found",
            },
        )
    return EraseResult(**subject_erasure_job_dict(job))


@router.post(
    "/erase/jobs/{job_id}/retry",
    response_model=EraseResult,
    status_code=status.HTTP_202_ACCEPTED,
)
async def retry_subject_erasure(
    job_id: UUID,
    auth: AuthContext = Depends(get_auth),
    db: AsyncSession = Depends(get_db),
):
    _require_privacy_admin(auth)
    job = await retry_subject_erasure_job(
        db, namespace=auth.namespace, job_id=job_id
    )
    if job is None:
        raise HTTPException(
            status_code=404,
            detail={
                "code": "subject_erasure_job_not_found",
                "message": "Subject-erasure job was not found",
            },
        )
    return EraseResult(**subject_erasure_job_dict(job))


@router.get(
    "/erase/jobs/{job_id}/certificate",
    response_model=ErasureCertificate,
)
async def erasure_certificate_by_job(
    job_id: UUID,
    limit: int = Query(default=100, ge=1, le=500),
    after_memory_id: UUID | None = Query(default=None),
    auth: AuthContext = Depends(get_auth),
    db: AsyncSession = Depends(get_db),
):
    _require_privacy_admin(auth)
    job = await get_subject_erasure_job(db, namespace=auth.namespace, job_id=job_id)
    if job is None:
        raise HTTPException(
            status_code=404,
            detail={
                "code": "subject_erasure_job_not_found",
                "message": "Subject-erasure job was not found",
            },
        )
    try:
        certificate = await erasure_certificate_dict(
            db,
            job=job,
            limit=limit,
            after_memory_id=after_memory_id,
        )
    except SubjectErasureNotComplete as exc:
        raise _not_complete_error(exc) from exc
    except SubjectErasureInvariantError as exc:
        raise HTTPException(
            status_code=503,
            detail={
                "code": "subject_erasure_certificate_invariant_failed",
                "message": "The durable erasure evidence is not internally exact",
            },
        ) from exc
    return ErasureCertificate(**certificate)


@router.get("/erase/{subject_id}/certificate", response_model=ErasureCertificate)
async def erasure_certificate(
    subject_id: str = Path(min_length=1, max_length=1024),
    limit: int = Query(default=100, ge=1, le=500),
    after_memory_id: UUID | None = Query(default=None),
    auth: AuthContext = Depends(get_auth),
    db: AsyncSession = Depends(get_db),
):
    """Compatibility lookup; the raw subject is transient and never returned."""

    _require_privacy_admin(auth)
    job = await get_subject_erasure_job_for_subject(
        db,
        namespace=auth.namespace,
        subject_id=subject_id,
    )
    if job is None:
        raise HTTPException(
            status_code=404,
            detail={
                "code": "subject_erasure_job_not_found",
                "message": "No durable erasure job was found for that subject",
            },
        )
    try:
        certificate = await erasure_certificate_dict(
            db,
            job=job,
            limit=limit,
            after_memory_id=after_memory_id,
        )
    except SubjectErasureNotComplete as exc:
        raise _not_complete_error(exc) from exc
    except SubjectErasureInvariantError as exc:
        raise HTTPException(
            status_code=503,
            detail={
                "code": "subject_erasure_certificate_invariant_failed",
                "message": "The durable erasure evidence is not internally exact",
            },
        ) from exc
    return ErasureCertificate(**certificate)
