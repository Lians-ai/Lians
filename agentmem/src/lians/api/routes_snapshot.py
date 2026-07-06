"""
GET /v1/snapshot — audit reconstruction: complete agent knowledge state at T.

This is the "audit reconstruction as a product surface" from SCALE.md §4:
  "Show me the agent's complete knowledge state as of 2025-03-14T09:30."
  One call. This is the compliance demo that closes the deal.

Different from /v1/recall (vector search → top-k relevant):
  /v1/snapshot is exhaustive — every fact valid at T, no relevance filter.
  SEC examiners don't want "the most relevant 5 memories" — they want everything.
"""
from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, Depends, Query
from fastapi.responses import PlainTextResponse
from sqlalchemy.ext.asyncio import AsyncSession

from ..db import get_db
from ..schemas import KnowledgeSnapshot, MarkdownExportResult
from ..memory_service import get_knowledge_snapshot
from ..export_markdown import export_memory_markdown
from .deps import get_auth, AuthContext

router = APIRouter(prefix="/v1", tags=["snapshot"])


@router.get("/snapshot", response_model=KnowledgeSnapshot)
async def knowledge_snapshot(
    agent_id: str = Query(..., description="Agent whose knowledge state to reconstruct"),
    as_of: datetime = Query(
        ...,
        description="Point-in-time checkpoint (ISO 8601 UTC). "
                    "Returns every memory valid at this timestamp.",
    ),
    limit: int = Query(1000, ge=1, le=10000),
    auth: AuthContext = Depends(get_auth),
    db: AsyncSession = Depends(get_db),
):
    """
    Reconstruct the complete knowledge state of an agent at a specific point in time.

    Returns every memory that was valid (`valid_from ≤ as_of < valid_to`) at the
    given timestamp, ordered by `event_time` ascending.  Erased content appears
    with `content: null` — the memory's existence and metadata are preserved.

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

    This endpoint is the one-call compliance demo that closes deals with risk
    committees and regulators.  mem0 has no temporal model.  Graphiti/Zep has
    temporal graph queries but no tamper-evident hash chain or compliance export API.
    """
    auth.require("read")
    items = await get_knowledge_snapshot(db, auth.namespace, agent_id, as_of, limit)
    return KnowledgeSnapshot(
        agent_id=agent_id,
        namespace=auth.namespace,
        as_of=as_of,
        total=len(items),
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
    Render the agent's complete point-in-time knowledge state as a signed,
    human-readable Markdown document.

    Same exhaustive fact set as `/v1/snapshot`, formatted for humans: YAML
    frontmatter, one section per fact with provenance, validity window, and
    materiality; erased facts appear as explicit erasure markers. The document's
    SHA-256 is written into the tamper-evident audit chain as an
    `export_markdown` event, and the footer states the hash, the anchoring
    event, and the verification procedure — an examiner (or a skeptical
    developer) can read exactly what the system knows and prove the statement
    was not altered after generation.
    """
    auth.require("read")
    result = await export_memory_markdown(db, auth.namespace, agent_id, as_of, limit)
    if raw:
        return PlainTextResponse(result.markdown, media_type="text/markdown; charset=utf-8")
    return MarkdownExportResult.model_validate(result)
