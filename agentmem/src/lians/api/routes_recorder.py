"""Authenticated Universal Recorder ingestion and readiness endpoints."""

from __future__ import annotations

import hashlib
from datetime import datetime, timezone
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..db import get_db
from ..audit_chain import chain_log
from ..metrics import record_recorder_outcome
from ..recorder_schemas import (
    FirstReceiptReadinessSummary,
    RecorderBatchRejection,
    RecorderBatchRequest,
    RecorderBatchResult,
    RecorderEnvelope,
    RecorderEventOut,
    RecorderEvidenceIndexJobOut,
    RecorderIngestResult,
    RecorderRunReadiness,
)
from ..recorder_index_service import retry_recorder_index_job
from ..recorder_models import RecorderEvidenceIndexJob
from ..recorder_service import (
    NormalizedRecorderEvent,
    RecorderEventPageCapacityExceeded,
    RecorderIntegrityError,
    RecorderNormalizationError,
    first_receipt_readiness,
    get_run_for_auth,
    ingest_recorder_event,
    list_run_events,
    normalize_recorder_envelope,
    run_readiness,
)
from .deps import AuthContext, get_auth

router = APIRouter(prefix="/v1/recorder", tags=["universal-recorder"])

_RUN_EVENT_PAGE_RESPONSE = {
    200: {
        "description": "Integrity-verified Recorder event page",
        "headers": {
            "X-Lians-Total-Count": {"schema": {"type": "integer", "minimum": 0}},
            "X-Lians-Page-Limit": {"schema": {"type": "integer", "minimum": 1}},
            "X-Lians-Page-Returned": {
                "schema": {"type": "integer", "minimum": 0}
            },
            "X-Lians-Has-More": {"schema": {"type": "boolean"}},
            "X-Lians-Page-Complete": {"schema": {"type": "boolean"}},
            "X-Lians-Collection-Complete": {"schema": {"type": "boolean"}},
            "X-Lians-Next-Before-Recorded-At": {
                "schema": {"type": "string", "format": "date-time"}
            },
            "X-Lians-Next-Before-Id": {
                "schema": {"type": "string", "format": "uuid"}
            },
        },
    },
    409: {
        "description": (
            "The Recorder event page failed integrity verification "
            "(`recorder_event_integrity_failed`)"
        )
    },
    413: {
        "description": (
            "The requested event page exceeds the safe materialization budget "
            "(`recorder_event_page_capacity_exceeded`)"
        )
    },
}


def _set_run_event_page_headers(
    response: Response,
    *,
    total: int,
    limit: int,
    returned: int,
    has_more: bool,
    cursor_supplied: bool,
    next_recorded_at: datetime | None,
    next_id: UUID | None,
) -> None:
    response.headers["X-Lians-Total-Count"] = str(total)
    response.headers["X-Lians-Page-Limit"] = str(limit)
    response.headers["X-Lians-Page-Returned"] = str(returned)
    response.headers["X-Lians-Has-More"] = str(has_more).lower()
    response.headers["X-Lians-Page-Complete"] = str(not has_more).lower()
    response.headers["X-Lians-Collection-Complete"] = str(
        not cursor_supplied and not has_more and total == returned
    ).lower()
    if has_more and next_recorded_at is not None and next_id is not None:
        response.headers["X-Lians-Next-Before-Recorded-At"] = (
            next_recorded_at.isoformat()
        )
        response.headers["X-Lians-Next-Before-Id"] = str(next_id)


def _index_job_out(row: RecorderEvidenceIndexJob) -> RecorderEvidenceIndexJobOut:
    total = int(row.snapshot_event_count)
    indexed = int(row.events_indexed)
    return RecorderEvidenceIndexJobOut(
        id=row.id,
        decision_id=row.decision_id,
        status=row.status,
        snapshot_max_recorded_at=row.snapshot_max_recorded_at,
        snapshot_max_event_id=row.snapshot_max_event_id,
        snapshot_event_count=total,
        cursor_recorded_at=row.cursor_recorded_at,
        cursor_event_id=row.cursor_event_id,
        events_indexed=indexed,
        events_remaining=max(0, total - indexed),
        artifacts_created=int(row.artifacts_created),
        links_created=int(row.links_created),
        pages_completed=int(row.pages_completed),
        processing_attempts=int(row.processing_attempts),
        progress_ratio=indexed / total,
        complete=row.status == "completed" and indexed == total,
        next_attempt_at=row.next_attempt_at,
        last_error_code=row.last_error_code,
        last_error_digest=row.last_error_digest,
        failure_code=row.failure_code,
        created_at=row.created_at,
        started_at=row.started_at,
        updated_at=row.updated_at,
        completed_at=row.completed_at,
        failed_at=row.failed_at,
    )


async def _exact_index_job_for_auth(
    db: AsyncSession,
    *,
    auth: AuthContext,
    job_id: UUID,
) -> RecorderEvidenceIndexJob | None:
    barrier_filter = (
        RecorderEvidenceIndexJob.barrier_group.is_(None)
        if auth.barrier_group is None
        else RecorderEvidenceIndexJob.barrier_group == auth.barrier_group
    )
    return (
        await db.execute(
            select(RecorderEvidenceIndexJob).where(
                RecorderEvidenceIndexJob.id == job_id,
                RecorderEvidenceIndexJob.namespace == auth.namespace,
                barrier_filter,
            )
        )
    ).scalar_one_or_none()


