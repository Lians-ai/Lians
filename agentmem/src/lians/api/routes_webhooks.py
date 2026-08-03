"""
Webhook management routes.

POST   /v1/webhooks                  Register a new endpoint
GET    /v1/webhooks                  List endpoints for the caller's namespace
PATCH  /v1/webhooks/{id}             Update enabled/events/description
DELETE /v1/webhooks/{id}             Remove endpoint
GET    /v1/webhooks/{id}/deliveries  Delivery history for an endpoint
"""
from __future__ import annotations

import secrets
import uuid
from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from pydantic import BaseModel, Field
from sqlalchemy import and_, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..api.deps import AuthContext, get_auth
from ..db import get_db
from ..models import WebhookDelivery, WebhookEndpoint
from ..mutation_safety import reject_non_replayable_idempotency_key
from ..webhook_service import (
    ALL_EVENTS,
    WebhookCapacityError,
    WebhookConflictError,
    delete_webhook,
    list_webhooks,
    register_webhook,
    update_webhook,
)

router = APIRouter(prefix="/v1", tags=["webhooks"])


# ── Pydantic schemas ──────────────────────────────────────────────────────────

class WebhookRegisterRequest(BaseModel):
    url: str = Field(..., max_length=2048, description="HTTPS endpoint that will receive events")
    events: list[str] = Field(..., min_length=1, max_length=len(ALL_EVENTS), description=f"Event types to subscribe to. Valid: {sorted(ALL_EVENTS)}")
    secret: Optional[str] = Field(None, min_length=16, max_length=1024, description="HMAC secret. If omitted, a 32-byte random secret is generated.")
    description: Optional[str] = Field(None, max_length=500)


class WebhookOut(BaseModel):
    id: uuid.UUID
    namespace: str
    url: str
    events: list[str]
    enabled: bool
    description: Optional[str]
    created_at: datetime
    updated_at: datetime


class WebhookUpdateRequest(BaseModel):
    expected_updated_at: datetime
    enabled: Optional[bool] = None
    events: Optional[list[str]] = Field(default=None, max_length=len(ALL_EVENTS))
    description: Optional[str] = Field(default=None, max_length=500)


class WebhookRegisterResult(BaseModel):
    endpoint: WebhookOut
    secret: str   # returned once at registration; encrypted at rest


class DeliveryOut(BaseModel):
    id: uuid.UUID
    event_type: str
    attempt: int
    status_code: Optional[int]
    error: Optional[str]
    delivered_at: Optional[datetime]
    created_at: datetime


class DeliveryListResult(BaseModel):
    deliveries: list[DeliveryOut]
    total: int
    returned: int
    complete: bool
    has_more: bool
    next_after_created_at: Optional[datetime] = None
    next_after_id: Optional[uuid.UUID] = None


def _ep_to_out(ep: WebhookEndpoint) -> WebhookOut:
    return WebhookOut(
        id=ep.id,
        namespace=ep.namespace,
        url=ep.url,
        events=ep.events or [],
        enabled=ep.enabled,
        description=ep.description,
        created_at=ep.created_at,
        updated_at=ep.updated_at,
    )


# ── Routes ────────────────────────────────────────────────────────────────────

@router.post(
    "/webhooks",
    response_model=WebhookRegisterResult,
    status_code=201,
    dependencies=[Depends(reject_non_replayable_idempotency_key)],
)
async def create_webhook(
    req: WebhookRegisterRequest,
    response: Response,
    auth: AuthContext = Depends(get_auth),
    db: AsyncSession = Depends(get_db),
):
    response.headers["Cache-Control"] = "no-store"
    response.headers["Pragma"] = "no-cache"
    auth.require("admin")
    secret = req.secret or secrets.token_hex(32)
    try:
        ep = await register_webhook(
            db,
            namespace=auth.namespace,
            url=req.url,
            secret=secret,
            events=req.events,
            description=req.description,
            barrier_group=auth.barrier_group,
        )
    except WebhookCapacityError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    return WebhookRegisterResult(endpoint=_ep_to_out(ep), secret=secret)


