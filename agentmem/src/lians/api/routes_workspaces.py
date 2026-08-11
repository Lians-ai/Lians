"""Workspace and governed push-connector product surfaces."""
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from ..db import get_db
from ..schemas import (
    ConnectorCatalog,
    ConnectorCatalogItem,
    ConnectorCreate,
    ConnectorIngestRequest,
    ConnectorIngestResult,
    ConnectorOut,
    ConnectorUpdate,
    WorkspaceOut,
    WorkspaceUpdate,
)
from ..workspace_service import (
    CONNECTOR_CATALOG,
    create_connector,
    get_workspace,
    ingest_connector_events,
    list_connectors,
    update_connector,
    update_workspace,
)
from .deps import AuthContext, get_auth

router = APIRouter(prefix="/v1", tags=["workspaces"])


@router.get("/workspace", response_model=WorkspaceOut)
async def workspace(
    auth: AuthContext = Depends(get_auth),
    db: AsyncSession = Depends(get_db),
):
    auth.require("read")
    return await get_workspace(db, auth.namespace)


@router.put("/workspace", response_model=WorkspaceOut)
async def put_workspace(
    req: WorkspaceUpdate,
    auth: AuthContext = Depends(get_auth),
    db: AsyncSession = Depends(get_db),
):
    auth.require("admin")
    return await update_workspace(db, auth.namespace, req)


@router.get("/connector-catalog", response_model=ConnectorCatalog)
async def connector_catalog(auth: AuthContext = Depends(get_auth)):
    auth.require("read")
    items = [ConnectorCatalogItem(**item) for item in CONNECTOR_CATALOG]
    return ConnectorCatalog(connectors=items, total=len(items))


@router.get("/connectors", response_model=list[ConnectorOut])
async def connectors(
    auth: AuthContext = Depends(get_auth),
    db: AsyncSession = Depends(get_db),
):
    auth.require("read")
    return await list_connectors(db, auth.namespace)


@router.post("/connectors", response_model=ConnectorOut, status_code=201)
async def post_connector(
    req: ConnectorCreate,
    auth: AuthContext = Depends(get_auth),
    db: AsyncSession = Depends(get_db),
):
    auth.require("admin")
    try:
        return await create_connector(db, auth.namespace, req)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from None
    except IntegrityError:
        await db.rollback()
        raise HTTPException(status_code=409, detail="Connector name already exists") from None


@router.patch("/connectors/{connector_id}", response_model=ConnectorOut)
async def patch_connector(
    connector_id: UUID,
    req: ConnectorUpdate,
    auth: AuthContext = Depends(get_auth),
    db: AsyncSession = Depends(get_db),
):
    auth.require("admin")
    try:
        result = await update_connector(db, auth.namespace, connector_id, req)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from None
    if result is None:
        raise HTTPException(status_code=404, detail="Connector not found")
    return result


@router.post("/connectors/{connector_id}/events", response_model=ConnectorIngestResult)
async def connector_events(
    connector_id: UUID,
    req: ConnectorIngestRequest,
    auth: AuthContext = Depends(get_auth),
    db: AsyncSession = Depends(get_db),
):
    auth.require("write")
    try:
        result = await ingest_connector_events(
            db,
            auth.namespace,
            connector_id,
            req,
            barrier_override=auth.barrier_group,
        )
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from None
    if result is None:
        raise HTTPException(status_code=404, detail="Connector not found")
    return result
