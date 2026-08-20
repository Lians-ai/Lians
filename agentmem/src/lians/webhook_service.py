"""
Webhook delivery service for Lians.

Every call to `dispatch_event()` creates the delivery record and a durable job
in the caller's transaction. Workers claim those jobs with database leases, so
a process restart cannot silently lose an outbound event.

Payload format (POST body):
    {
      "id":         "<delivery UUID>",
      "event":      "memory.superseded",
      "namespace":  "prod",
      "timestamp":  "2026-06-21T00:00:00Z",
      "data":       { ... event-specific fields ... }
    }

Signature headers:
    X-Lians-Signature: sha256=<hex_hmac>
    X-AgentMem-Signature: sha256=<hex_hmac>  (legacy compatibility)

Receivers verify: hmac.compare_digest(sha256(secret, body), header_value)
"""
from __future__ import annotations

import asyncio
import hashlib
import hmac
import ipaddress
import json
import logging
import socket
import uuid
from datetime import datetime, timezone
from typing import Any
from urllib.parse import urlsplit, urlunsplit

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from .durable_jobs import enqueue_job
from .models import DurableJob, WebhookEndpoint, WebhookDelivery
from .secret_storage import WEBHOOK_SIGNING_PURPOSE, seal_text, unseal_text

logger = logging.getLogger("lians.webhooks")

_MAX_ATTEMPTS = 3
_BACKOFF_BASE = 2.0   # seconds; attempt n waits BASE^(n-1) before retry
_TIMEOUT_S = 10.0
_BLOCKED_HOST_SUFFIXES = (".localhost", ".local", ".internal", ".home", ".lan")

# ── Supported event types ─────────────────────────────────────────────────────

MEMORY_SUPERSEDED   = "memory.superseded"
MEMORY_CONFLICT     = "memory.conflict"
MEMORY_ERASED       = "memory.erased"
SUPERSESSION_REJECTED = "supersession.rejected"
RELATIONSHIP_INVALIDATED = "relationship.invalidated"
EVIDENCE_BLAST_RADIUS = "evidence.blast_radius"

ALL_EVENTS = {
    MEMORY_SUPERSEDED, MEMORY_CONFLICT, MEMORY_ERASED, SUPERSESSION_REJECTED,
    RELATIONSHIP_INVALIDATED, EVIDENCE_BLAST_RADIUS,
}


# ── HMAC signing ──────────────────────────────────────────────────────────────

def _sign(secret: str, body: bytes) -> str:
    """Return 'sha256=<hex>' HMAC for body using UTF-8 encoded secret."""
    mac = hmac.new(secret.encode(), body, hashlib.sha256)
    return f"sha256={mac.hexdigest()}"


def _validate_webhook_url(url: str) -> tuple[str, int]:
    """Validate URL syntax and reject direct private-network destinations."""
    if len(url) > 2048:
        raise ValueError("Webhook URL is too long")
    parsed = urlsplit(url)
    if parsed.scheme.lower() != "https":
        raise ValueError("Webhook URL must use HTTPS")
    if parsed.username is not None or parsed.password is not None:
        raise ValueError("Webhook URL must not contain userinfo")
    if parsed.fragment:
        raise ValueError("Webhook URL must not contain a fragment")
    if not parsed.hostname:
        raise ValueError("Webhook URL must include a hostname")
    try:
        port = parsed.port or 443
    except ValueError as exc:
        raise ValueError("Webhook URL contains an invalid port") from exc

    host = parsed.hostname.lower().rstrip(".")
    if host == "localhost" or host.endswith(_BLOCKED_HOST_SUFFIXES):
        raise ValueError("Webhook URL resolves to a blocked host")
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        pass
    else:
        if not address.is_global:
            raise ValueError("Webhook URL must use a public IP address")
    return host, port


async def _resolve_webhook_destination(url: str) -> tuple[str, str, str]:
    """Resolve once, require public IPs, and return an IP-pinned HTTPS target.

    Validation followed by a normal hostname request creates a DNS-rebinding
    window because the HTTP client resolves the name a second time. Connecting
    to the validated address while retaining the original Host header and TLS
    SNI closes that gap.
    """
    host, port = _validate_webhook_url(url)
    try:
        literal = ipaddress.ip_address(host)
    except ValueError:
        try:
            addresses = await asyncio.to_thread(
                socket.getaddrinfo,
                host,
                port,
                type=socket.SOCK_STREAM,
            )
        except OSError as exc:
            raise ValueError("Webhook hostname could not be resolved") from exc
        if not addresses:
            raise ValueError("Webhook hostname could not be resolved")
        resolved = [ipaddress.ip_address(info[4][0]) for info in addresses]
        if any(not address.is_global for address in resolved):
            raise ValueError("Webhook hostname resolves to a non-public address")
        address = resolved[0]
    else:
        address = literal

    parsed = urlsplit(url)
    address_text = f"[{address}]" if address.version == 6 else str(address)
    pinned_netloc = address_text if port == 443 else f"{address_text}:{port}"
    pinned_url = urlunsplit(
        ("https", pinned_netloc, parsed.path or "/", parsed.query, "")
    )
    host_text = f"[{host}]" if ":" in host else host
    host_header = host_text if port == 443 else f"{host_text}:{port}"
    return pinned_url, host_header, host


async def _validate_webhook_destination(url: str) -> None:
    """Compatibility validation helper used by callers and older integrations."""
    await _resolve_webhook_destination(url)


# ── HTTP delivery (isolated so tests can mock it) ─────────────────────────────

