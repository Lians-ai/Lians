from typing import Optional
from uuid import UUID

from fastapi import APIRouter, Depends, Header, HTTPException, Query
from fastapi.responses import JSONResponse
from sqlalchemy.ext.asyncio import AsyncSession

from ..adapters import get_adapter
from ..admission import evaluate as evaluate_admission
from ..admission_service import enqueue_pending, record_rejection
from ..config import get_settings
from ..db import get_db
from ..idempotency import (
    IdempotencyConflict,
    IdempotencyReplayUnavailable,
    InvalidIdempotencyKey,
    InvalidIdempotencyRequest,
    OperationClaim,
    operation_claim,
)
from ..memory_service import (
    add_memory,
    assemble_context,
    batch_add_memories,
    get_memory_lineage,
    get_structured_fact_history,
    recall_memories,
    replay_memory_result,
)
from ..models import EventLog, PendingAdmission
from ..schemas import (
    ContextRequest,
    ContextResult,
    FactHistoryResult,
    MemoryAdd,
    MemoryBatchAdd,
    MemoryBatchResult,
    MemoryLineageResult,
    MemoryOut,
    RecallRequest,
    RecallResult,
)
from ..supersession import SupersessionDecisionUnavailable
from .deps import AuthContext, get_auth

router = APIRouter(prefix="/v1", tags=["memory"])

_MEMORY_CREATE_OPERATION = "memory.create"
_MEMORY_BATCH_OPERATION = "memory.batch_create"


def _request_context(req, auth: AuthContext) -> dict:
    return {
        "body": req,
        "barrier_group": auth.barrier_group,
        "principal_id": auth.principal_id,
        "auth_method": auth.auth_method,
    }


def _raise_idempotency_error(exc: Exception) -> None:
    if isinstance(exc, (InvalidIdempotencyKey, InvalidIdempotencyRequest)):
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if isinstance(exc, (IdempotencyConflict, IdempotencyReplayUnavailable)):
        if isinstance(exc, IdempotencyReplayUnavailable):
            from ..metrics import record_idempotency_outcome

            record_idempotency_outcome("replay_unavailable")
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    raise exc


def _raise_supersession_unavailable(
    exc: SupersessionDecisionUnavailable,
) -> None:
    raise HTTPException(
        status_code=503,
        detail={
            "code": exc.code,
            "message": exc.public_message,
        },
    ) from exc


async def _replay_admission_rejection(
    db: AsyncSession,
    namespace: str,
    claim: OperationClaim,
) -> None:
    if claim.replay is None or claim.replay.response_status != 422:
        raise IdempotencyReplayUnavailable("Invalid admission-rejection replay status")
    ids = claim.resource_ids
    if len(ids) != 1:
        raise IdempotencyReplayUnavailable("Invalid admission-rejection replay result")
    event = await db.get(EventLog, ids[0])
    if event is None or event.namespace != namespace or event.op != "admission_rejected":
        raise IdempotencyReplayUnavailable(
            "The committed admission-rejection result is unavailable"
        )
    payload = dict(event.payload or {})
    claim.replay_served()
    raise HTTPException(
        status_code=422,
        detail={
            "status": "rejected",
            "risk_tags": list(payload.get("risk_tags") or []),
            "reasons": list(payload.get("reasons") or []),
        },
    )


async def _replay_pending_admission(
    db: AsyncSession,
    namespace: str,
    claim: OperationClaim,
    *,
    batch: bool,
):
    expected_status = 422 if batch else 202
    if claim.replay is None or claim.replay.response_status != expected_status:
        raise IdempotencyReplayUnavailable("Invalid pending-admission replay status")
    ids = claim.resource_ids
    if len(ids) != 1:
        raise IdempotencyReplayUnavailable("Invalid pending-admission replay result")
    pending = await db.get(PendingAdmission, ids[0])
    if pending is None or pending.namespace != namespace:
        raise IdempotencyReplayUnavailable(
            "The committed pending-admission result is unavailable"
        )
    body = {
        "status": "held_for_review",
        "pending_id": str(pending.id),
        "risk_tags": list(pending.risk_tags or []),
        "reasons": list(pending.reasons or []),
    }
    claim.replay_served()
    if batch:
        raise HTTPException(status_code=422, detail=body)
    return JSONResponse(status_code=202, content=body)


async def _replay_memories(
    db: AsyncSession,
    auth: AuthContext,
    claim: OperationClaim,
    raw_subject_ids: list[str | None],
) -> list[MemoryOut]:
    if claim.replay is None or claim.replay.response_status != 200:
        raise IdempotencyReplayUnavailable("Invalid memory replay status")
    ids = claim.resource_ids
    if len(ids) != len(raw_subject_ids):
        raise IdempotencyReplayUnavailable("Invalid memory replay result cardinality")
    if len(set(ids)) != len(ids):
        raise IdempotencyReplayUnavailable("Duplicate memory IDs in replay result")
    return [
        await replay_memory_result(
            db,
            auth.namespace,
            memory_id,
            raw_subject_id=raw_subject_id,
            barrier_override=auth.barrier_group,
        )
        for memory_id, raw_subject_id in zip(ids, raw_subject_ids)
    ]


