"""Constrained runtime routing, exact-cache, budget, and concurrency APIs."""

from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from ..audit_chain import chain_log
from ..db import get_db
from ..improvement_models import AgentVersion
from ..improvement_service import ImprovementNotFound, visible_by_id
from ..mutation_safety import reject_non_replayable_idempotency_key
from ..runtime_models import CacheDecision, ConcurrencyPlan, RoutingDecision, RuntimePolicyVersion
from ..runtime_schemas import (
    CacheAccessRequest,
    CacheDecisionOut,
    ConcurrencyPlanOut,
    ConcurrencyPlanRequest,
    RouteDecideRequest,
    RoutingDecisionOut,
    RuntimePolicyCreate,
    RuntimePolicyOut,
)
from ..runtime_service import (
    RuntimeContractError,
    access_runtime_cache,
    cache_decision_out,
    concurrency_plan_out,
    create_concurrency_plan,
    create_routing_decision,
    create_runtime_policy,
    routing_decision_out,
    runtime_policy_out,
)
from .deps import AuthContext, get_auth

runtime_router = APIRouter(prefix="/v1/runtime", tags=["runtime-control"])
routing_router = APIRouter(prefix="/v1/routing", tags=["runtime-routing"])
cache_router = APIRouter(prefix="/v1/cache", tags=["runtime-cache"])


def _principal(auth: AuthContext) -> str:
    if not auth.principal_id:
        raise HTTPException(status_code=401, detail="Authenticated principal identity required")
    return auth.principal_id


async def _audit_commit(
    db: AsyncSession,
    *,
    auth: AuthContext,
    operation: str,
    resource_type: str,
    resource_id: UUID,
    content_hash: str,
) -> None:
    await chain_log(
        db,
        namespace=auth.namespace,
        agent_id=_principal(auth),
        op=operation,
        content_hash=content_hash,
        payload={"resource_type": resource_type, "resource_id": str(resource_id)},
    )
    await db.commit()


@runtime_router.post(
    "/policies",
    response_model=RuntimePolicyOut,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(reject_non_replayable_idempotency_key)],
)
async def post_runtime_policy(
    body: RuntimePolicyCreate,
    auth: AuthContext = Depends(get_auth),
    db: AsyncSession = Depends(get_db),
) -> RuntimePolicyOut:
    auth.require("write")
    try:
        row = await create_runtime_policy(
            db,
            namespace=auth.namespace,
            barrier_group=auth.barrier_group,
            principal_ref=_principal(auth),
            body=body,
        )
        await _audit_commit(
            db,
            auth=auth,
            operation="runtime_policy_version_created",
            resource_type="runtime_policy_version",
            resource_id=row.id,
            content_hash=row.policy_hash,
        )
    except IntegrityError as exc:
        await db.rollback()
        raise HTTPException(
            status_code=409, detail="Runtime policy version already exists"
        ) from exc
    return runtime_policy_out(row)


@runtime_router.get("/policies/{policy_id}", response_model=RuntimePolicyOut)
async def get_runtime_policy(
    policy_id: UUID,
    auth: AuthContext = Depends(get_auth),
    db: AsyncSession = Depends(get_db),
) -> RuntimePolicyOut:
    auth.require("read")
    try:
        row = await visible_by_id(
            db,
            RuntimePolicyVersion,
            policy_id,
            namespace=auth.namespace,
            barrier_group=auth.barrier_group,
        )
    except ImprovementNotFound as exc:
        raise HTTPException(status_code=404, detail="Runtime policy not found") from exc
    return runtime_policy_out(row)


@routing_router.post(
    "/decide",
    response_model=RoutingDecisionOut,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(reject_non_replayable_idempotency_key)],
)
async def post_routing_decision(
    body: RouteDecideRequest,
    auth: AuthContext = Depends(get_auth),
    db: AsyncSession = Depends(get_db),
) -> RoutingDecisionOut:
    auth.require("write")
    try:
        policy = await visible_by_id(
            db,
            RuntimePolicyVersion,
            body.runtime_policy_version_id,
            namespace=auth.namespace,
            barrier_group=auth.barrier_group,
        )
        row = await create_routing_decision(
            db,
            policy=policy,
            namespace=auth.namespace,
            barrier_group=auth.barrier_group,
            principal_ref=_principal(auth),
            body=body,
        )
        await _audit_commit(
            db,
            auth=auth,
            operation="routing_decision_created",
            resource_type="routing_decision",
            resource_id=row.id,
            content_hash=row.decision_hash,
        )
    except ImprovementNotFound as exc:
        await db.rollback()
        raise HTTPException(status_code=404, detail="Routing dependency not found") from exc
    except RuntimeContractError as exc:
        await db.rollback()
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except IntegrityError as exc:
        await db.rollback()
        raise HTTPException(status_code=409, detail="Routing decision already exists") from exc
    return routing_decision_out(row, policy)