@router.get("/webhooks", response_model=list[WebhookOut])
async def get_webhooks(
    auth: AuthContext = Depends(get_auth),
    db: AsyncSession = Depends(get_db),
):
    auth.require("admin")
    try:
        endpoints = await list_webhooks(
            db, auth.namespace, barrier_override=auth.barrier_group
        )
    except WebhookCapacityError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return [_ep_to_out(ep) for ep in endpoints]


@router.patch("/webhooks/{endpoint_id}", response_model=WebhookOut)
async def patch_webhook(
    endpoint_id: uuid.UUID,
    req: WebhookUpdateRequest,
    auth: AuthContext = Depends(get_auth),
    db: AsyncSession = Depends(get_db),
):
    auth.require("admin")
    try:
        ep = await update_webhook(
            db, auth.namespace, endpoint_id,
            expected_updated_at=req.expected_updated_at,
            enabled=req.enabled,
            events=req.events,
            description=req.description,
            barrier_override=auth.barrier_group,
        )
    except WebhookConflictError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    if ep is None:
        raise HTTPException(status_code=404, detail="Webhook not found")
    return _ep_to_out(ep)


@router.delete(
    "/webhooks/{endpoint_id}",
    status_code=204,
    dependencies=[Depends(reject_non_replayable_idempotency_key)],
)
async def remove_webhook(
    endpoint_id: uuid.UUID,
    expected_updated_at: datetime = Query(...),
    auth: AuthContext = Depends(get_auth),
    db: AsyncSession = Depends(get_db),
):
    auth.require("admin")
    try:
        deleted = await delete_webhook(
            db,
            auth.namespace,
            endpoint_id,
            expected_updated_at=expected_updated_at,
            barrier_override=auth.barrier_group,
        )
    except WebhookConflictError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    if not deleted:
        raise HTTPException(status_code=404, detail="Webhook not found")


@router.get("/webhooks/{endpoint_id}/deliveries", response_model=DeliveryListResult)
async def webhook_deliveries(
    endpoint_id: uuid.UUID,
    limit: int = Query(50, ge=1, le=500),
    after_created_at: Optional[datetime] = Query(default=None),
    after_id: Optional[uuid.UUID] = Query(default=None),
    auth: AuthContext = Depends(get_auth),
    db: AsyncSession = Depends(get_db),
):
    auth.require("admin")
    if (after_created_at is None) != (after_id is None):
        raise HTTPException(
            status_code=422,
            detail="after_created_at and after_id must be supplied together",
        )
    ep = await db.get(WebhookEndpoint, endpoint_id)
    if (
        ep is None
        or ep.namespace != auth.namespace
        or (
            auth.barrier_group is not None
            and ep.barrier_group not in (None, auth.barrier_group)
        )
    ):
        raise HTTPException(status_code=404, detail="Webhook not found")

    total = int(
        (
            await db.execute(
                select(func.count())
                .select_from(WebhookDelivery)
                .where(WebhookDelivery.endpoint_id == endpoint_id)
            )
        ).scalar_one()
    )
    filters = [WebhookDelivery.endpoint_id == endpoint_id]
    if after_created_at is not None and after_id is not None:
        filters.append(
            or_(
                WebhookDelivery.created_at < after_created_at,
                and_(
                    WebhookDelivery.created_at == after_created_at,
                    WebhookDelivery.id < after_id,
                ),
            )
        )
    result = await db.execute(
        select(WebhookDelivery)
        .where(*filters)
        .order_by(WebhookDelivery.created_at.desc(), WebhookDelivery.id.desc())
        .limit(limit + 1)
    )
    fetched = list(result.scalars().all())
    has_more = len(fetched) > limit
    rows = fetched[:limit]
    next_row = rows[-1] if has_more and rows else None
    return DeliveryListResult(
        deliveries=[
            DeliveryOut(
                id=r.id,
                event_type=r.event_type,
                attempt=r.attempt,
                status_code=r.status_code,
                error=r.error,
                delivered_at=r.delivered_at,
                created_at=r.created_at,
            )
            for r in rows
        ],
        total=total,
        returned=len(rows),
        complete=after_created_at is None and not has_more and len(rows) == total,
        has_more=has_more,
        next_after_created_at=next_row.created_at if next_row is not None else None,
        next_after_id=next_row.id if next_row is not None else None,
    )
