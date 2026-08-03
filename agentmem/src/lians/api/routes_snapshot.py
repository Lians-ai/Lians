"""
GET /v1/snapshot — exact-count, paged agent knowledge state at T.

This is the "audit reconstruction as a product surface" from SCALE.md §4:
  "Show me the agent's knowledge state as of 2025-03-14T09:30."

Different from /v1/recall (vector search → top-k relevant):
  /v1/snapshot is unranked and keyset-paginated. Exact ``total`` and explicit
  completeness fields prevent one bounded page from masquerading as everything.
"""
from datetime import datetime, timezone
from typing import Optional
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import PlainTextResponse
from sqlalchemy.ext.asyncio import AsyncSession

from ..config import get_settings
from ..db import get_db
from ..export_markdown import ExportCapacityExceeded, export_memory_markdown
from ..memory_service import (
    count_knowledge_snapshot,
    get_knowledge_snapshot,
    measure_knowledge_snapshot_bytes,
)
from ..schemas import KnowledgeSnapshot, MarkdownExportResult
from .deps import AuthContext, get_auth

router = APIRouter(prefix="/v1", tags=["snapshot"])


@router.get("/snapshot", response_model=KnowledgeSnapshot)
async def knowledge_snapshot(
    agent_id: str = Query(..., description="Agent whose knowledge state to reconstruct"),
    as_of: datetime = Query(
        ...,
        description="Point-in-time checkpoint (ISO 8601 UTC). "
                    "Returns a deterministic page of memories valid at this timestamp.",
    ),
    limit: int = Query(1000, ge=1, le=10000),
    after_event_time: Optional[datetime] = Query(
        None,
        description="Keyset cursor event_time returned by the previous page",
    ),
    after_id: Optional[UUID] = Query(
        None,
        description="Keyset cursor memory ID returned by the previous page",
    ),
    recorded_as_of: Optional[datetime] = Query(
        None,
        description=(
            "Fixed transaction-time watermark returned by the first page. "
            "Retain it on every continuation request."
        ),
    ),
    auth: AuthContext = Depends(get_auth),
    db: AsyncSession = Depends(get_db),
):
    """
    Reconstruct a deterministic page of an agent's knowledge state at a point in time.

    Returns memories valid (`valid_from ≤ as_of < valid_to`) at the timestamp,
    ordered by `(event_time, id)`. ``total`` is exact. ``complete`` is true only
    when this response contains the whole snapshot; otherwise follow the cursor.
    Retain ``recorded_as_of`` across pages so later-ingested backdated facts do
    not move the transaction-time boundary during traversal.

    **Use cases:**

    - **Regulatory examination:** SEC/FINRA examiners can verify the agent's
      exact knowledge at any date without diving into application logs.
    - **Incident investigation:** "What did the agent know right before the
      suspicious trade at 09:31?"
    - **Backtest validation:** Pair with `/v1/backtest/check` — first confirm
      the snapshot contains only historically-valid facts, then reason about
      the agent's decisions with confidence.
    - **Drift analysis:** Compare snapshots across two dates to see which facts
      were added, superseded, or revised between T₁ and T₂.

    Callers must retain the completeness fields with any compliance export.
    """
    auth.require("read")
    if (after_event_time is None) != (after_id is None):
        raise HTTPException(422, "after_event_time and after_id must be supplied together")
    effective_recorded_as_of = recorded_as_of or datetime.now(timezone.utc)
    effective_recorded_as_of = (
        effective_recorded_as_of.replace(tzinfo=timezone.utc)
        if effective_recorded_as_of.tzinfo is None
        else effective_recorded_as_of.astimezone(timezone.utc)
    )
    page_rows, estimated_bytes = await measure_knowledge_snapshot_bytes(
        db,
        auth.namespace,
        agent_id,
        as_of,
        include_content=True,
        barrier_override=auth.barrier_group,
        recorded_as_of=effective_recorded_as_of,
        after_event_time=after_event_time,
        after_id=after_id,
        limit=limit + 1,
    )
    byte_limit = get_settings().content_export_page_bytes_limit
    if estimated_bytes > byte_limit:
        raise HTTPException(
            status_code=413,
            detail={
                "code": "snapshot_page_byte_capacity_exceeded",
                "message": "The requested snapshot page exceeds the response byte budget",
                "estimated_bytes": estimated_bytes,
                "byte_limit": byte_limit,
                "candidate_rows": page_rows,
                "requested_limit": limit,
            },
        )
    fetched = await get_knowledge_snapshot(
        db, auth.namespace, agent_id, as_of, limit,
        barrier_override=auth.barrier_group,
        recorded_as_of=effective_recorded_as_of,
        after_event_time=after_event_time,
        after_id=after_id,
    )
    has_more = page_rows > limit
    items = fetched
    total = await count_knowledge_snapshot(
        db,
        auth.namespace,
        agent_id,
        as_of,
        barrier_override=auth.barrier_group,
        recorded_as_of=effective_recorded_as_of,
    )
    next_row = items[-1] if has_more and items else None
    return KnowledgeSnapshot(
        agent_id=agent_id,
        namespace=auth.namespace,
        as_of=as_of,
        recorded_as_of=effective_recorded_as_of,
        total=total,
        returned=len(items),
        complete=(
            after_event_time is None and not has_more and total == len(items)
        ),
        has_more=has_more,
        next_event_time=next_row.event_time if next_row else None,
        next_id=next_row.id if next_row else None,
        items=items,
    )


@router.get("/snapshot/markdown")
async def snapshot_markdown(
    agent_id: str = Query(..., description="Agent whose memory statement to render"),
    as_of: Optional[datetime] = Query(
        None,
        description="Point-in-time checkpoint (ISO 8601 UTC). Default: now.",
    ),
    limit: int = Query(1000, ge=1, le=10000),
    raw: bool = Query(
        False,
        description="Return the bare Markdown document (text/markdown) instead of JSON.",
    ),
    auth: AuthContext = Depends(get_auth),
    db: AsyncSession = Depends(get_db),
):
    """
    Render one bounded point-in-time snapshot page as a signed Markdown document.

    Same unranked fact selection as `/v1/snapshot`, formatted for humans: YAML
    frontmatter, one section per fact with provenance, validity window, and
    materiality; erased facts appear as explicit erasure markers. The document's
    SHA-256 is written into the tamper-evident audit chain as an
    `export_markdown` event, and the footer states the hash, the anchoring
    event, and the verification procedure — an examiner (or a skeptical
    developer) can verify that page was not altered. The document discloses its
    exact snapshot total and whether the included page is complete.
    """
    auth.require("read")
    try:
        result = await export_memory_markdown(
            db,
            auth.namespace,
            agent_id,
            as_of,
            limit,
            barrier_override=auth.barrier_group,
            max_bytes=get_settings().content_export_page_bytes_limit,
        )
    except ExportCapacityExceeded as exc:
        raise HTTPException(
            status_code=413,
            detail={
                "code": exc.code,
                "message": exc.public_message,
                "estimated_bytes": exc.estimated_bytes,
                "byte_limit": exc.byte_limit,
            },
        ) from exc
    if raw:
        return PlainTextResponse(result.markdown, media_type="text/markdown; charset=utf-8")
    return MarkdownExportResult.model_validate(result)
