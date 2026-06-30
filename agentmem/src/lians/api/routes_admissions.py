"""
Admission review queue — list and resolve memory writes that admission control
held for human review (PII/PHI/MNPI in enforce mode).

    GET  /v1/admissions                  — list held (pending) writes
    POST /v1/admissions/{id}/resolve     — approve (→ create the memory) or reject

Reviewing held content is a privileged compliance action, so these require the
admin scope.
"""
from __future__ import annotations

from typing import Optional
from uuid import UUID

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from ..db import get_db
from ..schemas import AdmissionListResult, AdmissionResolveRequest, PendingAdmissionOut
from ..admission_service import list_pending, resolve_pending
from .deps import get_auth, AuthContext

router = APIRouter(prefix="/v1/admissions", tags=["admission"])


@router.get("", response_model=AdmissionListResult)
async def get_admissions(
    status: Optional[str] = Query(default="pending"),
    limit: int = Query(default=50, ge=1, le=500),
    auth: AuthContext = Depends(get_auth),
    db: AsyncSession = Depends(get_db),
):
    auth.require("admin")
    effective = status if status else None
    rows = await list_pending(db, auth.namespace, status=effective, limit=limit)
    return AdmissionListResult(
        pending=[PendingAdmissionOut.model_validate(r) for r in rows],
        total=len(rows),
        status_filter=effective,
    )


@router.post("/{pending_id}/resolve")
async def resolve_admission(
    pending_id: UUID,
    req: AdmissionResolveRequest,
    auth: AuthContext = Depends(get_auth),
    db: AsyncSession = Depends(get_db),
):
    """Approve a held write (creates the memory) or reject it. Audited either way."""
    auth.require("admin")
    return await resolve_pending(db, auth.namespace, pending_id, req.action, req.note)
