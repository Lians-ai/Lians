"""Exact-token context and advisory tool optimization routes."""

from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from ..audit_chain import chain_log
from ..db import get_db
from ..improvement_service import ImprovementNotFound, visible_by_id
from ..mutation_safety import reject_non_replayable_idempotency_key
from ..optimization_models import ContextBundle, ToolRegistryVersion, ToolSelectionDecision
from ..optimization_schemas import (
    ContextCompileOut,
    ContextCompileRequest,
    ToolRegistryCreate,
    ToolRegistryOut,
    ToolSelectOut,
    ToolSelectRequest,
)
from ..optimization_service import (
    OptimizationContractError,
    compile_context,
    context_bundle_out,
    create_tool_registry,
    select_tools,
    tool_registry_out,
    tool_selection_out,
)
from .deps import AuthContext, get_auth

context_router = APIRouter(prefix="/v1/context", tags=["context-optimization"])
tools_router = APIRouter(prefix="/v1/tools", tags=["tool-optimization"])


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


@context_router.post(
    "/compile",
    response_model=ContextCompileOut,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(reject_non_replayable_idempotency_key)],
)
async def post_context_compile(
    body: ContextCompileRequest,
    auth: AuthContext = Depends(get_auth),
    db: AsyncSession = Depends(get_db),
) -> ContextCompileOut:
    auth.require("write")
    try:
        row = await compile_context(
            db,
            namespace=auth.namespace,
            barrier_group=auth.barrier_group,
            principal_ref=_principal(auth),
            body=body,
        )
        await _audit_commit(
            db,
            auth=auth,
            operation="context_bundle_compiled",
            resource_type="context_bundle",
            resource_id=row.id,
            content_hash=row.bundle_hash,
        )
    except OptimizationContractError as exc:
        await db.rollback()
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except IntegrityError as exc:
        await db.rollback()
        raise HTTPException(status_code=409, detail="Context bundle already exists") from exc
    return context_bundle_out(row)


@context_router.get("/bundles/{bundle_id}", response_model=ContextCompileOut)
async def get_context_bundle(
    bundle_id: UUID,
    auth: AuthContext = Depends(get_auth),
    db: AsyncSession = Depends(get_db),
) -> ContextCompileOut:
    auth.require("read")
    try:
        row = await visible_by_id(
            db,
            ContextBundle,
            bundle_id,
            namespace=auth.namespace,
            barrier_group=auth.barrier_group,
        )
    except ImprovementNotFound as exc:
        raise HTTPException(status_code=404, detail="Context bundle not found") from exc
    return context_bundle_out(row)


@tools_router.post(
    "/registries",
    response_model=ToolRegistryOut,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(reject_non_replayable_idempotency_key)],
)
async def post_tool_registry(
    body: ToolRegistryCreate,
    auth: AuthContext = Depends(get_auth),
    db: AsyncSession = Depends(get_db),
) -> ToolRegistryOut:
    auth.require("write")
    try:
        row = await create_tool_registry(
            db,
            namespace=auth.namespace,
            barrier_group=auth.barrier_group,
            principal_ref=_principal(auth),
            body=body,
        )
        await _audit_commit(
            db,
            auth=auth,
            operation="tool_registry_version_created",
            resource_type="tool_registry_version",
            resource_id=row.id,
            content_hash=row.registry_hash,
        )
    except IntegrityError as exc:
        await db.rollback()
        raise HTTPException(status_code=409, detail="Tool registry version already exists") from exc
    return tool_registry_out(row)


@tools_router.get("/registries/{registry_id}", response_model=ToolRegistryOut)
async def get_tool_registry(
    registry_id: UUID,
    auth: AuthContext = Depends(get_auth),
    db: AsyncSession = Depends(get_db),
) -> ToolRegistryOut:
    auth.require("read")
    try:
        row = await visible_by_id(
            db,
            ToolRegistryVersion,
            registry_id,
            namespace=auth.namespace,
            barrier_group=auth.barrier_group,
        )
    except ImprovementNotFound as exc:
        raise HTTPException(status_code=404, detail="Tool registry not found") from exc
    return tool_registry_out(row)


@tools_router.post(
    "/select",
    response_model=ToolSelectOut,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(reject_non_replayable_idempotency_key)],
)
async def post_tool_selection(
    body: ToolSelectRequest,
    auth: AuthContext = Depends(get_auth),
    db: AsyncSession = Depends(get_db),
) -> ToolSelectOut:
    auth.require("write")
    try:
        registry = await visible_by_id(
            db,
            ToolRegistryVersion,
            body.registry_version_id,
            namespace=auth.namespace,
            barrier_group=auth.barrier_group,
        )
        row = await select_tools(
            db,
            registry=registry,
            namespace=auth.namespace,
            barrier_group=auth.barrier_group,
            principal_ref=_principal(auth),
            body=body,
        )
        await _audit_commit(
            db,
            auth=auth,
            operation="tool_selection_created",
            resource_type="tool_selection_decision",
            resource_id=row.id,
            content_hash=row.selection_hash,
        )
    except ImprovementNotFound as exc:
        await db.rollback()
        raise HTTPException(status_code=404, detail="Tool registry not found") from exc
    except OptimizationContractError as exc:
        await db.rollback()
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except IntegrityError as exc:
        await db.rollback()
        raise HTTPException(status_code=409, detail="Tool selection already exists") from exc
    return tool_selection_out(row, registry_hash=registry.registry_hash)


@tools_router.get("/selections/{selection_id}", response_model=ToolSelectOut)
async def get_tool_selection(
    selection_id: UUID,
    auth: AuthContext = Depends(get_auth),
    db: AsyncSession = Depends(get_db),
) -> ToolSelectOut:
    auth.require("read")
    try:
        row = await visible_by_id(
            db,
            ToolSelectionDecision,
            selection_id,
            namespace=auth.namespace,
            barrier_group=auth.barrier_group,
        )
        registry = await visible_by_id(
            db,
            ToolRegistryVersion,
            row.registry_version_id,
            namespace=auth.namespace,
            barrier_group=auth.barrier_group,
        )
    except ImprovementNotFound as exc:
        raise HTTPException(status_code=404, detail="Tool selection not found") from exc
    return tool_selection_out(row, registry_hash=registry.registry_hash)


__all__ = ["context_router", "tools_router"]