@routing_router.get("/decisions/{decision_id}", response_model=RoutingDecisionOut)
async def get_routing_decision(
    decision_id: UUID,
    auth: AuthContext = Depends(get_auth),
    db: AsyncSession = Depends(get_db),
) -> RoutingDecisionOut:
    auth.require("read")
    try:
        row = await visible_by_id(
            db,
            RoutingDecision,
            decision_id,
            namespace=auth.namespace,
            barrier_group=auth.barrier_group,
        )
        policy = await visible_by_id(
            db,
            RuntimePolicyVersion,
            row.runtime_policy_version_id,
            namespace=auth.namespace,
            barrier_group=auth.barrier_group,
        )
    except ImprovementNotFound as exc:
        raise HTTPException(status_code=404, detail="Routing decision not found") from exc
    return routing_decision_out(row, policy)


@cache_router.post(
    "/decide",
    response_model=CacheDecisionOut,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(reject_non_replayable_idempotency_key)],
)
async def post_cache_decision(
    body: CacheAccessRequest,
    auth: AuthContext = Depends(get_auth),
    db: AsyncSession = Depends(get_db),
) -> CacheDecisionOut:
    auth.require("write")
    try:
        policy = await visible_by_id(
            db,
            RuntimePolicyVersion,
            body.runtime_policy_version_id,
            namespace=auth.namespace,
            barrier_group=auth.barrier_group,
        )
        version = await visible_by_id(
            db,
            AgentVersion,
            body.agent_version_id,
            namespace=auth.namespace,
            barrier_group=auth.barrier_group,
        )
        row, payload = await access_runtime_cache(
            db,
            policy=policy,
            version=version,
            namespace=auth.namespace,
            barrier_group=auth.barrier_group,
            principal_ref=_principal(auth),
            body=body,
        )
        await _audit_commit(
            db,
            auth=auth,
            operation="cache_decision_created",
            resource_type="cache_decision",
            resource_id=row.id,
            content_hash=row.decision_hash,
        )
    except ImprovementNotFound as exc:
        await db.rollback()
        raise HTTPException(status_code=404, detail="Cache dependency not found") from exc
    except RuntimeContractError as exc:
        await db.rollback()
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return cache_decision_out(row, payload=payload)


@cache_router.get("/decisions/{decision_id}", response_model=CacheDecisionOut)
async def get_cache_decision(
    decision_id: UUID,
    auth: AuthContext = Depends(get_auth),
    db: AsyncSession = Depends(get_db),
) -> CacheDecisionOut:
    auth.require("read")
    try:
        row = await visible_by_id(
            db,
            CacheDecision,
            decision_id,
            namespace=auth.namespace,
            barrier_group=auth.barrier_group,
        )
    except ImprovementNotFound as exc:
        raise HTTPException(status_code=404, detail="Cache decision not found") from exc
    return cache_decision_out(row)


@runtime_router.post(
    "/concurrency/plan",
    response_model=ConcurrencyPlanOut,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(reject_non_replayable_idempotency_key)],
)
async def post_concurrency_plan(
    body: ConcurrencyPlanRequest,
    auth: AuthContext = Depends(get_auth),
    db: AsyncSession = Depends(get_db),
) -> ConcurrencyPlanOut:
    auth.require("write")
    try:
        await visible_by_id(
            db,
            AgentVersion,
            body.agent_version_id,
            namespace=auth.namespace,
            barrier_group=auth.barrier_group,
        )
        row = await create_concurrency_plan(
            db,
            namespace=auth.namespace,
            barrier_group=auth.barrier_group,
            principal_ref=_principal(auth),
            body=body,
        )
        await _audit_commit(
            db,
            auth=auth,
            operation="runtime_concurrency_plan_created",
            resource_type="runtime_concurrency_plan",
            resource_id=row.id,
            content_hash=row.plan_hash,
        )
    except ImprovementNotFound as exc:
        await db.rollback()
        raise HTTPException(status_code=404, detail="Agent version not found") from exc
    except RuntimeContractError as exc:
        await db.rollback()
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except IntegrityError as exc:
        await db.rollback()
        raise HTTPException(status_code=409, detail="Concurrency plan already exists") from exc
    return concurrency_plan_out(row, body.model_dump(mode="json")["calls"])


@runtime_router.get("/concurrency/plans/{plan_id}", response_model=ConcurrencyPlanOut)
async def get_concurrency_plan(
    plan_id: UUID,
    auth: AuthContext = Depends(get_auth),
    db: AsyncSession = Depends(get_db),
) -> ConcurrencyPlanOut:
    auth.require("read")
    try:
        row = await visible_by_id(
            db,
            ConcurrencyPlan,
            plan_id,
            namespace=auth.namespace,
            barrier_group=auth.barrier_group,
        )
    except ImprovementNotFound as exc:
        raise HTTPException(status_code=404, detail="Concurrency plan not found") from exc
    return concurrency_plan_out(row, row.calls)


__all__ = ["cache_router", "routing_router", "runtime_router"]