async def _http_post(url: str, body: bytes, signature: str) -> tuple[int, str]:
    """POST body to url with signature header.  Returns (status_code, error_or_empty)."""
    try:
        pinned_url, host_header, sni_hostname = await _resolve_webhook_destination(url)
    except ValueError as exc:
        return 0, f"Blocked webhook destination: {exc}"

    try:
        import httpx
    except ImportError:
        return 0, "httpx not installed - pip install httpx to enable webhook delivery"

    try:
        async with httpx.AsyncClient(
            timeout=_TIMEOUT_S,
            follow_redirects=False,
            trust_env=False,
        ) as client:
            resp = await client.post(
                pinned_url,
                content=body,
                headers={
                    "Content-Type": "application/json",
                    "Host": host_header,
                    "X-Lians-Signature": signature,
                    "X-AgentMem-Signature": signature,
                },
                extensions={"sni_hostname": sni_hostname},
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
    barrier_group: str | None = None,
) -> None:
    """
    Fan out *event_type* to all enabled endpoints subscribed to it in *namespace*.

    This coroutine performs no network I/O. Each delivery and its work item are
    committed atomically with the memory write that produced the event.
    """
    conditions = [
            WebhookEndpoint.namespace == namespace,
            WebhookEndpoint.enabled.is_(True),
    ]
    if barrier_group is not None:
        conditions.append(
            (WebhookEndpoint.barrier_group.is_(None))
            | (WebhookEndpoint.barrier_group == barrier_group)
        )
    result = await db.execute(select(WebhookEndpoint).where(*conditions))
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
    for endpoint in endpoints:
        delivery = WebhookDelivery(
            id=uuid.uuid4(),
            endpoint_id=endpoint.id,
            event_type=event_type,
            payload=payload,
        )
        db.add(delivery)
        await db.flush()
        await enqueue_job(
            db,
            namespace=namespace,
            kind="webhook.delivery",
            payload={
                "endpoint_id": str(endpoint.id),
                "delivery_id": str(delivery.id),
            },
            dedupe_key=str(delivery.id),
            max_attempts=_MAX_ATTEMPTS,
        )

    await db.flush()


class WebhookDeliveryError(RuntimeError):
    """A retryable outbound delivery failure."""


async def handle_webhook_job(db: AsyncSession, job: DurableJob) -> None:
    """Deliver one leased webhook job without placing secrets in the job row."""
    try:
        endpoint_id = uuid.UUID(str(job.payload["endpoint_id"]))
        delivery_id = uuid.UUID(str(job.payload["delivery_id"]))
    except (KeyError, TypeError, ValueError) as exc:
        raise ValueError("Malformed webhook delivery job") from exc

    endpoint = await db.get(WebhookEndpoint, endpoint_id)
    delivery = await db.get(WebhookDelivery, delivery_id)
    if delivery is None:
        return
    if endpoint is None or not endpoint.enabled:
        delivery.attempt = job.attempts
        delivery.error = "Webhook endpoint is missing or disabled"
        await db.commit()
        return

    body = json.dumps(delivery.payload, default=str).encode()
    secret = unseal_text(
        endpoint.secret,
        purpose=WEBHOOK_SIGNING_PURPOSE,
        context=endpoint.namespace,
    )
    status_code, error = await _http_post(endpoint.url, body, _sign(secret, body))
    delivered = bool(status_code and 200 <= status_code < 300)
    delivery.attempt = job.attempts
    delivery.status_code = status_code or None
    delivery.error = error or None
    delivery.delivered_at = datetime.now(tz=timezone.utc) if delivered else None
    await db.commit()

    if delivered:
        logger.debug(
            "Webhook %s delivered to %s (attempt %d)",
            delivery.event_type,
            endpoint.url,
            job.attempts,
        )
        return
    raise WebhookDeliveryError(
        f"Webhook {delivery.event_type} returned {status_code or 'no response'}"
    )


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
    barrier_group: str | None = None,
) -> WebhookEndpoint:
    events = _validate_events(events)
    _validate_webhook_url(url)
    endpoint = WebhookEndpoint(
        namespace=namespace,
        barrier_group=barrier_group,
        url=url,
        secret=seal_text(
            secret,
            purpose=WEBHOOK_SIGNING_PURPOSE,
            context=namespace,
        ),
        events=events,
        description=description,
    )
    db.add(endpoint)
    await db.commit()
    await db.refresh(endpoint)
    return endpoint


async def list_webhooks(
    db: AsyncSession,
    namespace: str,
    barrier_override: str | None = None,
) -> list[WebhookEndpoint]:
    conditions = [WebhookEndpoint.namespace == namespace]
    if barrier_override is not None:
        conditions.append(
            (WebhookEndpoint.barrier_group.is_(None))
            | (WebhookEndpoint.barrier_group == barrier_override)
        )
    result = await db.execute(
        select(WebhookEndpoint)
        .where(*conditions)
        .order_by(WebhookEndpoint.created_at)
    )
    return list(result.scalars().all())


async def delete_webhook(
    db: AsyncSession,
    namespace: str,
    endpoint_id: uuid.UUID,
    barrier_override: str | None = None,
) -> bool:
    ep = await db.get(WebhookEndpoint, endpoint_id)
    if (
        ep is None
        or ep.namespace != namespace
        or (
            barrier_override is not None
            and ep.barrier_group not in (None, barrier_override)
        )
    ):
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
    barrier_override: str | None = None,
) -> WebhookEndpoint | None:
    ep = await db.get(WebhookEndpoint, endpoint_id)
    if (
        ep is None
        or ep.namespace != namespace
        or (
            barrier_override is not None
            and ep.barrier_group not in (None, barrier_override)
        )
    ):
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
