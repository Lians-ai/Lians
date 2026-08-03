"""
Legacy-compatible webhook delivery service for Lians.

Every call to `dispatch_event()` first commits its fan-out through the durable
Integration Outbox in the caller's transaction. The old in-process HMAC retry
path is disabled by default and forbidden in production; it remains only as an
explicit local-development compatibility aid.

Payload format (POST body):
    {
      "id":         "<delivery UUID>",
      "event":      "memory.superseded",
      "namespace":  "prod",
      "timestamp":  "2026-06-21T00:00:00Z",
      "data":       { ... event-specific fields ... }
    }

Signature header:
    X-Lians-Signature: sha256=<hex_hmac>
    X-AgentMem-Signature: sha256=<hex_hmac>  (deprecated compatibility alias)

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

from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from .models import WebhookDelivery, WebhookEndpoint
from .mutation_safety import MutationVersionConflict, assert_expected_updated_at
from .secret_storage import WEBHOOK_SIGNING_PURPOSE, seal_text, unseal_text

logger = logging.getLogger("lians.webhooks")

_MAX_ATTEMPTS = 3


class WebhookConflictError(RuntimeError):
    """A legacy webhook mutation lost its persisted concurrency precondition."""


class WebhookCapacityError(RuntimeError):
    """Legacy webhook rows exceed the configured bounded compatibility surface."""


_BACKOFF_BASE = 2.0   # seconds; attempt n waits BASE^(n-1) before retry
_TIMEOUT_S = 10.0
_BLOCKED_HOST_SUFFIXES = (".localhost", ".local", ".internal", ".home", ".lan")

# ── Supported event types ─────────────────────────────────────────────────────

MEMORY_SUPERSEDED   = "memory.superseded"
MEMORY_CONFLICT     = "memory.conflict"
MEMORY_ERASED       = "memory.erased"
SUPERSESSION_REJECTED = "supersession.rejected"
RELATIONSHIP_INVALIDATED = "relationship.invalidated"

ALL_EVENTS = {
    MEMORY_SUPERSEDED, MEMORY_CONFLICT, MEMORY_ERASED, SUPERSESSION_REJECTED,
    RELATIONSHIP_INVALIDATED,
}


# ── HMAC signing ──────────────────────────────────────────────────────────────

def _sign(secret: str, body: bytes) -> str:
    """Return 'sha256=<hex>' HMAC for body using UTF-8 encoded secret."""
    mac = hmac.new(secret.encode(), body, hashlib.sha256)
    return f"sha256={mac.hexdigest()}"


def _destination_log_ref(url: str) -> str:
    """Return a bounded opaque reference; never log tenant-owned webhook URLs."""
    return hashlib.sha256(url.encode("utf-8")).hexdigest()[:16]


def _namespace_log_ref(namespace: str) -> str:
    """Return a bounded opaque namespace reference for operational logs."""
    return hashlib.sha256(namespace.encode("utf-8")).hexdigest()[:16]


def _webhook_capacity_lock_id(namespace: str) -> int:
    digest = hashlib.sha256(
        b"lians/legacy-webhook-capacity/v1\0" + namespace.encode("utf-8")
    ).digest()
    return int.from_bytes(digest[:8], byteorder="big", signed=True)


async def _assert_webhook_capacity(db: AsyncSession, namespace: str) -> None:
    """Serialize registration and enforce the namespace-wide legacy ceiling."""
    from .config import get_settings

    if db.get_bind().dialect.name == "postgresql":
        await db.execute(
            text("SELECT pg_advisory_xact_lock(:lock_id)"),
            {"lock_id": _webhook_capacity_lock_id(namespace)},
        )
    limit = get_settings().legacy_webhook_max_endpoints_per_namespace
    count = int(
        (
            await db.execute(
                select(func.count())
                .select_from(WebhookEndpoint)
                .where(WebhookEndpoint.namespace == namespace)
            )
        ).scalar_one()
    )
    if count >= limit:
        raise WebhookCapacityError(
            f"Legacy webhook endpoints reached the per-namespace limit of {limit}"
        )


def _validate_webhook_url(url: str) -> tuple[str, int]:
    """Validate URL syntax and reject direct private-network destinations."""
    if len(url) > 2048:
        raise ValueError("Webhook URL is too long")
    parsed = urlsplit(url)
    if parsed.scheme.lower() != "https":
        raise ValueError("Webhook URL must use HTTPS")
    if parsed.username is not None or parsed.password is not None:
        raise ValueError("Webhook URL must not contain userinfo")
    if parsed.query:
        raise ValueError("Webhook URL must not contain query parameters")
    if parsed.fragment:
        raise ValueError("Webhook URL must not contain a fragment")
    if not parsed.hostname:
        raise ValueError("Webhook URL must include a hostname")
    try:
        port = parsed.port or 443
    except ValueError as exc:
        raise ValueError("Webhook URL contains an invalid port") from exc

    host = parsed.hostname.lower().rstrip(".")
    try:
        literal = ipaddress.ip_address(host)
    except ValueError:
        try:
            host = host.encode("idna").decode("ascii")
        except UnicodeError as exc:
            raise ValueError("Webhook URL contains an invalid hostname") from exc
        literal = None
    if host == "localhost" or host.endswith(_BLOCKED_HOST_SUFFIXES):
        raise ValueError("Webhook URL resolves to a blocked host")
    if literal is not None:
        if not literal.is_global:
            raise ValueError("Webhook URL must use a public IP address")
    return host, port


async def _resolve_webhook_destination(
    url: str,
) -> tuple[str, int, list[ipaddress.IPv4Address | ipaddress.IPv6Address]]:
    """Resolve public addresses used to pin the subsequent socket connection."""
    host, port = _validate_webhook_url(url)
    try:
        literal = ipaddress.ip_address(host)
        return host, port, [literal]
    except ValueError:
        pass

    try:
        addresses = await asyncio.wait_for(
            asyncio.to_thread(
                socket.getaddrinfo,
                host,
                port,
                type=socket.SOCK_STREAM,
            ),
            timeout=5.0,
        )
    except (OSError, TimeoutError) as exc:
        raise ValueError("Webhook hostname could not be resolved") from exc
    if not addresses:
        raise ValueError("Webhook hostname could not be resolved")
    resolved: set[ipaddress.IPv4Address | ipaddress.IPv6Address] = set()
    for info in addresses:
        address = ipaddress.ip_address(str(info[4][0]).split("%", 1)[0])
        if not address.is_global:
            raise ValueError("Webhook hostname resolves to a non-public address")
        resolved.add(address)
    if not resolved:
        raise ValueError("Webhook hostname could not be resolved")
    return host, port, sorted(
        resolved,
        key=lambda address: (address.version, int(address)),
    )


async def _validate_webhook_destination(url: str) -> None:
    """Compatibility wrapper for validation-only callers."""
    await _resolve_webhook_destination(url)


def _pinned_webhook_url(
    url: str,
    host: str,
    port: int,
    address: ipaddress.IPv4Address | ipaddress.IPv6Address,
) -> tuple[str, str]:
    parsed = urlsplit(url)
    ip_host = f"[{address.compressed}]" if address.version == 6 else address.compressed
    pinned_authority = ip_host if port == 443 else f"{ip_host}:{port}"
    host_authority = host if ":" not in host else f"[{host}]"
    if port != 443:
        host_authority = f"{host_authority}:{port}"
    return (
        urlunsplit(("https", pinned_authority, parsed.path or "/", "", "")),
        host_authority,
    )


# ── HTTP delivery (isolated so tests can mock it) ─────────────────────────────

async def _http_post(url: str, body: bytes, signature: str) -> tuple[int, str]:
    """POST body and return a bounded, non-sensitive delivery result code."""
    from .config import get_settings

    if get_settings().airgap_mode:
        return 0, "airgap_disabled"
    try:
        host, port, addresses = await _resolve_webhook_destination(url)
    except ValueError:
        return 0, "blocked_destination"
    pinned_url, host_authority = _pinned_webhook_url(
        url, host, port, addresses[0]
    )

    try:
        import httpx
    except ImportError:
        return 0, "httpx not installed — pip install httpx to enable webhook delivery"

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
                    "Host": host_authority,
                    "X-Lians-Signature": signature,
                    "X-AgentMem-Signature": signature,
                },
                extensions={"sni_hostname": host},
            )
            return (
                resp.status_code,
                "" if resp.is_success else f"http_status_{resp.status_code}",
            )
    except httpx.TimeoutException:
        return 0, "transport_timeout"
    except httpx.TransportError:
        return 0, "transport_error"
    except Exception:
        # Exception text can contain tenant URLs, credentials, response bodies,
        # resolver details, or proxy configuration. Persist only a safe code.
        return 0, "delivery_error"


# ── Core dispatch ─────────────────────────────────────────────────────────────

async def dispatch_event(
    db: AsyncSession,
    namespace: str,
    event_type: str,
    data: dict[str, Any],
    barrier_group: str | None = None,
) -> None:
    """
    Transactionally enqueue *event_type*, then optionally run the legacy fan-out.
    """
    from .config import get_settings

    settings = get_settings()
    if settings.airgap_mode:
        return
    from .integration_service import enqueue_integration_event

    aggregate_id = next(
        (
            str(data[key])
            for key in (
                "decision_id",
                "memory_id",
                "relationship_id",
                "subject_ref",
            )
            if data.get(key) is not None
        ),
        None,
    )
    await enqueue_integration_event(
        db,
        namespace=namespace,
        barrier_group=barrier_group,
        event_type=event_type,
        payload=data,
        aggregate_type=event_type.split(".", 1)[0],
        aggregate_id=aggregate_id,
        store_without_destinations=False,
    )
    if not settings.legacy_webhooks_enabled:
        return
    conditions = [
            WebhookEndpoint.namespace == namespace,
            WebhookEndpoint.enabled.is_(True),
    ]
    if barrier_group is not None:
        conditions.append(
            (WebhookEndpoint.barrier_group.is_(None))
            | (WebhookEndpoint.barrier_group == barrier_group)
        )
    endpoint_limit = settings.legacy_webhook_max_endpoints_per_namespace
    result = await db.execute(
        select(WebhookEndpoint).where(*conditions).limit(endpoint_limit + 1)
    )
    endpoint_rows = list(result.scalars().all())
    if len(endpoint_rows) > endpoint_limit:
        logger.error(
            "Legacy webhook fan-out refused: configured namespace ceiling exceeded "
            "(namespace_ref=%s, limit=%d)",
            _namespace_log_ref(namespace),
            endpoint_limit,
        )
        return
    endpoints = [ep for ep in endpoint_rows if event_type in (ep.events or [])]

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
                secret=unseal_text(
                    endpoint.secret,
                    purpose=WEBHOOK_SIGNING_PURPOSE,
                    context=endpoint.namespace,
                ),
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

        from sqlalchemy import update as sa_update

        from .db import AsyncSessionLocal
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
            logger.debug(
                "Webhook %s delivered (destination_ref=%s, attempt=%d)",
                event_type,
                _destination_log_ref(url),
                attempt,
            )
            return

        logger.warning(
            "Webhook delivery failed (attempt %d/%d, destination_ref=%s): %s %s",
            attempt,
            _MAX_ATTEMPTS,
            _destination_log_ref(url),
            status_code,
            error,
        )

    logger.error(
        "Webhook %s failed after %d attempts (destination_ref=%s)",
        event_type,
        _MAX_ATTEMPTS,
        _destination_log_ref(url),
    )


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
    from .config import get_settings

    settings = get_settings()
    if settings.airgap_mode:
        raise ValueError("Legacy webhooks cannot be registered while AIRGAP_MODE is enabled")
    if not settings.legacy_webhooks_enabled:
        raise ValueError(
            "Legacy webhooks are retired; configure a durable /v1/integrations destination"
        )
    await _assert_webhook_capacity(db, namespace)
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
    from .config import get_settings

    conditions = [WebhookEndpoint.namespace == namespace]
    if barrier_override is not None:
        conditions.append(
            (WebhookEndpoint.barrier_group.is_(None))
            | (WebhookEndpoint.barrier_group == barrier_override)
        )
    limit = get_settings().legacy_webhook_max_endpoints_per_namespace
    result = await db.execute(
        select(WebhookEndpoint)
        .where(*conditions)
        .order_by(WebhookEndpoint.created_at)
        .limit(limit + 1)
    )
    rows = list(result.scalars().all())
    if len(rows) > limit:
        raise WebhookCapacityError(
            f"Legacy webhook rows exceed the configured per-namespace limit of {limit}"
        )
    return rows


async def delete_webhook(
    db: AsyncSession,
    namespace: str,
    endpoint_id: uuid.UUID,
    expected_updated_at: datetime,
    barrier_override: str | None = None,
) -> bool:
    conditions = [
        WebhookEndpoint.id == endpoint_id,
        WebhookEndpoint.namespace == namespace,
    ]
    if barrier_override is not None:
        conditions.append(
            (WebhookEndpoint.barrier_group.is_(None))
            | (WebhookEndpoint.barrier_group == barrier_override)
        )
    ep = (
        await db.execute(select(WebhookEndpoint).where(*conditions).with_for_update())
    ).scalar_one_or_none()
    if (
        ep is None
    ):
        return False
    try:
        assert_expected_updated_at(ep.updated_at, expected_updated_at)
    except MutationVersionConflict as exc:
        raise WebhookConflictError(str(exc)) from exc
    await db.delete(ep)
    await db.commit()
    return True


async def update_webhook(
    db: AsyncSession,
    namespace: str,
    endpoint_id: uuid.UUID,
    *,
    expected_updated_at: datetime,
    enabled: bool | None = None,
    events: list[str] | None = None,
    description: str | None = None,
    barrier_override: str | None = None,
) -> WebhookEndpoint | None:
    from .config import get_settings

    if enabled is True:
        if get_settings().airgap_mode:
            raise ValueError("Legacy webhooks cannot be enabled while AIRGAP_MODE is enabled")
        if not get_settings().legacy_webhooks_enabled:
            raise ValueError(
                "Legacy webhooks are retired; configure a durable /v1/integrations destination"
            )
    conditions = [
        WebhookEndpoint.id == endpoint_id,
        WebhookEndpoint.namespace == namespace,
    ]
    if barrier_override is not None:
        conditions.append(
            (WebhookEndpoint.barrier_group.is_(None))
            | (WebhookEndpoint.barrier_group == barrier_override)
        )
    ep = (
        await db.execute(select(WebhookEndpoint).where(*conditions).with_for_update())
    ).scalar_one_or_none()
    if (
        ep is None
    ):
        return None
    try:
        assert_expected_updated_at(ep.updated_at, expected_updated_at)
    except MutationVersionConflict as exc:
        raise WebhookConflictError(str(exc)) from exc
    if enabled is not None:
        ep.enabled = enabled
    if events is not None:
        ep.events = _validate_events(events)
    if description is not None:
        ep.description = description
    await db.commit()
    await db.refresh(ep)
    return ep