@router.post("/memories", response_model=MemoryOut)
async def create_memory(
    req: MemoryAdd,
    auth: AuthContext = Depends(get_auth),
    db: AsyncSession = Depends(get_db),
    idempotency_key: Optional[str] = Header(
        default=None,
        alias="Idempotency-Key",
        min_length=1,
        max_length=255,
    ),
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
    blocked = {s.strip().lower() for s in settings.admission_blocked_sources.split(",") if s.strip()}
    decision = evaluate_admission(
        req.content, req.source, mode=settings.admission_mode, blocked_sources=blocked,
    )

    raw_subject_id = req.subject_id
    try:
        async with operation_claim(
            db,
            namespace=auth.namespace,
            operation=_MEMORY_CREATE_OPERATION,
            key=idempotency_key,
            request=_request_context(req, auth),
        ) as claim:
            if claim.is_replay:
                kind = claim.replay.resource_kind
                if kind == "memory":
                    replay = (
                        await _replay_memories(db, auth, claim, [raw_subject_id])
                    )[0]
                    claim.replay_served()
                    return replay
                if kind == "pending_admission":
                    return await _replay_pending_admission(
                        db, auth.namespace, claim, batch=False
                    )
                if kind == "admission_rejection":
                    await _replay_admission_rejection(db, auth.namespace, claim)
                raise IdempotencyReplayUnavailable(
                    "The committed idempotency result has an unexpected resource kind"
                )

            if decision.action == "reject":
                event = await record_rejection(
                    db,
                    auth.namespace,
                    req.agent_id,
                    decision,
                    commit=False,
                )
                await claim.complete_and_commit(
                    resource_kind="admission_rejection",
                    resource_ids=[event.id],
                    response_status=422,
                )
                raise HTTPException(status_code=422, detail={
                    "status": "rejected",
                    "risk_tags": decision.risk_tags,
                    "reasons": decision.reasons,
                })

            if decision.action == "review":
                pending = await enqueue_pending(
                    db,
                    auth.namespace,
                    req,
                    decision,
                    barrier_override=auth.barrier_group,
                    commit=False,
                )
                await claim.complete_and_commit(
                    resource_kind="pending_admission",
                    resource_ids=[pending.id],
                    response_status=202,
                )
                return JSONResponse(status_code=202, content={
                    "status": "held_for_review",
                    "pending_id": str(pending.id),
                    "risk_tags": decision.risk_tags,
                    "reasons": decision.reasons,
                })

            # Admitted — preserve monitor-mode findings on the authoritative row.
            if decision.risk_tags:
                req.metadata = {
                    **(req.metadata or {}),
                    "_admission": {
                        "action": "admit",
                        "risk_tags": decision.risk_tags,
                    },
                }
            result = await add_memory(
                db,
                auth.namespace,
                req,
                barrier_override=auth.barrier_group,
                commit=False,
            )
            await claim.complete_and_commit(
                resource_kind="memory",
                resource_ids=[result.id],
                response_status=200,
            )
            return result
    except (
        InvalidIdempotencyKey,
        InvalidIdempotencyRequest,
        IdempotencyConflict,
        IdempotencyReplayUnavailable,
    ) as exc:
        _raise_idempotency_error(exc)
    except SupersessionDecisionUnavailable as exc:
        _raise_supersession_unavailable(exc)


@router.post("/memories/batch", response_model=MemoryBatchResult)
async def batch_create_memories(
    req: MemoryBatchAdd,
    auth: AuthContext = Depends(get_auth),
    db: AsyncSession = Depends(get_db),
    idempotency_key: Optional[str] = Header(
        default=None,
        alias="Idempotency-Key",
        min_length=1,
        max_length=255,
    ),
):
    """
    Add multiple memories in a single request.

    Memories are processed sequentially so that a later item in the batch can
    supersede an earlier one (e.g., when loading a time-series of revisions).
    Each item runs the full supersession funnel and audit-log write.
    """
    auth.require("write")
    settings = get_settings()
    blocked = {
        s.strip().lower()
        for s in settings.admission_blocked_sources.split(",")
        if s.strip()
    }
    admission_results = []
    for item in req.memories:
        decision = evaluate_admission(
            item.content,
            item.source,
            mode=settings.admission_mode,
            blocked_sources=blocked,
        )
        admission_results.append((item, decision))

    raw_subject_ids = [item.subject_id for item in req.memories]
    try:
        async with operation_claim(
            db,
            namespace=auth.namespace,
            operation=_MEMORY_BATCH_OPERATION,
            key=idempotency_key,
            request=_request_context(req, auth),
        ) as claim:
            if claim.is_replay:
                kind = claim.replay.resource_kind
                if kind == "memory_batch":
                    memories = await _replay_memories(
                        db, auth, claim, raw_subject_ids
                    )
                    claim.replay_served()
                    return MemoryBatchResult(added=len(memories), memories=memories)
                if kind == "pending_admission":
                    return await _replay_pending_admission(
                        db, auth.namespace, claim, batch=True
                    )
                if kind == "admission_rejection":
                    await _replay_admission_rejection(db, auth.namespace, claim)
                raise IdempotencyReplayUnavailable(
                    "The committed batch idempotency result has an unexpected resource kind"
                )

            for item, decision in admission_results:
                if decision.action == "reject":
                    event = await record_rejection(
                        db,
                        auth.namespace,
                        item.agent_id,
                        decision,
                        commit=False,
                    )
                    await claim.complete_and_commit(
                        resource_kind="admission_rejection",
                        resource_ids=[event.id],
                        response_status=422,
                    )
                    raise HTTPException(status_code=422, detail={
                        "status": "rejected",
                        "risk_tags": decision.risk_tags,
                        "reasons": decision.reasons,
                    })
                if decision.action == "review":
                    pending = await enqueue_pending(
                        db,
                        auth.namespace,
                        item,
                        decision,
                        barrier_override=auth.barrier_group,
                        commit=False,
                    )
                    await claim.complete_and_commit(
                        resource_kind="pending_admission",
                        resource_ids=[pending.id],
                        response_status=422,
                    )
                    raise HTTPException(status_code=422, detail={
                        "status": "held_for_review",
                        "pending_id": str(pending.id),
                        "risk_tags": decision.risk_tags,
                        "reasons": decision.reasons,
                    })
                if decision.risk_tags:
                    item.metadata = {
                        **(item.metadata or {}),
                        "_admission": {
                            "action": "admit",
                            "risk_tags": decision.risk_tags,
                        },
                    }
            result = await batch_add_memories(
                db,
                auth.namespace,
                req.memories,
                barrier_override=auth.barrier_group,
                commit=False,
            )
            await claim.complete_and_commit(
                resource_kind="memory_batch",
                resource_ids=[memory.id for memory in result.memories],
                response_status=200,
            )
            return result
    except (
        InvalidIdempotencyKey,
        InvalidIdempotencyRequest,
        IdempotencyConflict,
        IdempotencyReplayUnavailable,
    ) as exc:
        _raise_idempotency_error(exc)
    except SupersessionDecisionUnavailable as exc:
        _raise_supersession_unavailable(exc)


@router.get("/memories/{memory_id}/lineage", response_model=MemoryLineageResult)
async def memory_lineage(
    memory_id: UUID,
    max_nodes: int = Query(default=1000, ge=3, le=5000),
    auth: AuthContext = Depends(get_auth),
    db: AsyncSession = Depends(get_db),
):
    """
    Return the bounded belief-provenance graph for a memory.

    Traverses both backward (to find the oldest ancestor) and forward (to find
    the current live tip), then returns every version with the supersession
    metadata (relation, confidence, LLM rationale) connecting each pair.

    Use this endpoint to answer regulator questions such as:
    "What did the system believe about AAPL earnings guidance on 2026-03-01,
    and how did that belief evolve before and after that date?"

    The queried memory may be anywhere in the graph. Nodes are returned in
    deterministic topological order; callers must follow explicit edge IDs
    because several older facts may legitimately converge on one successor.
    Completeness and immutable audit-event binding are reported separately.
    """
    auth.require("read")
    return await get_memory_lineage(
        db,
        auth.namespace,
        memory_id,
        barrier_override=auth.barrier_group,
        max_nodes=max_nodes,
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
    Return matches from a bounded structured-fact scan, ordered by event time.

    This is the time-series complement to lineage: instead of navigating from a
    known memory_id, the caller queries by what they know — the ticker and metric
    they care about. Superseded versions are included. Check ``scan_complete``
    and ``total_is_lower_bound`` before treating the result as exhaustive.

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
    diagnostics: dict = {}
    items = await get_structured_fact_history(
        db, auth.namespace, agent_id, key_values, adapter, limit,
        barrier_override=auth.barrier_group,
        diagnostics=diagnostics,
    )
    return FactHistoryResult(
        ticker=key_values["ticker"],
        metric=key_values["metric"],
        agent_id=agent_id,
        namespace=auth.namespace,
        total=int(diagnostics.get("matches_in_scan", len(items))),
        total_is_lower_bound=bool(diagnostics.get("total_is_lower_bound", False)),
        has_more=bool(diagnostics.get("has_more", False)),
        scan_complete=bool(diagnostics.get("scan_complete", True)),
        rows_scanned=int(diagnostics.get("rows_scanned", len(items))),
        scan_limit=int(diagnostics.get("scan_limit", len(items))),
        items=items,
    )


@router.post("/recall", response_model=RecallResult)
async def recall(
    req: RecallRequest,
    auth: AuthContext = Depends(get_auth),
    db: AsyncSession = Depends(get_db),
):
    auth.require("read")
    return await recall_memories(db, auth.namespace, req, barrier_override=auth.barrier_group)


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
    return await assemble_context(db, auth.namespace, req, barrier_override=auth.barrier_group)
