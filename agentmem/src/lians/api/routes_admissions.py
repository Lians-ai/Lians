"""
Admission review queue — list and resolve memory writes that admission control
held for human review (PII/PHI/MNPI in enforce mode).

    GET  /v1/admissions                  — list held (pending) writes
    POST /v1/admissions/{id}/resolve     — approve (→ create the memory) or reject

Reviewing held content is a privileged compliance action, so these require the
admin scope.
"""
from __future__ import annotations

from datetime import datetime
from typing import Optional
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession

from ..admission_service import (
    PendingContentIntegrityError,
    PendingContentPageCapacityExceeded,
    count_pending,
    decrypt_pending_contents,
    list_pending,
    materialize_pending_page,
    resolve_pending,
)
from ..db import get_db
from ..mutation_safety import reject_non_replayable_idempotency_key
from ..schemas import AdmissionListResult, AdmissionResolveRequest, PendingAdmissionOut
from .deps import AuthContext, get_auth

router = APIRouter(prefix="/v1/admissions", tags=["admission"])

_ADMISSION_PAGE_RESPONSES = {
    409: {
        "description": (
            "Pending content failed integrity verification "
            "(`pending_admission_content_integrity_failed`)"
        )
    },
    413: {
        "description": (
            "The pending-content page exceeds the safe materialization budget "
            "(`pending_admission_page_capacity_exceeded`)"
        )
    },
}
_ADMISSION_RESOLVE_RESPONSES = {
    409: {
        "description": (
            "Pending content failed integrity verification "
            "(`pending_admission_content_integrity_failed`)"
        )
    }
}


@router.get(
    "",
    response_model=AdmissionListResult,
    responses=_ADMISSION_PAGE_RESPONSES,
)
async def get_admissions(
    status: Optional[str] = Query(default="pending"),
    limit: int = Query(default=50, ge=1, le=500),
    after_created_at: datetime | None = Query(default=None),
    after_id: UUID | None = Query(default=None),
    auth: AuthContext = Depends(get_auth),
    db: AsyncSession = Depends(get_db),
):
    auth.require("admin")
    if (after_created_at is None) != (after_id is None):
        raise HTTPException(
            status_code=422,
            detail="after_created_at and after_id must be supplied together",
        )
    effective = status if status else None
    rows = await list_pending(
        db, auth.namespace, status=effective, limit=limit + 1,
        barrier_override=auth.barrier_group,
        after_created_at=after_created_at,
        after_id=after_id,
    )
    total = await count_pending(
        db,
        auth.namespace,
        status=effective,
        barrier_override=auth.barrier_group,
    )
    has_more = len(rows) > limit
    try:
        page_rows = await materialize_pending_page(db, rows[:limit])
    except PendingContentPageCapacityExceeded as exc:
        raise HTTPException(
            status_code=413,
            detail={
                "code": "pending_admission_page_capacity_exceeded",
                "estimated_bytes": exc.estimated_bytes,
                "byte_limit": exc.byte_limit,
                "content_materialized": False,
            },
        ) from exc
    except PendingContentIntegrityError as exc:
        raise HTTPException(
            status_code=409,
            detail={"code": "pending_admission_content_integrity_failed"},
        ) from exc
    try:
        contents = await decrypt_pending_contents(db, page_rows)
    except PendingContentIntegrityError as exc:
        raise HTTPException(
            status_code=409,
            detail={"code": "pending_admission_content_integrity_failed"},
        ) from exc
    pending = [
        (
            PendingAdmissionOut.model_validate(row).model_copy(
                update={"content": content}
            )
        )
        for row, content in zip(page_rows, contents, strict=True)
    ]
    return AdmissionListResult(
        pending=pending,
        total=total,
        returned=len(pending),
        complete=after_created_at is None and not has_more,
        has_more=has_more,
        next_created_at=(page_rows[-1].created_at if has_more and page_rows else None),
        next_id=(page_rows[-1].id if has_more and page_rows else None),
        status_filter=effective,
    )


@router.post(
    "/{pending_id}/resolve",
    dependencies=[Depends(reject_non_replayable_idempotency_key)],
    responses=_ADMISSION_RESOLVE_RESPONSES,
)
async def resolve_admission(
    pending_id: UUID,
    req: AdmissionResolveRequest,
    auth: AuthContext = Depends(get_auth),
    db: AsyncSession = Depends(get_db),
):
    """Approve a held write (creates the memory) or reject it. Audited either way."""
    auth.require("admin")
    try:
        return await resolve_pending(
            db, auth.namespace, pending_id, req.action, req.note,
            barrier_override=auth.barrier_group,
        )
    except PendingContentIntegrityError as exc:
        raise HTTPException(
            status_code=409,
            detail={"code": "pending_admission_content_integrity_failed"},
        ) from exc
