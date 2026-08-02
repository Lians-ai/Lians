from typing import Optional
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Header, HTTPException
from fastapi.responses import JSONResponse
from sqlalchemy.ext.asyncio import AsyncSession

from ..db import get_db
from ..config import get_settings
from ..admission_service import (
    enqueue_pending,
    evaluate_memory_admission,
    record_rejection,
)
from ..schemas import (
    MemoryAdd, MemoryOut, RecallRequest, RecallResult,
    MemoryBatchAdd, MemoryBatchResult, MemoryLineageResult,
    MessageIngestRequest,
    FactHistoryResult, ContextRequest, ContextResult,
    MemoryFeedbackCreate, MemoryFeedbackOut, MemoryLearningSummary,
    MemoryReviewResolve, MemoryReviewResult,
    MemoryMaintenanceResult,
)
from ..memory_service import (
    add_memory_idempotent, recall_memories, batch_add_memories,
    get_memory_lineage, get_structured_fact_history, assemble_context,
)
from ..adapters import get_adapter
from .deps import get_auth, AuthContext
from ..feedback_service import (
    record_memory_feedback, memory_learning_summary, resolve_memory_review,
    run_memory_maintenance,
)

router = APIRouter(prefix="/v1", tags=["memory"])


@router.post("/memories/{memory_id}/feedback", response_model=MemoryFeedbackOut)
async def create_memory_feedback(
    memory_id: UUID,
    req: MemoryFeedbackCreate,
    auth: AuthContext = Depends(get_auth),
    db: AsyncSession = Depends(get_db),
):
    """Record an outcome signal and apply the configured safe learning policy."""
    auth.require("write")
    try:
        return await record_memory_feedback(db, auth.namespace, memory_id, req)
    except LookupError:
        raise HTTPException(status_code=404, detail="Memory not found") from None


@router.get("/memory-learning/summary", response_model=MemoryLearningSummary)
async def get_memory_learning_summary(
    agent_id: Optional[str] = None,
    auth: AuthContext = Depends(get_auth),
    db: AsyncSession = Depends(get_db),
):
    auth.require("read")
    return await memory_learning_summary(db, auth.namespace, agent_id)


@router.post("/memories/{memory_id}/review", response_model=MemoryReviewResult)
async def resolve_learning_review(
    memory_id: UUID,
    req: MemoryReviewResolve,
    auth: AuthContext = Depends(get_auth),
    db: AsyncSession = Depends(get_db),
):
    """Keep or retire a feedback-flagged memory without rewriting history."""
    auth.require("write")
    try:
        return await resolve_memory_review(db, auth.namespace, memory_id, req)
    except LookupError:
        raise HTTPException(status_code=404, detail="Memory not found") from None
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from None


@router.post("/memory-learning/maintenance", response_model=MemoryMaintenanceResult)
async def trigger_learning_maintenance(
    dry_run: bool = Query(default=True),
    min_signals: int = Query(default=3, ge=1, le=100),
    auth: AuthContext = Depends(get_auth),
    db: AsyncSession = Depends(get_db),
):
    auth.require("admin")
    return await run_memory_maintenance(
        db, auth.namespace, min_signals=min_signals, dry_run=dry_run,
    )


@router.post("/memories", response_model=MemoryOut)
async def create_memory(
    req: MemoryAdd,
    auth: AuthContext = Depends(get_auth),
    db: AsyncSession = Depends(get_db),
    idempotency_key: Optional[str] = Header(default=None, alias="Idempotency-Key"),
):
    """
    Add a memory. Supply an ``Idempotency-Key`` header to make the write safe to
    retry.

    Memory admission control runs first (config ``admission_mode``): a write that
    looks like prompt injection or comes from a blocked source is **rejected**
    (422); one carrying PII/PHI/MNPI is **held for review** (202) in enforce mode.
    In monitor mode (default) everything is admitted but tagged under
    ``metadata._admission``.
    """
    auth.require("write")

    settings = get_settings()
    decision = evaluate_memory_admission(
        req,
        mode=settings.admission_mode,
        blocked_sources=settings.admission_blocked_sources,
    )

    if decision.action == "reject":
        await record_rejection(db, auth.namespace, req.agent_id, decision)
        raise HTTPException(status_code=422, detail={
            "status": "rejected", "risk_tags": decision.risk_tags, "reasons": decision.reasons,
        })

    if decision.action == "review":
        pending = await enqueue_pending(
            db, auth.namespace, req, decision,
            barrier_override=auth.barrier_group,
        )
        return JSONResponse(status_code=202, content={
            "status": "held_for_review", "pending_id": str(pending.id),
            "risk_tags": decision.risk_tags, "reasons": decision.reasons,
        })

    # admitted — record any risk findings on the memory for downstream visibility
    return await add_memory_idempotent(
        db, auth.namespace, req, idempotency_key, barrier_override=auth.barrier_group,
    )


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
    settings = get_settings()
    for item in req.memories:
        decision = evaluate_memory_admission(
            item,
            mode=settings.admission_mode,
            blocked_sources=settings.admission_blocked_sources,
        )
        if decision.action == "reject":
            await record_rejection(db, auth.namespace, item.agent_id, decision)
            raise HTTPException(status_code=422, detail={
                "status": "rejected",
                "risk_tags": decision.risk_tags,
                "reasons": decision.reasons,
            })
        if decision.action == "review":
            pending = await enqueue_pending(
                db, auth.namespace, item, decision,
                barrier_override=auth.barrier_group,
            )
            raise HTTPException(status_code=422, detail={
                "status": "held_for_review",
                "pending_id": str(pending.id),
                "risk_tags": decision.risk_tags,
                "reasons": decision.reasons,
            })
    return await batch_add_memories(
        db, auth.namespace, req.memories, barrier_override=auth.barrier_group
    )


