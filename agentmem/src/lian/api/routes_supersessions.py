"""
Supersession review queue — low-confidence supersessions flagged for human review.

In finance a wrong silent supersession (dropping the old number when it should
have been kept) is a compliance failure.  This route surfaces every supersession
event whose confidence is below the configured threshold so a compliance officer
or senior analyst can confirm or reject it before treating the old fact as stale.

GET /v1/supersessions/review
    Returns SupersessionReviewResult: a list of flagged events with metadata,
    sorted newest-first.  Optionally override the confidence threshold per call.

    Query params:
        threshold   float   override config.supersession_review_threshold
        limit       int     max items (default 50)
"""
from __future__ import annotations
from typing import Optional
from uuid import UUID

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from ..db import get_db
from ..schemas import SupersessionReviewResult, SupersessionAction, SupersessionActionResult
from ..memory_service import get_pending_supersessions, apply_supersession_action
from .deps import get_auth, AuthContext

router = APIRouter(prefix="/v1", tags=["supersessions"])


@router.get("/supersessions/review", response_model=SupersessionReviewResult)
async def review_supersessions(
    threshold: Optional[float] = Query(default=None, ge=0.0, le=1.0,
        description="Confidence threshold — events below this score are returned. "
                    "Defaults to config.supersession_review_threshold (0.75)."),
    limit: int = Query(default=50, ge=1, le=500),
    auth: AuthContext = Depends(get_auth),
    db: AsyncSession = Depends(get_db),
) -> SupersessionReviewResult:
    """
    Return supersession events that need human review.

    A low confidence score means the engine was uncertain whether the new fact
    genuinely supersedes the old one.  Finance teams should inspect these before
    relying on the most-recent value as the current fact.

    Typical workflow:
    - Poll this endpoint daily (or via webhook when confidence < threshold).
    - For each item, fetch the memory at memory_id and superseded_by to compare.
    - Confirm (accept the supersession) or reject (restore valid_to = NULL on the
      old memory via a future PATCH endpoint).
    """
    auth.require("read")
    return await get_pending_supersessions(
        db=db,
        namespace=auth.namespace,
        confidence_threshold=threshold,
        limit=limit,
    )


@router.patch(
    "/supersessions/{memory_id}",
    response_model=SupersessionActionResult,
    summary="Confirm or reject a supersession",
)
async def action_supersession(
    memory_id: UUID,
    body: SupersessionAction,
    auth: AuthContext = Depends(get_auth),
    db: AsyncSession = Depends(get_db),
) -> SupersessionActionResult:
    """
    Act on a supersession flagged for review.

    **confirm** — the supersession was correct. Writes an immutable audit event
    with the reviewer's note; the superseded memory remains closed.

    **reject** — the supersession was wrong. Restores the old memory as currently
    valid (`valid_to = NULL`), clears `superseded_by`, and writes an audit event.
    Both memories are now valid — the engine treated them as additive facts.

    After a reject, the old memory will appear in future recall results alongside
    the newer memory until a human explicitly supersedes or erases one of them.
    """
    auth.require("write")
    return await apply_supersession_action(db, auth.namespace, memory_id, body)
