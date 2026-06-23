"""
Webhook delivery service for AgentMem.

Every call to `dispatch_event()` fans out an HMAC-SHA256-signed JSON payload to
all enabled endpoints registered for that namespace and event type.  Delivery is
fire-and-forget from the write path's perspective: failures are logged and
retried up to MAX_ATTEMPTS times with exponential back-off in a background task.

Payload format (POST body):
    {
      "id":         "<delivery UUID>",
      "event":      "memory.superseded",
      "namespace":  "prod",
      "timestamp":  "2026-06-21T00:00:00Z",
      "data":       { ... event-specific fields ... }
    }

Signature header:
    X-AgentMem-Signature: sha256=<hex_hmac>

Receivers verify: hmac.compare_digest(sha256(secret, body), header_value)
"""
from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import logging
import time
import uuid
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from .models import WebhookEndpoint, WebhookDelivery

logger = logging.getLogger("agentmem.webhooks")

_MAX_ATTEMPTS = 3
_BACKOFF_BASE = 2.0   # seconds; attempt n waits BASE^(n-1) before retry
_TIMEOUT_S = 10.0

# ── Supported event types ─────────────────────────────────────────────────────

MEMORY_SUPERSEDED   = "memory.superseded"
MEMORY_CONFLICT     = "memory.conflict"
MEMORY_ERASED       = "memory.erased"
SUPERSESSION_REJECTED = "supersession.rejected"

ALL_EVENTS = {MEMORY_SUPERSEDED, MEMORY_CONFLICT, MEMORY_ERASED, SUPERSESSION_REJECTED}


# ── HMAC signing ──────────────────────────────────────────────────────────────

def _sign(secret: str, body: bytes) -> str:
    """Return 'sha256=<hex>' HMAC for body using UTF-8 encoded secret."""
    mac = hmac.new(secret.encode(), body, hashlib.sha256)
    return f"sha256={mac.hexdigest()}"


# ── HTTP delivery (isolated so tests can mock it) ─────────────────────────────

async def _http_post(url: str, body: bytes, signature: str) -> tuple[int, str]:
    """POST body to url with signature header.  Returns (status_code, error_or_empty)."""
    try:
        import httpx
    except ImportError:
        return 0, "httpx not installed — pip install httpx to enable webhook delivery"

    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT_S) as client:
            resp = await client.post(
                url,
                content=body,
                headers={
                    "Content-Type": "application/json",
                    "X-AgentMem-Signature": signature,
                },
            )
            return resp.status_code, "" if resp.is_success else resp.text[:500]
    except Exception as exc:
        return 0, str(exc)[:500]


# ── Core dispatch ─────────────────────────────────────────────────────────────

async def dispatch_event(
    db: AsyncSession,
    namespace: str,
    event_type: str,
    data: dict[str, Any],
) -> None:
    """
    Fan out *event_type* to all enabled endpoints subscribed to it in *namespace*.

    This coroutine itself is fast (one DB read + task spawning).  Each delivery
    runs in a separate asyncio task so the write path is never blocked.
    """
    result = await db.execute(
        select(WebhookEndpoint).where(
            WebhookEndpoint.namespace == namespace,
            WebhookEndpoint.enabled.is_(True),
        )
    )
    endpoints = [ep for ep in result.scalars().all() if event_type in (ep.events or [])]

    if not endpoints:
        return

    now = datetime.now(tz=timezone.utc)
    delivery_id = str(uuid.uuid4())
    payload = {
        "id": delivery_id,
        "event": event_type,
        "namespace": namespace,
        "timestamp": now.isoformat(),
        "data": data,
    }
    body = json.dumps(payload, default=str).encode()

    for endpoint in endpoints:
        delivery = WebhookDelivery(
            id=uuid.uuid4(),
            endpoint_id=endpoint.id,
            event_type=event_type,
            payload=payload,
        )
        db.add(delivery)
        asyncio.create_task(
            _deliver_with_retry(
                endpoint_id=endpoint.id,
                url=endpoint.url,
                secret=endpoint.secret,
                delivery_id=delivery.id,
                body=body,
                event_type=event_type,
            )
        )

    await db.flush()


async def _deliver_with_retry(
    endpoint_id: uuid.UUID,
    url: str,
    secret: str,
    delivery_id: uuid.UUID,
    body: bytes,
    event_type: str,
) -> None:
    """Attempt delivery up to MAX_ATTEMPTS times with exponential back-off."""
    signature = _sign(secret, body)

    for attempt in range(1, _MAX_ATTEMPTS + 1):
        if attempt > 1:
            await asyncio.sleep(_BACKOFF_BASE ** (attempt - 1))

        status_code, error = await _http_post(url, body, signature)
        delivered = status_code and 200 <= status_code < 300

        from .db import AsyncSessionLocal
        from sqlalchemy import update as sa_update
        async with AsyncSessionLocal() as session:
            await session.execute(
                sa_update(WebhookDelivery)
                .where(WebhookDelivery.id == delivery_id)
                .values(
                    attempt=attempt,
                    status_code=status_code or None,
                    error=error or None,
                    delivered_at=datetime.now(tz=timezone.utc) if delivered else None,
                )
            )
            await session.commit()

        if delivered:
            logger.debug("Webhook %s delivered to %s (attempt %d)", event_type, url, attempt)
            return

        logger.warning(
            "Webhook delivery failed (attempt %d/%d): %s → %s %s",
            attempt, _MAX_ATTEMPTS, url, status_code, error,
        )

    logger.error("Webhook %s to %s failed after %d attempts", event_type, url, _MAX_ATTEMPTS)


# ── Registration helpers (called by API routes) ───────────────────────────────

def _validate_events(events: list[str]) -> list[str]:
    unknown = set(events) - ALL_EVENTS
    if unknown:
        raise ValueError(f"Unknown event types: {', '.join(sorted(unknown))}. Valid: {', '.join(sorted(ALL_EVENTS))}")
    return list(set(events))


async def register_webhook(
    db: AsyncSession,
    namespace: str,
    url: str,
    secret: str,
    events: list[str],
    description: str | None = None,
) -> WebhookEndpoint:
    events = _validate_events(events)
    endpoint = WebhookEndpoint(
        namespace=namespace,
        url=url,
        secret=secret,
        events=events,
        description=description,
    )
    db.add(endpoint)
    await db.commit()
    await db.refresh(endpoint)
    return endpoint


async def list_webhooks(db: AsyncSession, namespace: str) -> list[WebhookEndpoint]:
    result = await db.execute(
        select(WebhookEndpoint)
        .where(WebhookEndpoint.namespace == namespace)
        .order_by(WebhookEndpoint.created_at)
    )
    return list(result.scalars().all())


async def delete_webhook(db: AsyncSession, namespace: str, endpoint_id: uuid.UUID) -> bool:
    ep = await db.get(WebhookEndpoint, endpoint_id)
    if ep is None or ep.namespace != namespace:
        return False
    await db.delete(ep)
    await db.commit()
    return True


async def update_webhook(
    db: AsyncSession,
    namespace: str,
    endpoint_id: uuid.UUID,
    *,
    enabled: bool | None = None,
    events: list[str] | None = None,
    description: str | None = None,
) -> WebhookEndpoint | None:
    ep = await db.get(WebhookEndpoint, endpoint_id)
    if ep is None or ep.namespace != namespace:
        return None
    if enabled is not None:
        ep.enabled = enabled
    if events is not None:
        ep.events = _validate_events(events)
    if description is not None:
        ep.description = description
    await db.commit()
    await db.refresh(ep)
    return ep
