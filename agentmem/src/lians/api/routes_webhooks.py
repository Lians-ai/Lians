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

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..db import get_db
from ..api.deps import get_auth, AuthContext
from ..models import WebhookEndpoint, WebhookDelivery
from ..webhook_service import (
    register_webhook, list_webhooks, delete_webhook, update_webhook,
    ALL_EVENTS,
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
    enabled: Optional[bool] = None
    events: Optional[list[str]] = None
    description: Optional[str] = None


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

@router.post("/webhooks", response_model=WebhookRegisterResult, status_code=201)
async def create_webhook(
    req: WebhookRegisterRequest,
    auth: AuthContext = Depends(get_auth),
    db: AsyncSession = Depends(get_db),
):
    auth.require_all("write", "webhooks")
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
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    return WebhookRegisterResult(endpoint=_ep_to_out(ep), secret=secret)


@router.get("/webhooks", response_model=list[WebhookOut])
async def get_webhooks(
    auth: AuthContext = Depends(get_auth),
    db: AsyncSession = Depends(get_db),
):
    auth.require_all("read", "webhooks")
    endpoints = await list_webhooks(
        db, auth.namespace, barrier_override=auth.barrier_group
    )
    return [_ep_to_out(ep) for ep in endpoints]


@router.patch("/webhooks/{endpoint_id}", response_model=WebhookOut)
async def patch_webhook(
    endpoint_id: uuid.UUID,
    req: WebhookUpdateRequest,
    auth: AuthContext = Depends(get_auth),
    db: AsyncSession = Depends(get_db),
):
    auth.require_all("write", "webhooks")
    try:
        ep = await update_webhook(
            db, auth.namespace, endpoint_id,
            enabled=req.enabled,
            events=req.events,
            description=req.description,
            barrier_override=auth.barrier_group,
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    if ep is None:
        raise HTTPException(status_code=404, detail="Webhook not found")
    return _ep_to_out(ep)


@router.delete("/webhooks/{endpoint_id}", status_code=204)
async def remove_webhook(
    endpoint_id: uuid.UUID,
    auth: AuthContext = Depends(get_auth),
    db: AsyncSession = Depends(get_db),
):
    auth.require_all("write", "webhooks")
    deleted = await delete_webhook(
        db, auth.namespace, endpoint_id,
        barrier_override=auth.barrier_group,
    )
    if not deleted:
        raise HTTPException(status_code=404, detail="Webhook not found")


@router.get("/webhooks/{endpoint_id}/deliveries", response_model=DeliveryListResult)
async def webhook_deliveries(
    endpoint_id: uuid.UUID,
    limit: int = 50,
    auth: AuthContext = Depends(get_auth),
    db: AsyncSession = Depends(get_db),
):
    auth.require_all("read", "webhooks")
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

    result = await db.execute(
        select(WebhookDelivery)
        .where(WebhookDelivery.endpoint_id == endpoint_id)
        .order_by(WebhookDelivery.created_at.desc())
        .limit(limit)
    )
    rows = result.scalars().all()
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
        total=len(rows),
    )