def _ingestion_identity(auth: AuthContext) -> dict[str, str | None]:
    if not auth.principal_id or not auth.auth_method:
        raise HTTPException(
            status_code=500,
            detail="Authenticated Recorder principal could not be canonicalized",
        )
    return {
        "ingested_by_principal_ref": auth.principal_id,
        "ingested_by_auth_method": auth.auth_method,
        "ingested_by_credential_id": auth.credential_id,
    }


@router.post(
    "/events",
    response_model=RecorderIngestResult,
    status_code=status.HTTP_202_ACCEPTED,
    summary="Ingest one provider-neutral AI execution event",
)
async def ingest_event(
    envelope: RecorderEnvelope,
    auth: AuthContext = Depends(get_auth),
    db: AsyncSession = Depends(get_db),
) -> RecorderIngestResult:
    auth.require("write")
    try:
        result = await ingest_recorder_event(
            db,
            namespace=auth.namespace,
            barrier_group=auth.barrier_group,
            envelope=envelope,
            **_ingestion_identity(auth),
        )
    except RecorderNormalizationError as exc:
        record_recorder_outcome("rejected")
        raise HTTPException(
            status_code=422,
            detail={"code": exc.code, "message": exc.detail},
        ) from exc
    await db.commit()
    record_recorder_outcome("deduplicated" if result.duplicate else "accepted")
    return result


@router.post(
    "/batch",
    response_model=RecorderBatchResult,
    status_code=status.HTTP_202_ACCEPTED,
    summary="Ingest up to 500 mixed-protocol Recorder events",
)
async def ingest_batch(
    request: RecorderBatchRequest,
    auth: AuthContext = Depends(get_auth),
    db: AsyncSession = Depends(get_db),
) -> RecorderBatchResult:
    """Normalize a mixed OTLP, MCP, A2A, and native batch in one transaction."""
    auth.require("write")
    received_at = datetime.now(timezone.utc)
    prepared: list[tuple[int, RecorderEnvelope, NormalizedRecorderEvent]] = []
    rejections: list[RecorderBatchRejection] = []

    for index, envelope in enumerate(request.events):
        try:
            normalized = normalize_recorder_envelope(envelope, received_at=received_at)
            prepared.append((index, envelope, normalized))
        except RecorderNormalizationError as exc:
            rejection = RecorderBatchRejection(
                index=index,
                code=exc.code,
                detail=exc.detail,
            )
            if request.atomic:
                # Atomic ingestion rejects the whole batch, not only the first
                # invalid envelope surfaced to the caller.
                record_recorder_outcome("rejected", len(request.events))
                raise HTTPException(status_code=422, detail=rejection.model_dump()) from exc
            rejections.append(rejection)

    results: list[RecorderIngestResult] = []
    for _index, envelope, normalized in prepared:
        result = await ingest_recorder_event(
            db,
            namespace=auth.namespace,
            barrier_group=auth.barrier_group,
            envelope=envelope,
            normalized=normalized,
            received_at=received_at,
            **_ingestion_identity(auth),
        )
        results.append(result)

    await db.commit()
    record_recorder_outcome("accepted", sum(result.accepted for result in results))
    record_recorder_outcome("deduplicated", sum(result.duplicate for result in results))
    record_recorder_outcome("rejected", len(rejections))
    ready_run_ids = sorted(
        {result.readiness.run_id for result in results if result.readiness.receipt_ready},
        key=str,
    )
    return RecorderBatchResult(
        received=len(request.events),
        accepted=sum(result.accepted for result in results),
        duplicates=sum(result.duplicate for result in results),
        rejected=len(rejections),
        results=results,
        rejections=rejections,
        ready_run_ids=ready_run_ids,
    )


@router.get(
    "/runs/{run_id}/readiness",
    response_model=RecorderRunReadiness,
    summary="Inspect a correlated boundary's Decision Receipt readiness",
)
async def get_run_readiness(
    run_id: UUID,
    auth: AuthContext = Depends(get_auth),
    db: AsyncSession = Depends(get_db),
) -> RecorderRunReadiness:
    auth.require("read")
    run = await get_run_for_auth(
        db,
        namespace=auth.namespace,
        barrier_group=auth.barrier_group,
        run_id=run_id,
    )
    if run is None:
        raise HTTPException(status_code=404, detail="Recorder run not found")
    return run_readiness(run)