@router.post("/memories/messages", response_model=MemoryBatchResult)
async def ingest_messages(
    req: MessageIngestRequest,
    auth: AuthContext = Depends(get_auth),
    db: AsyncSession = Depends(get_db),
):
    """Ingest standard chat messages through the engine's canonical write path."""
    from datetime import datetime, timezone

    allowed = {"user", "assistant", "system", "tool"}
    roles = set(req.roles)
    if not roles <= allowed:
        raise HTTPException(status_code=422, detail="roles contains an unsupported role")
    default_time = req.event_time or datetime.now(timezone.utc)
    memories = []
    for index, message in enumerate(req.messages):
        if message.role not in roles:
            continue
        memories.append(
            MemoryAdd(
                agent_id=req.agent_id,
                content=message.content,
                event_time=message.event_time or default_time,
                source=req.source,
                subject_id=req.subject_id,
                metadata={
                    **req.metadata,
                    **message.metadata,
                    "role": message.role,
                    "message_index": index,
                },
                importance=req.importance,
            )
        )
    if not memories:
        return MemoryBatchResult(added=0, memories=[])
    return await batch_create_memories(
        MemoryBatchAdd(memories=memories),
        auth,
        db,
    )


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
    return await get_memory_lineage(
        db, auth.namespace, memory_id, barrier_override=auth.barrier_group
    )


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
        db, auth.namespace, agent_id, key_values, adapter, limit,
        barrier_override=auth.barrier_group,
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
    envelope = None
    if req.decision_envelope_id is not None:
        auth.require("write")
        from ..decision_evidence import get_envelope

        envelope = await get_envelope(
            db,
            auth.namespace,
            req.decision_envelope_id,
            auth.barrier_group,
        )
        if envelope is None:
            raise HTTPException(status_code=422, detail="Decision envelope not found")
    result = await recall_memories(
        db, auth.namespace, req, barrier_override=auth.barrier_group
    )
    if envelope is not None:
        from ..decision_evidence import attach_recall_receipt

        await attach_recall_receipt(
            db,
            envelope,
            result.receipt_sha256,
            result.receipt,
        )
        await db.commit()
    return result


@router.post("/context", response_model=ContextResult)
async def context(
    req: ContextRequest,
    auth: AuthContext = Depends(get_auth),
    db: AsyncSession = Depends(get_db),
):
    """
    Build a token-budgeted, ready-to-inject context block from recall — one call
    to get the "memory context" string for a prompt. Facts are bitemporal, so the
    block never contains stale revisions; pass ``as_of`` for point-in-time context,
    ``mmr: true`` for diversity reranking, and ``max_tokens`` to cap the budget.
    """
    auth.require("read")
    envelope = None
    if req.decision_envelope_id is not None:
        auth.require("write")
        from ..decision_evidence import get_envelope

        envelope = await get_envelope(
            db,
            auth.namespace,
            req.decision_envelope_id,
            auth.barrier_group,
        )
        if envelope is None:
            raise HTTPException(status_code=422, detail="Decision envelope not found")
    result = await assemble_context(
        db, auth.namespace, req, barrier_override=auth.barrier_group
    )
    if envelope is not None:
        from ..decision_evidence import attach_recall_receipt

        await attach_recall_receipt(
            db,
            envelope,
            result.receipt_sha256,
            result.receipt,
        )
        await db.commit()
    return result
