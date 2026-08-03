"""Flagship Lians Investigator aggregation and triage API."""
from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from sqlalchemy.ext.asyncio import AsyncSession

from ..audit_chain import AuditCapacityExceeded
from ..db import get_db
from ..decision_record_integrity import DecisionRecordIntegrityError
from ..investigator_schemas import DecisionInvestigationReport, InvestigatorQueueOut
from ..investigator_service import build_decision_investigation, build_investigator_queue
from .deps import AuthContext, get_auth

router = APIRouter(prefix="/v1/investigator", tags=["investigator"])


@router.get(
    "/queue",
    response_model=InvestigatorQueueOut,
    summary="Prioritize recent decisions by evidence and control-plane signals",
)
async def investigator_queue(
    response: Response,
    limit: int = Query(default=100, ge=1, le=500),
    scan_limit: int = Query(default=500, ge=1, le=5000),
    auth: AuthContext = Depends(get_auth),
    db: AsyncSession = Depends(get_db),
) -> InvestigatorQueueOut:
    auth.require("read")
    response.headers["Cache-Control"] = "no-store"
    response.headers["Pragma"] = "no-cache"
    if scan_limit < limit:
        raise HTTPException(status_code=422, detail="scan_limit must be at least limit")
    return await build_investigator_queue(
        db,
        namespace=auth.namespace,
        barrier_group=auth.barrier_group,
        limit=limit,
        scan_limit=scan_limit,
    )


@router.get(
    "/decisions/{decision_id}",
    response_model=DecisionInvestigationReport,
    summary="Reconstruct one decision across evidence, Gate, review, and remediation",
)
async def investigate_decision(
    decision_id: UUID,
    response: Response,
    timeline_limit: int = Query(default=200, ge=1, le=1000),
    evidence_limit: int = Query(default=500, ge=1, le=5000),
    control_history_limit: int = Query(default=200, ge=1, le=2000),
    case_limit: int = Query(default=100, ge=1, le=1000),
    task_limit: int = Query(default=500, ge=1, le=5000),
    closure_limit: int = Query(default=500, ge=1, le=5000),
    include_sensitive: bool = Query(default=False),
    verify_audit: bool = Query(default=True),
    auth: AuthContext = Depends(get_auth),
    db: AsyncSession = Depends(get_db),
) -> DecisionInvestigationReport:
    auth.require("read")
    response.headers["Cache-Control"] = "no-store"
    response.headers["Pragma"] = "no-cache"
    if include_sensitive:
        auth.require("admin")
    try:
        report = await build_decision_investigation(
            db,
            namespace=auth.namespace,
            barrier_group=auth.barrier_group,
            decision_id=decision_id,
            timeline_limit=timeline_limit,
            evidence_limit=evidence_limit,
            control_history_limit=control_history_limit,
            case_limit=case_limit,
            task_limit=task_limit,
            closure_limit=closure_limit,
            include_sensitive=include_sensitive,
            verify_audit=verify_audit,
        )
    except DecisionRecordIntegrityError as exc:
        raise HTTPException(
            status_code=409,
            detail={
                "code": "decision_record_integrity_verification_failed",
                "message": "Decision record failed authenticated integrity verification",
            },
        ) from exc
    except AuditCapacityExceeded as exc:
        raise HTTPException(
            status_code=413,
            detail={
                "code": exc.code,
                "message": exc.public_message,
                "estimated_bytes": exc.estimated_bytes,
                "byte_limit": exc.byte_limit,
            },
        ) from exc
    if report is None:
        raise HTTPException(status_code=404, detail="Decision not found")
    return report