@router.get(
    "/runs/{run_id}/events",
    response_model=list[RecorderEventOut],
    summary="List normalized events in a correlated boundary",
    responses=_RUN_EVENT_PAGE_RESPONSE,
)
async def get_run_events(
    run_id: UUID,
    response: Response,
    limit: int = Query(500, ge=1, le=5000),
    before_recorded_at: datetime | None = Query(None),
    before_id: UUID | None = Query(None),
    auth: AuthContext = Depends(get_auth),
    db: AsyncSession = Depends(get_db),
) -> list[RecorderEventOut]:
    auth.require("read")
    if (before_recorded_at is None) != (before_id is None):
        raise HTTPException(
            status_code=422,
            detail="before_recorded_at and before_id must be supplied together",
        )
    run = await get_run_for_auth(
        db,
        namespace=auth.namespace,
        barrier_group=auth.barrier_group,
        run_id=run_id,
    )
    if run is None:
        raise HTTPException(status_code=404, detail="Recorder run not found")
    try:
        page = await list_run_events(
            db,
            namespace=auth.namespace,
            barrier_group=auth.barrier_group,
            run_id=run_id,
            limit=limit,
            before_recorded_at=before_recorded_at,
            before_id=before_id,
        )
    except RecorderIntegrityError as exc:
        raise HTTPException(
            status_code=409,
            detail={"code": "recorder_event_integrity_failed"},
        ) from exc
    except RecorderEventPageCapacityExceeded as exc:
        raise HTTPException(
            status_code=413,
            detail={
                "code": "recorder_event_page_capacity_exceeded",
                "estimated_bytes": exc.estimated_bytes,
                "byte_limit": exc.byte_limit,
                "events_materialized": False,
            },
        ) from exc
    last = page.events[-1] if page.has_more and page.events else None
    _set_run_event_page_headers(
        response,
        total=page.total,
        limit=limit,
        returned=len(page.events),
        has_more=page.has_more,
        cursor_supplied=before_recorded_at is not None,
        next_recorded_at=last.recorded_at if last is not None else None,
        next_id=last.id if last is not None else None,
    )
    return page.events


@router.get(
    "/readiness",
    response_model=FirstReceiptReadinessSummary,
    summary="Summarize time and capture gaps to the first Decision Receipt",
)
async def get_first_receipt_readiness(
    agent_id: str | None = Query(None, min_length=1, max_length=255),
    limit: int = Query(50, ge=1, le=200),
    auth: AuthContext = Depends(get_auth),
    db: AsyncSession = Depends(get_db),
) -> FirstReceiptReadinessSummary:
    auth.require("read")
    return await first_receipt_readiness(
        db,
        namespace=auth.namespace,
        barrier_group=auth.barrier_group,
        agent_id=agent_id,
        limit=limit,
    )


@router.get(
    "/indexing/jobs/{job_id}",
    response_model=RecorderEvidenceIndexJobOut,
    summary="Inspect durable fixed-snapshot Recorder evidence indexing",
)
async def get_evidence_index_job(
    job_id: UUID,
    auth: AuthContext = Depends(get_auth),
    db: AsyncSession = Depends(get_db),
) -> RecorderEvidenceIndexJobOut:
    auth.require("read")
    row = await _exact_index_job_for_auth(db, auth=auth, job_id=job_id)
    if row is None:
        raise HTTPException(status_code=404, detail="Recorder indexing job not found")
    return _index_job_out(row)


@router.get(
    "/indexing/decisions/{decision_id}",
    response_model=RecorderEvidenceIndexJobOut,
    summary="Find a durable Recorder evidence job by authoritative decision",
)
async def get_evidence_index_job_for_decision(
    decision_id: UUID,
    auth: AuthContext = Depends(get_auth),
    db: AsyncSession = Depends(get_db),
) -> RecorderEvidenceIndexJobOut:
    auth.require("read")
    barrier_filter = (
        RecorderEvidenceIndexJob.barrier_group.is_(None)
        if auth.barrier_group is None
        else RecorderEvidenceIndexJob.barrier_group == auth.barrier_group
    )
    row = (
        await db.execute(
            select(RecorderEvidenceIndexJob).where(
                RecorderEvidenceIndexJob.namespace == auth.namespace,
                RecorderEvidenceIndexJob.decision_id == decision_id,
                barrier_filter,
            )
        )
    ).scalar_one_or_none()
    if row is None:
        raise HTTPException(status_code=404, detail="Recorder indexing job not found")
    return _index_job_out(row)


@router.post(
    "/indexing/jobs/{job_id}/retry",
    response_model=RecorderEvidenceIndexJobOut,
    summary="Retry a failed Recorder evidence snapshot after remediation",
)
async def retry_evidence_index_job(
    job_id: UUID,
    auth: AuthContext = Depends(get_auth),
    db: AsyncSession = Depends(get_db),
) -> RecorderEvidenceIndexJobOut:
    auth.require("write")
    row = await _exact_index_job_for_auth(db, auth=auth, job_id=job_id)
    if row is None:
        raise HTTPException(status_code=404, detail="Recorder indexing job not found")
    try:
        row = await retry_recorder_index_job(db, job=row)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    await chain_log(
        db,
        auth.namespace,
        auth.principal_id or "lians:principal:v1:unknown",
        "recorder_evidence_index_retry",
        content_hash=hashlib.sha256(str(row.id).encode()).hexdigest(),
        payload={
            "job_id": str(row.id),
            "decision_id": str(row.decision_id),
            "snapshot_event_count": int(row.snapshot_event_count),
        },
    )
    await db.commit()
    return _index_job_out(row)
