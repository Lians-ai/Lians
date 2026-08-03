"""
Relationship-graph routes — the knowledge-graph layer.

    POST /v1/graph/relate      — assert an edge (src --rel_type--> dst)
    POST /v1/graph/unrelate    — invalidate a live edge (sets valid_to)
    GET  /v1/graph/neighbors   — entities within N hops (optional as_of)
    GET  /v1/graph/path        — shortest connection between two entities

The path query is the conflict-of-interest / related-party / referral-reachability
question; every read accepts ``as_of`` for point-in-time traversal. Edges live in
the same audit chain and information barrier as memories.
"""
from __future__ import annotations

from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession

from .. import graph_service
from ..db import get_db
from ..mutation_safety import reject_non_replayable_idempotency_key
from ..schemas import (
    ExtractRequest,
    ExtractResult,
    NeighborsResult,
    PathResult,
    RelateRequest,
    RelateResult,
    UnrelateRequest,
)
from .deps import AuthContext, get_auth

router = APIRouter(prefix="/v1/graph", tags=["graph"])

_GRAPH_EXTRACT_RESPONSES = {
    409: {
        "description": (
            "Exclusive candidates conflict before mutation "
            "(`graph_extraction_exclusive_conflict`)"
        )
    },
    413: {
        "description": (
            "The complete extraction candidate set exceeds a row, field, byte, "
            "or cumulative-exclusive capacity ceiling. Stable codes: "
            "`graph_extraction_candidate_capacity_exceeded`, "
            "`graph_extraction_candidate_field_capacity_exceeded`, "
            "`graph_extraction_candidate_bytes_exceeded`, and "
            "`graph_extraction_exclusive_capacity_exceeded`"
        )
    },
    503: {
        "description": (
            "The extractor returned an invalid candidate set or the authoritative "
            "graph mutation could not be completed safely. Stable codes include "
            "`graph_extraction_candidate_invalid`, "
            "`graph_exclusive_invalidation_capacity_exceeded`, "
            "`graph_mutation_candidate_capacity_exceeded`, and "
            "`graph_live_edge_invariant_violation`"
        )
    },
}


def _raise_graph_mutation_unavailable(
    exc: graph_service.GraphMutationDecisionUnavailable,
) -> None:
    raise HTTPException(
        status_code=exc.status_code,
        detail={"code": exc.code, "message": exc.public_message},
    ) from exc


@router.post(
    "/relate",
    response_model=RelateResult,
    dependencies=[Depends(reject_non_replayable_idempotency_key)],
)
async def relate(
    req: RelateRequest,
    auth: AuthContext = Depends(get_auth),
    db: AsyncSession = Depends(get_db),
):
    auth.require("write")
    try:
        edge = await graph_service.relate(
            db, auth.namespace,
            agent_id=req.agent_id,
            src_entity=req.src_entity,
            rel_type=req.rel_type,
            dst_entity=req.dst_entity,
            event_time=req.event_time,
            exclusive=req.exclusive,
            subject_id=req.subject_id,
            source=req.source,
            metadata=req.metadata,
            normalize=req.normalize,
            barrier_override=auth.barrier_group,
        )
    except graph_service.GraphMutationDecisionUnavailable as exc:
        _raise_graph_mutation_unavailable(exc)
    return RelateResult(
        id=edge.id,
        src_entity=edge.src_entity,
        rel_type=edge.rel_type,
        dst_entity=edge.dst_entity,
        event_time=edge.event_time,
        valid_to=edge.valid_to,
    )


@router.post(
    "/extract",
    response_model=ExtractResult,
    dependencies=[Depends(reject_non_replayable_idempotency_key)],
    responses=_GRAPH_EXTRACT_RESPONSES,
)
async def extract(
    req: ExtractRequest,
    auth: AuthContext = Depends(get_auth),
    db: AsyncSession = Depends(get_db),
):
    """
    Extract relationship edges from unstructured text and write them — the
    Graphiti-style "build the graph for me" convenience, but rule-based and
    deterministic by default (auditable), with opt-in LLM extraction. Every
    extracted edge lands in the audit chain and inside the information barrier.
    """
    auth.require("write")
    try:
        result = await graph_service.extract_and_relate(
            db, auth.namespace,
            agent_id=req.agent_id, text=req.text, event_time=req.event_time,
            normalize=req.normalize, exclusive=req.exclusive, use_llm=req.use_llm,
            barrier_override=auth.barrier_group,
        )
    except graph_service.GraphMutationDecisionUnavailable as exc:
        _raise_graph_mutation_unavailable(exc)
    return ExtractResult(**result)


@router.post(
    "/unrelate",
    dependencies=[Depends(reject_non_replayable_idempotency_key)],
)
async def unrelate(
    req: UnrelateRequest,
    auth: AuthContext = Depends(get_auth),
    db: AsyncSession = Depends(get_db),
):
    auth.require("write")
    try:
        count = await graph_service.unrelate(
            db, auth.namespace,
            agent_id=req.agent_id,
            src_entity=req.src_entity,
            rel_type=req.rel_type,
            dst_entity=req.dst_entity,
            event_time=req.event_time,
            normalize=req.normalize,
            barrier_override=auth.barrier_group,
        )
    except graph_service.GraphMutationDecisionUnavailable as exc:
        _raise_graph_mutation_unavailable(exc)
    return {"invalidated": count}


@router.get("/neighbors", response_model=NeighborsResult)
async def neighbors(
    entity: str = Query(..., description="Entity to expand from"),
    agent_id: str = Query(...),
    depth: int = Query(1, ge=1, le=6),
    direction: str = Query("any", pattern="^(any|in|out)$"),
    as_of: Optional[datetime] = Query(None, description="Point-in-time traversal"),
    rel_type: Optional[list[str]] = Query(
        None,
        max_length=100,
        description="Restrict to at most 100 relationship types",
    ),
    normalize: bool = Query(False),
    max_nodes: int = Query(5000, ge=1, le=20_000),
    max_edges: int = Query(20_000, ge=1, le=50_000),
    auth: AuthContext = Depends(get_auth),
    db: AsyncSession = Depends(get_db),
):
    auth.require("read")
    result = await graph_service.neighbors(
        db, auth.namespace, agent_id, entity,
        depth=depth, as_of=as_of, rel_types=rel_type,
        direction=direction, normalize=normalize,
        barrier_override=auth.barrier_group,
        max_nodes=max_nodes,
        max_edges=max_edges,
    )
    return NeighborsResult(**result)


@router.get("/path", response_model=PathResult)
async def path(
    src: str = Query(..., description="Source entity"),
    dst: str = Query(..., description="Destination entity"),
    agent_id: str = Query(...),
    max_depth: int = Query(4, ge=1, le=8),
    as_of: Optional[datetime] = Query(None),
    rel_type: Optional[list[str]] = Query(None, max_length=100),
    normalize: bool = Query(False),
    max_nodes: int = Query(5000, ge=1, le=20_000),
    max_edges: int = Query(20_000, ge=1, le=50_000),
    auth: AuthContext = Depends(get_auth),
    db: AsyncSession = Depends(get_db),
):
    """
    Shortest connection between two entities — the conflict-of-interest /
    related-party reachability query. ``connected: false`` is returned only
    after a complete bounded search; a budget-capped negative is ``null``.
    """
    auth.require("read")
    result = await graph_service.path(
        db, auth.namespace, agent_id, src, dst,
        max_depth=max_depth, as_of=as_of, rel_types=rel_type, normalize=normalize,
        barrier_override=auth.barrier_group,
        max_nodes=max_nodes,
        max_edges=max_edges,
    )
    return PathResult(**result)
