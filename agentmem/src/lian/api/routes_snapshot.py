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

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from ..db import get_db
from ..schemas import KnowledgeSnapshot
from ..memory_service import get_knowledge_snapshot
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
