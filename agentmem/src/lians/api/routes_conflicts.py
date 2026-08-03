"""
Conflict detection and resolution routes.

    GET  /v1/conflicts                        — list flagged conflicts
    POST /v1/conflicts/{conflict_id}/resolve  — resolve a conflict

Conflicts arise when two memories report different values for the same fact at
the same (or ambiguous) point in time and the supersession engine cannot
determine which is authoritative.  Unlike the supersession review queue (which
reviews low-confidence overwrites), the conflict queue reviews cases where the
system deliberately chose NOT to overwrite either memory.

Both conflicting memories remain valid and visible until a human resolves the
conflict.  Resolution options:

    accept_a — memory_a is authoritative; memory_b is invalidated (valid_to=now)
    accept_b — memory_b is authoritative; memory_a is invalidated
    dismiss  — both memories remain live (sources legitimately differ)

A "conflict_resolved" audit event is appended to the SEC 17a-4 chain on every
resolution so the decision is tamper-evident.
"""
from __future__ import annotations

from datetime import datetime
from typing import Optional
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession

from ..db import get_db
from ..memory_service import list_conflicts, resolve_conflict
from ..mutation_safety import reject_non_replayable_idempotency_key
from ..schemas import ConflictListResult, ConflictResolveRequest, ConflictResolveResult
from .deps import AuthContext, get_auth

router = APIRouter(prefix="/v1", tags=["conflicts"])


@router.get("/conflicts", response_model=ConflictListResult)
async def get_conflicts(
    status: Optional[str] = Query(
        default="open",
        description="Filter by status: open | accept_a | accept_b | dismissed",
    ),
    limit: int = Query(default=50, ge=1, le=500),
    after_detected_at: datetime | None = Query(default=None),
    after_id: UUID | None = Query(default=None),
    auth: AuthContext = Depends(get_auth),
    db: AsyncSession = Depends(get_db),
):
    """
    List conflict flags for this namespace.

    By default returns only ``open`` conflicts — those awaiting human review.
    Pass ``status=`` with any value to filter, or ``status=`` with an empty
    string (or omit) to see all statuses.

    Each conflict includes the decrypted content of both conflicting memories
    so the reviewer can decide which source to trust.
    """
    auth.require("read")
    if (after_detected_at is None) != (after_id is None):
        raise HTTPException(
            status_code=422,
            detail="after_detected_at and after_id must be supplied together",
        )
    # Empty string query param → no status filter (all conflicts)
    effective_status = status if status else None
    return await list_conflicts(
        db, auth.namespace, status=effective_status, limit=limit,
        barrier_override=auth.barrier_group,
        after_detected_at=after_detected_at,
        after_id=after_id,
    )


@router.post(
    "/conflicts/{conflict_id}/resolve",
    response_model=ConflictResolveResult,
    dependencies=[Depends(reject_non_replayable_idempotency_key)],
)
async def resolve_conflict_endpoint(
    conflict_id: UUID,
    req: ConflictResolveRequest,
    auth: AuthContext = Depends(get_auth),
    db: AsyncSession = Depends(get_db),
):
    """
    Resolve a conflict flag.

    ``resolution`` must be one of:

    * ``accept_a`` — memory_a is authoritative; memory_b is invalidated
    * ``accept_b`` — memory_b is authoritative; memory_a is invalidated
    * ``dismiss``  — both memories remain live

    A ``conflict_resolved`` event is appended to the namespace audit chain
    regardless of which resolution is chosen, recording the decision and
    optional reviewer note for regulatory examination.
    """
    auth.require("write")
    return await resolve_conflict(
        db, auth.namespace, conflict_id, req,
        barrier_override=auth.barrier_group,
    )
