from typing import Optional
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Header
from sqlalchemy.ext.asyncio import AsyncSession

from ..db import get_db
from ..schemas import (
    MemoryAdd, MemoryOut, RecallRequest, RecallResult,
    MemoryBatchAdd, MemoryBatchResult, MemoryLineageResult,
    FactHistoryResult,
)
from ..memory_service import (
    add_memory, add_memory_idempotent, recall_memories, batch_add_memories,
    get_memory_lineage, get_structured_fact_history,
)
from ..adapters import get_adapter
from .deps import get_auth, AuthContext

router = APIRouter(prefix="/v1", tags=["memory"])


@router.post("/memories", response_model=MemoryOut)
async def create_memory(
    req: MemoryAdd,
    auth: AuthContext = Depends(get_auth),
    db: AsyncSession = Depends(get_db),
    idempotency_key: Optional[str] = Header(default=None, alias="Idempotency-Key"),
):
    """
    Add a memory. Supply an ``Idempotency-Key`` header to make the write safe to
    retry: a repeated request with the same key returns the original memory
    instead of inserting a duplicate.
    """
    auth.require("write")
    return await add_memory_idempotent(db, auth.namespace, req, idempotency_key)


@router.post("/memories/batch", response_model=MemoryBatchResult)
async def batch_create_memories(
    req: MemoryBatchAdd,
    auth: AuthContext = Depends(get_auth),
    db: AsyncSession = Depends(get_db),
):
    """
    Add multiple memories in a single request.

    Memories are processed sequentially so that a later item in the batch can
    supersede an earlier one (e.g., when loading a time-series of revisions).
    Each item runs the full supersession funnel and audit-log write.
    """
    auth.require("write")
    return await batch_add_memories(db, auth.namespace, req.memories)


@router.get("/memories/{memory_id}/lineage", response_model=MemoryLineageResult)
async def memory_lineage(
    memory_id: UUID,
    auth: AuthContext = Depends(get_auth),
    db: AsyncSession = Depends(get_db),
):
    """
    Return the full belief provenance chain for a memory.

    Traverses both backward (to find the oldest ancestor) and forward (to find
    the current live tip), then returns every version with the supersession
    metadata (relation, confidence, LLM rationale) connecting each pair.

    Use this endpoint to answer regulator questions such as:
    "What did the system believe about AAPL earnings guidance on 2026-03-01,
    and how did that belief evolve before and after that date?"

    The queried memory may be anywhere in the chain — root, tip, or middle.
    ``nodes`` are always returned oldest-first.
    """
    auth.require("read")
    return await get_memory_lineage(db, auth.namespace, memory_id)


@router.get("/facts/history", response_model=FactHistoryResult)
async def fact_history(
    ticker: str = Query(..., description="Ticker, ISIN, CUSIP, or company name"),
    metric: str = Query(..., description="Metric/field name, e.g. 'eps', 'price_target'"),
    agent_id: str = Query(..., description="Agent to query"),
    limit: int = Query(100, ge=1, le=500),
    auth: AuthContext = Depends(get_auth),
    db: AsyncSession = Depends(get_db),
):
    """
    Return every recorded version of a structured fact, ordered by event_time ascending.

    This is the time-series complement to lineage: instead of navigating from a
    known memory_id, the caller queries by what they know — the ticker and metric
    they care about.  Superseded versions are included so analysts can see how a
    fact evolved.

    Entity normalization is applied automatically — passing 'Apple Inc.',
    'US0378331005' (ISIN), or '037833100' (CUSIP) all return the same AAPL series.

    Example use case: ``GET /v1/facts/history?ticker=AAPL&metric=eps&agent_id=equity-desk``
    """
    auth.require("read")
    adapter = get_adapter()
    key_values = {
        "ticker": adapter.normalize("ticker", ticker),
        "metric": adapter.normalize("metric", metric),
    }
    items = await get_structured_fact_history(
        db, auth.namespace, agent_id, key_values, adapter, limit
    )
    return FactHistoryResult(
        ticker=key_values["ticker"],
        metric=key_values["metric"],
        agent_id=agent_id,
        namespace=auth.namespace,
        total=len(items),
        items=items,
    )


@router.post("/recall", response_model=RecallResult)
async def recall(
    req: RecallRequest,
    auth: AuthContext = Depends(get_auth),
    db: AsyncSession = Depends(get_db),
):
    auth.require("read")
    return await recall_memories(db, auth.namespace, req)
