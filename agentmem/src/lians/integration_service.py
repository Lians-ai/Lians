"""Transactional outbox, leased delivery worker, and integration administration."""

from __future__ import annotations

import asyncio
import base64
import hashlib
import hmac
import ipaddress
import json
import logging
import os
import secrets
import socket
import time
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from email.utils import parsedate_to_datetime
from typing import Any
from urllib.parse import urlsplit, urlunsplit
from uuid import UUID

import httpx
from sqlalchemy import and_, func, or_, select, text, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from sqlalchemy.orm import defer

from .config import get_settings
from .db import set_current_barrier_group, set_current_namespace
from .integration_models import (
    IntegrationDelivery,
    IntegrationDeliveryAttempt,
    IntegrationDestination,
    IntegrationOutboxEvent,
)
from .integration_schemas import (
    DestinationCreate,
    DestinationCredentials,
    DestinationOut,
    DestinationPatch,
    IntegrationEventOut,
)
from .metrics import record_integration_attempt, set_integration_worker_state
from .secret_storage import is_sealed_text, seal_text, unseal_text

logger = logging.getLogger("lians.integrations")

_worker_last_poll_at: datetime | None = None
_worker_last_heartbeat_at: datetime | None = None
_worker_last_iteration_healthy = False

INTEGRATION_SECRET_PURPOSE = "integration-destination-secret-config"
INTEGRATION_PAYLOAD_PURPOSE = "integration-outbox-event-payload"
_BLOCKED_HOST_SUFFIXES = (".localhost", ".local", ".home", ".lan")
_RETRYABLE_STATUS_CODES = {408, 425, 429}


class IntegrationConfigurationError(ValueError):
    """A destination or event violates a security/configuration invariant."""


class IntegrationConflictError(ValueError):
    """An optimistic-concurrency or idempotency conflict."""


class IntegrationNotFoundError(ValueError):
    """The requested integration resource is outside the caller boundary."""


def _canonical_json(value: Any) -> str:
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        default=str,
    )


def _json_safe(value: Any) -> Any:
    return json.loads(_canonical_json(value))


def _sha256(value: bytes | str) -> str:
    if isinstance(value, str):
        value = value.encode("utf-8")
    return hashlib.sha256(value).hexdigest()


def _secret_context(namespace: str, destination_id: UUID) -> str:
    return f"{namespace}:{destination_id}"


def _payload_context(namespace: str, event_id: UUID) -> str:
    return f"{namespace}:{event_id}"


def _credentials_dict(credentials: DestinationCredentials) -> dict[str, Any]:
    return {
        "kind": credentials.kind,
        "token": credentials.token.get_secret_value() if credentials.token else None,
        "username": credentials.username,
        "password": (credentials.password.get_secret_value() if credentials.password else None),
        "header_name": credentials.header_name,
        "custom_headers": {
            name: value.get_secret_value() for name, value in credentials.custom_headers.items()
        },
    }


def _build_secret_config(
    *, credentials: DestinationCredentials, signing_secret: str, url: str
) -> dict[str, Any]:
    return {
        "version": 1,
        "auth": _credentials_dict(credentials),
        "signing_secret": signing_secret,
        "url": url,
    }


def _seal_secret_config(*, namespace: str, destination_id: UUID, config: dict[str, Any]) -> str:
    return seal_text(
        _canonical_json(config),
        purpose=INTEGRATION_SECRET_PURPOSE,
        context=_secret_context(namespace, destination_id),
    )


def _unseal_secret_config(destination: IntegrationDestination) -> dict[str, Any]:
    value = destination.secret_config_encrypted
    if not is_sealed_text(value):
        raise IntegrationConfigurationError("Destination secret configuration is not encrypted")
    plaintext = unseal_text(
        value,
        purpose=INTEGRATION_SECRET_PURPOSE,
        context=_secret_context(destination.namespace, destination.id),
    )
    decoded = json.loads(plaintext)
    if decoded.get("version") != 1 or not isinstance(decoded.get("auth"), dict):
        raise IntegrationConfigurationError(
            "Destination secret configuration has an unsupported version"
        )
    if not isinstance(decoded.get("signing_secret"), str):
        raise IntegrationConfigurationError("Destination signing secret is missing")
    if not isinstance(decoded.get("url"), str):
        raise IntegrationConfigurationError("Destination URL is missing")
    return decoded


def _unseal_event_payload(event: IntegrationOutboxEvent) -> dict[str, Any]:
    return unseal_event_payload_value(
        namespace=event.namespace,
        event_id=event.id,
        payload_encrypted=event.payload_encrypted,
        payload_hash=event.payload_hash,
    )


def unseal_event_payload_value(
    *,
    namespace: str,
    event_id: UUID,
    payload_encrypted: str,
    payload_hash: str,
) -> dict[str, Any]:
    """Authenticate one already-budgeted payload value without ORM lazy I/O."""

    value = payload_encrypted
    if not is_sealed_text(value):
        raise IntegrationConfigurationError("Integration event payload is not encrypted")
    plaintext = unseal_text(
        value,
        purpose=INTEGRATION_PAYLOAD_PURPOSE,
        context=_payload_context(namespace, event_id),
    )
    payload = json.loads(plaintext)
    if not isinstance(payload, dict):
        raise IntegrationConfigurationError("Integration event payload is not an object")
    if _sha256(_canonical_json(payload)) != payload_hash:
        raise IntegrationConfigurationError("Integration event payload hash mismatch")
    return payload


def destination_out(row: IntegrationDestination) -> DestinationOut:
    return DestinationOut(
        id=row.id,
        namespace=row.namespace,
        barrier_group=row.barrier_group,
        name=row.name,
        destination_type=row.destination_type,
        url_origin=row.url_origin,
        url_fingerprint=row.url_fingerprint,
        event_patterns=list(row.event_patterns or []),
        payload_profile=row.payload_profile,
        credential_kind=row.credential_kind,
        credential_configured=row.credential_kind != "none",
        custom_header_names=list(row.custom_header_names or []),
        signing_secret_fingerprint=row.secret_fingerprint,
        max_attempts=row.max_attempts,
        timeout_seconds=row.timeout_seconds,
        enabled=row.enabled,
        description=row.description,
        version=row.version,
        created_at=row.created_at,
        updated_at=row.updated_at,
        revoked_at=row.revoked_at,
        last_success_at=row.last_success_at,
        last_failure_at=row.last_failure_at,
    )


def _address_allowed(address: ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
    settings = get_settings()
    if (
        address.is_loopback
        or address.is_link_local
        or address.is_multicast
        or address.is_unspecified
        or address.is_reserved
    ):
        return False
    if address.is_private and not settings.integration_allow_private_network:
        return False
    return address.is_global or (settings.integration_allow_private_network and address.is_private)


def _validate_url_syntax(url: str) -> tuple[str, int]:
    settings = get_settings()
    if len(url) > 2048:
        raise IntegrationConfigurationError("Destination URL is too long")
    if any(ord(char) <= 32 or ord(char) == 127 for char in url):
        raise IntegrationConfigurationError(
            "Destination URL must not contain whitespace or control characters"
        )
    parsed = urlsplit(url)
    scheme = parsed.scheme.casefold()
    if scheme != "https" and not (scheme == "http" and settings.integration_allow_insecure_http):
        raise IntegrationConfigurationError("Destination URL must use HTTPS")
    if parsed.username is not None or parsed.password is not None:
        raise IntegrationConfigurationError("Destination URL must not contain userinfo")
    if parsed.query:
        raise IntegrationConfigurationError(
            "Destination URL must not contain query parameters; store tokens "
            "as encrypted credentials"
        )
    if parsed.fragment:
        raise IntegrationConfigurationError("Destination URL must not contain a fragment")
    if not parsed.hostname:
        raise IntegrationConfigurationError("Destination URL must include a hostname")
    try:
        port = parsed.port or (443 if scheme == "https" else 80)
    except ValueError as exc:
        raise IntegrationConfigurationError("Destination URL contains an invalid port") from exc
    host = parsed.hostname.casefold().rstrip(".")
    try:
        literal = ipaddress.ip_address(host)
    except ValueError:
        try:
            host = host.encode("idna").decode("ascii")
        except UnicodeError as exc:
            raise IntegrationConfigurationError(
                "Destination URL contains an invalid hostname"
            ) from exc
        literal = None
    if host == "localhost" or host.endswith(".localhost"):
        raise IntegrationConfigurationError("Destination URL uses a blocked host")
    if host.endswith(_BLOCKED_HOST_SUFFIXES) and not settings.integration_allow_private_network:
        raise IntegrationConfigurationError("Destination URL uses a private host suffix")
    if literal is not None:
        if not _address_allowed(literal):
            raise IntegrationConfigurationError(
                "Destination URL resolves to a blocked network address"
            )
    return host, port


def _url_origin(url: str) -> str:
    parsed = urlsplit(url)
    host = parsed.hostname or ""
    if ":" in host and not host.startswith("["):
        host = f"[{host}]"
    port = parsed.port
    default_port = 443 if parsed.scheme.casefold() == "https" else 80
    authority = host if port is None or port == default_port else f"{host}:{port}"
    return f"{parsed.scheme.casefold()}://{authority}"


async def _resolve_destination_addresses(
    url: str,
) -> tuple[str, int, list[ipaddress.IPv4Address | ipaddress.IPv6Address]]:
    """Resolve and validate every address, returning a connect-time pin set."""
    host, port = _validate_url_syntax(url)
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
        raise IntegrationConfigurationError("Destination hostname could not be resolved") from exc
    if not addresses:
        raise IntegrationConfigurationError("Destination hostname could not be resolved")
    resolved: set[ipaddress.IPv4Address | ipaddress.IPv6Address] = set()
    for info in addresses:
        raw_address = str(info[4][0]).split("%", 1)[0]
        address = ipaddress.ip_address(raw_address)
        if not _address_allowed(address):
            raise IntegrationConfigurationError(
                "Destination hostname resolves to a blocked network address"
            )
        resolved.add(address)
    if not resolved:
        raise IntegrationConfigurationError("Destination hostname could not be resolved")
    return host, port, sorted(
        resolved,
        key=lambda address: (address.version, int(address)),
    )


async def validate_destination_url(url: str) -> None:
    """Validate current DNS answers when a destination is created or changed."""
    await _resolve_destination_addresses(url)


def _pinned_destination_url(
    url: str,
    host: str,
    port: int,
    address: ipaddress.IPv4Address | ipaddress.IPv6Address,
) -> tuple[str, str]:
    """Build an IP-pinned URL plus the original HTTP Host authority."""
    parsed = urlsplit(url)
    scheme = parsed.scheme.casefold()
    default_port = 443 if scheme == "https" else 80
    ip_host = f"[{address.compressed}]" if address.version == 6 else address.compressed
    pinned_authority = ip_host if port == default_port else f"{ip_host}:{port}"
    host_authority = host if ":" not in host else f"[{host}]"
    if port != default_port:
        host_authority = f"{host_authority}:{port}"
    return (
        urlunsplit((scheme, pinned_authority, parsed.path or "/", "", "")),
        host_authority,
    )


def event_pattern_matches(pattern: str, event_type: str) -> bool:
    if pattern == "*":
        return True
    if pattern.endswith(".*"):
        return event_type.startswith(pattern[:-1])
    return hmac.compare_digest(pattern, event_type)


def _destination_matches(destination: IntegrationDestination, event_type: str) -> bool:
    return any(
        event_pattern_matches(pattern, event_type) for pattern in (destination.event_patterns or [])
    )


def _destination_capacity_lock_id(namespace: str) -> int:
    digest = hashlib.sha256(
        b"lians/integration-destination-capacity/v1\0" + namespace.encode("utf-8")
    ).digest()
    return int.from_bytes(digest[:8], byteorder="big", signed=True)


async def _assert_active_destination_capacity(
    db: AsyncSession,
    *,
    namespace: str,
) -> None:
    """Serialize and enforce the active-destination registration ceiling."""

    if db.get_bind().dialect.name == "postgresql":
        await db.execute(
            text("SELECT pg_advisory_xact_lock(:lock_id)"),
            {"lock_id": _destination_capacity_lock_id(namespace)},
        )
    limit = get_settings().integration_max_destinations_per_event
    active = int(
        (
            await db.execute(
                select(func.count())
                .select_from(IntegrationDestination)
                .where(
                    IntegrationDestination.namespace == namespace,
                    IntegrationDestination.enabled.is_(True),
                    IntegrationDestination.revoked_at.is_(None),
                )
            )
        ).scalar_one()
    )
    if active >= limit:
        raise IntegrationConfigurationError(
            f"Active integration destinations reached the per-namespace limit of {limit}"
        )


async def _matching_destinations(
    db: AsyncSession,
    *,
    namespace: str,
    barrier_group: str | None,
    event_type: str,
    force_destination_id: UUID | None = None,
) -> list[IntegrationDestination]:
    limit = get_settings().integration_max_destinations_per_event
    filters: list[Any] = [
        IntegrationDestination.namespace == namespace,
        IntegrationDestination.enabled.is_(True),
        IntegrationDestination.revoked_at.is_(None),
    ]
    if force_destination_id is not None:
        filters.append(IntegrationDestination.id == force_destination_id)
    if barrier_group is not None:
        filters.append(
            or_(
                IntegrationDestination.barrier_group.is_(None),
                IntegrationDestination.barrier_group == barrier_group,
            )
        )
    stmt = select(IntegrationDestination).where(*filters).order_by(IntegrationDestination.id)
    if force_destination_id is None:
        # Registration normally guarantees this bound. LIMIT + 1 is the
        # definitive enqueue-time fence for legacy rows or concurrent drift;
        # refusing the whole event avoids silent partial fan-out.
        stmt = stmt.limit(limit + 1)
    rows = list((await db.execute(stmt)).scalars())
    if force_destination_id is None and len(rows) > limit:
        raise IntegrationConfigurationError(
            f"Active integration destinations exceed the per-event limit of {limit}"
        )
    if force_destination_id is not None:
        return rows
    return [row for row in rows if _destination_matches(row, event_type)]


def _delivery_barrier(event_barrier: str | None, destination_barrier: str | None) -> str | None:
    return event_barrier if event_barrier is not None else destination_barrier


def _delivery_resources_match_boundary(
    delivery: IntegrationDelivery,
    destination: IntegrationDestination,
    event: IntegrationOutboxEvent,
) -> bool:
    """Recheck denormalized tenant/barrier joins before any outbound side effect."""

    if (
        delivery.destination_id != destination.id
        or delivery.event_id != event.id
        or delivery.namespace != destination.namespace
        or delivery.namespace != event.namespace
    ):
        return False
    if (
        destination.barrier_group is not None
        and event.barrier_group is not None
        and destination.barrier_group != event.barrier_group
    ):
        return False
    return delivery.barrier_group == _delivery_barrier(
        event.barrier_group,
        destination.barrier_group,
    )


def _delivery_idempotency_key(namespace: str, event_id: UUID, destination_id: UUID) -> str:
    return _sha256(f"lians-delivery/v1/{namespace}/{event_id}/{destination_id}")


async def enqueue_integration_event(
    db: AsyncSession,
    *,
    namespace: str,
    event_type: str,
    payload: dict[str, Any],
    barrier_group: str | None = None,
    schema_version: str = "1",
    aggregate_type: str | None = None,
    aggregate_id: str | None = None,
    source_event_id: UUID | None = None,
    correlation_id: str | None = None,
    idempotency_key: str | None = None,
    occurred_at: datetime | None = None,
    force_destination_id: UUID | None = None,
    store_without_destinations: bool = True,
) -> tuple[IntegrationOutboxEvent | None, list[IntegrationDelivery], bool]:
    """Create an event and delivery fan-out in the caller's current transaction."""
    if not event_type or len(event_type) > 255:
        raise IntegrationConfigurationError("event_type must be 1-255 characters")
    safe_payload = _json_safe(payload)
    canonical_payload = _canonical_json(safe_payload)
    if len(canonical_payload.encode("utf-8")) > get_settings().integration_max_payload_bytes:
        raise IntegrationConfigurationError(
            "Integration payload exceeds INTEGRATION_MAX_PAYLOAD_BYTES"
        )
    now = datetime.now(UTC)
    key = idempotency_key or f"event:{uuid.uuid4()}"
    existing = (
        await db.execute(
            select(IntegrationOutboxEvent)
            .options(
                defer(
                    IntegrationOutboxEvent.payload_encrypted,
                    raiseload=True,
                )
            )
            .where(
                IntegrationOutboxEvent.namespace == namespace,
                IntegrationOutboxEvent.idempotency_key == key,
            )
        )
    ).scalar_one_or_none()
    if existing is not None:
        if existing.event_type != event_type or existing.payload_hash != _sha256(canonical_payload):
            raise IntegrationConflictError(
                "Idempotency key is already bound to a different integration event"
            )
        delivery_limit = get_settings().integration_max_destinations_per_event
        deliveries = list(
            (
                await db.execute(
                    select(IntegrationDelivery)
                    .where(
                        IntegrationDelivery.event_id == existing.id,
                        IntegrationDelivery.namespace == namespace,
                    )
                    .order_by(IntegrationDelivery.created_at, IntegrationDelivery.id)
                    .limit(delivery_limit + 1)
                )
            ).scalars()
        )
        if len(deliveries) > delivery_limit:
            raise IntegrationConflictError(
                "Stored integration event exceeds the delivery fan-out contract"
            )
        return existing, deliveries, False

    destinations = await _matching_destinations(
        db,
        namespace=namespace,
        barrier_group=barrier_group,
        event_type=event_type,
        force_destination_id=force_destination_id,
    )
    if not destinations and not store_without_destinations:
        return None, [], False
    if force_destination_id is not None and not destinations:
        raise IntegrationNotFoundError("Active integration destination not found")

    event_id = uuid.uuid4()
    event = IntegrationOutboxEvent(
        id=event_id,
        namespace=namespace,
        barrier_group=barrier_group,
        event_type=event_type,
        schema_version=schema_version,
        aggregate_type=aggregate_type,
        aggregate_id=aggregate_id,
        source_event_id=source_event_id,
        correlation_id=correlation_id,
        idempotency_key=key,
        payload_encrypted=seal_text(
            canonical_payload,
            purpose=INTEGRATION_PAYLOAD_PURPOSE,
            context=_payload_context(namespace, event_id),
        ),
        payload_hash=_sha256(canonical_payload),
        occurred_at=occurred_at or now,
        enqueued_at=now,
    )
    db.add(event)
    try:
        await db.flush()
    except IntegrityError as exc:
        raise IntegrationConflictError(
            "Integration event idempotency key was claimed concurrently"
        ) from exc

    deliveries: list[IntegrationDelivery] = []
    for destination in destinations:
        delivery = IntegrationDelivery(
            id=uuid.uuid4(),
            namespace=namespace,
            barrier_group=_delivery_barrier(barrier_group, destination.barrier_group),
            event_id=event.id,
            destination_id=destination.id,
            run_sequence=1,
            idempotency_key=_delivery_idempotency_key(namespace, event.id, destination.id),
            status="pending",
            next_attempt_at=now,
            created_at=now,
            updated_at=now,
        )
        db.add(delivery)
        deliveries.append(delivery)
    if deliveries:
        await db.flush()
    return event, deliveries, True


async def enqueue_audit_event(
    db: AsyncSession,
    *,
    event_id: UUID,
    namespace: str,
    agent_id: str,
    operation: str,
    memory_id: UUID | None,
    content_hash: str | None,
    row_hash: str,
    payload: dict[str, Any],
    occurred_at: datetime,
    barrier_group: str | None = None,
) -> None:
    """Transactional bridge from the global audit chain into subscribed sinks."""
    settings = get_settings()
    payload_barrier = payload.get("barrier_group")
    if barrier_group is None and isinstance(payload_barrier, str):
        barrier_group = payload_barrier
    if barrier_group is None and memory_id is not None:
        # Internal/admin service calls may not carry an authenticated barrier
        # ContextVar. Recover the source memory boundary before fan-out rather
        # than silently treating a desk-specific event as namespace-wide.
        from .models import Memory

        barrier_group = (
            await db.execute(
                select(Memory.barrier_group).where(
                    Memory.id == memory_id,
                    Memory.namespace == namespace,
                )
            )
        ).scalar_one_or_none()
    event_payload: dict[str, Any] = {
        "audit_event_id": str(event_id),
        "operation": operation,
        "actor_ref_hash": _sha256(agent_id),
        "memory_id": str(memory_id) if memory_id is not None else None,
        "content_hash": content_hash,
        "audit_row_hash": row_hash,
        "audit_payload_hash": _sha256(_canonical_json(_json_safe(payload))),
    }
    if settings.integration_include_audit_payload:
        event_payload["audit_payload"] = _json_safe(payload)
    await enqueue_integration_event(
        db,
        namespace=namespace,
        barrier_group=barrier_group,
        event_type=f"audit.{operation}",
        payload=event_payload,
        schema_version="1",
        aggregate_type="audit_event",
        aggregate_id=str(event_id),
        source_event_id=event_id,
        idempotency_key=f"audit:{event_id}",
        occurred_at=occurred_at,
        store_without_destinations=False,
    )


async def create_destination(
    db: AsyncSession,
    *,
    namespace: str,
    body: DestinationCreate,
    effective_barrier_group: str | None,
) -> tuple[IntegrationDestination, str]:
    await validate_destination_url(body.url)
    if body.enabled:
        await _assert_active_destination_capacity(db, namespace=namespace)
    destination_id = uuid.uuid4()
    signing_secret = (
        body.signing_secret.get_secret_value() if body.signing_secret else secrets.token_urlsafe(48)
    )
    config = _build_secret_config(
        credentials=body.credentials,
        signing_secret=signing_secret,
        url=body.url,
    )
    now = datetime.now(UTC)
    row = IntegrationDestination(
        id=destination_id,
        namespace=namespace,
        barrier_group=effective_barrier_group,
        name=body.name,
        destination_type=body.destination_type,
        url_origin=_url_origin(body.url),
        url_fingerprint=_sha256(body.url),
        event_patterns=body.event_patterns,
        payload_profile=body.payload_profile,
        credential_kind=body.credentials.kind,
        custom_header_names=sorted(body.credentials.custom_headers),
        secret_config_encrypted=_seal_secret_config(
            namespace=namespace, destination_id=destination_id, config=config
        ),
        secret_fingerprint=_sha256(signing_secret),
        max_attempts=body.max_attempts,
        timeout_seconds=body.timeout_seconds,
        enabled=body.enabled,
        description=body.description,
        version=1,
        created_at=now,
        updated_at=now,
    )
    db.add(row)
    try:
        await db.flush()
    except IntegrityError as exc:
        raise IntegrationConflictError(
            "An integration destination with this name already exists"
        ) from exc
    return row, signing_secret


def _boundary_conditions(*, namespace: str, barrier_group: str | None) -> list[Any]:
    conditions: list[Any] = [IntegrationDestination.namespace == namespace]
    if barrier_group is not None:
        conditions.append(IntegrationDestination.barrier_group == barrier_group)
    return conditions


async def get_destination(
    db: AsyncSession,
    *,
    destination_id: UUID,
    namespace: str,
    barrier_group: str | None,
    for_update: bool = False,
    include_secret_config: bool = True,
) -> IntegrationDestination:
    stmt = select(IntegrationDestination).where(
        IntegrationDestination.id == destination_id,
        *_boundary_conditions(namespace=namespace, barrier_group=barrier_group),
    )
    if not include_secret_config:
        stmt = stmt.options(
            defer(
                IntegrationDestination.secret_config_encrypted,
                raiseload=True,
            )
        )
    if for_update:
        stmt = stmt.with_for_update()
    row = (await db.execute(stmt)).scalar_one_or_none()
    if row is None:
        raise IntegrationNotFoundError("Integration destination not found")
    return row


async def update_destination(
    db: AsyncSession,
    *,
    destination_id: UUID,
    namespace: str,
    barrier_group: str | None,
    body: DestinationPatch,
) -> tuple[IntegrationDestination, list[str]]:
    row = await get_destination(
        db,
        destination_id=destination_id,
        namespace=namespace,
        barrier_group=barrier_group,
        for_update=True,
    )
    if row.revoked_at is not None:
        raise IntegrationConflictError("Integration destination is revoked")
    if row.version != body.expected_version:
        raise IntegrationConflictError(f"Version conflict; current version is {row.version}")
    changes = body.model_dump(exclude_unset=True)
    changes.pop("expected_version", None)
    changed_fields = sorted(changes)
    if changes.get("enabled") is True and not row.enabled:
        await _assert_active_destination_capacity(db, namespace=namespace)
    if "url" in changes:
        await validate_destination_url(changes["url"])
        secret_config = _unseal_secret_config(row)
        secret_config["url"] = changes.pop("url")
        row.secret_config_encrypted = _seal_secret_config(
            namespace=row.namespace,
            destination_id=row.id,
            config=secret_config,
        )
        row.url_origin = _url_origin(secret_config["url"])
        row.url_fingerprint = _sha256(secret_config["url"])
    for field, value in changes.items():
        setattr(row, field, value)
    row.version += 1
    row.updated_at = datetime.now(UTC)
    try:
        await db.flush()
    except IntegrityError as exc:
        raise IntegrationConflictError(
            "An integration destination with this name already exists"
        ) from exc
    return row, changed_fields


async def rotate_destination_secrets(
    db: AsyncSession,
    *,
    destination_id: UUID,
    namespace: str,
    barrier_group: str | None,
    expected_version: int,
    credentials: DestinationCredentials | None,
    signing_secret: str | None,
) -> tuple[IntegrationDestination, str]:
    row = await get_destination(
        db,
        destination_id=destination_id,
        namespace=namespace,
        barrier_group=barrier_group,
        for_update=True,
    )
    if row.revoked_at is not None:
        raise IntegrationConflictError("Integration destination is revoked")
    if row.version != expected_version:
        raise IntegrationConflictError(f"Version conflict; current version is {row.version}")
    prior = _unseal_secret_config(row)
    new_signing_secret = signing_secret or secrets.token_urlsafe(48)
    auth = prior["auth"] if credentials is None else _credentials_dict(credentials)
    config = {
        "version": 1,
        "auth": auth,
        "signing_secret": new_signing_secret,
        "url": prior["url"],
    }
    row.secret_config_encrypted = _seal_secret_config(
        namespace=namespace, destination_id=row.id, config=config
    )
    row.secret_fingerprint = _sha256(new_signing_secret)
    if credentials is not None:
        row.credential_kind = credentials.kind
        row.custom_header_names = sorted(credentials.custom_headers)
    row.version += 1
    row.updated_at = datetime.now(UTC)
    await db.flush()
    return row, new_signing_secret


async def revoke_destination(
    db: AsyncSession,
    *,
    destination_id: UUID,
    namespace: str,
    barrier_group: str | None,
    expected_version: int,
) -> IntegrationDestination:
    row = await get_destination(
        db,
        destination_id=destination_id,
        namespace=namespace,
        barrier_group=barrier_group,
        for_update=True,
    )
    if row.revoked_at is not None:
        raise IntegrationConflictError("Integration destination is already revoked")
    if row.version != expected_version:
        raise IntegrationConflictError(f"Version conflict; current version is {row.version}")
    now = datetime.now(UTC)
    row.enabled = False
    row.revoked_at = now
    row.updated_at = now
    row.version += 1
    await db.execute(
        update(IntegrationDelivery)
        .where(
            IntegrationDelivery.destination_id == row.id,
            IntegrationDelivery.namespace == namespace,
            IntegrationDelivery.status.in_(("pending", "retry")),
        )
        .values(
            status="cancelled",
            cancelled_at=now,
            updated_at=now,
            lease_owner=None,
            lease_expires_at=None,
        )
        .execution_options(synchronize_session=False)
    )
    await db.flush()
    return row


async def replay_delivery(
    db: AsyncSession,
    *,
    delivery_id: UUID,
    namespace: str,
    barrier_group: str | None,
) -> IntegrationDelivery:
    filters: list[Any] = [
        IntegrationDelivery.id == delivery_id,
        IntegrationDelivery.namespace == namespace,
    ]
    if barrier_group is not None:
        filters.append(IntegrationDelivery.barrier_group == barrier_group)
    prior = (
        await db.execute(select(IntegrationDelivery).where(*filters).with_for_update())
    ).scalar_one_or_none()
    if prior is None:
        raise IntegrationNotFoundError("Integration delivery not found")
    if prior.status not in {"dead_letter", "cancelled"}:
        raise IntegrationConflictError("Only dead-lettered or cancelled deliveries can be replayed")
    existing_replay = (
        await db.execute(
            select(IntegrationDelivery.id).where(
                IntegrationDelivery.replayed_from_id == prior.id,
                IntegrationDelivery.namespace == namespace,
            )
        )
    ).scalar_one_or_none()
    if existing_replay is not None:
        raise IntegrationConflictError(f"Delivery was already replayed as {existing_replay}")
    destination = (
        await db.execute(
            select(IntegrationDestination)
            .options(
                defer(
                    IntegrationDestination.secret_config_encrypted,
                    raiseload=True,
                )
            )
            .where(
                IntegrationDestination.id == prior.destination_id,
                IntegrationDestination.namespace == namespace,
            )
        )
    ).scalar_one_or_none()
    event = (
        await db.execute(
            select(IntegrationOutboxEvent)
            .options(
                defer(
                    IntegrationOutboxEvent.payload_encrypted,
                    raiseload=True,
                )
            )
            .where(
                IntegrationOutboxEvent.id == prior.event_id,
                IntegrationOutboxEvent.namespace == namespace,
            )
        )
    ).scalar_one_or_none()
    if (
        destination is None
        or event is None
        or not _delivery_resources_match_boundary(prior, destination, event)
    ):
        raise IntegrationConflictError(
            "Referenced resources do not match the delivery boundary"
        )
    if destination.revoked_at is not None or not destination.enabled:
        raise IntegrationConflictError(
            "Destination must be active before a delivery can be replayed"
        )
    now = datetime.now(UTC)
    replay = IntegrationDelivery(
        id=uuid.uuid4(),
        namespace=prior.namespace,
        barrier_group=prior.barrier_group,
        event_id=prior.event_id,
        destination_id=prior.destination_id,
        run_sequence=prior.run_sequence + 1,
        replayed_from_id=prior.id,
        idempotency_key=prior.idempotency_key,
        status="pending",
        attempt_count=0,
        next_attempt_at=now,
        created_at=now,
        updated_at=now,
    )
    db.add(replay)
    await db.flush()
    return replay


def event_out(
    row: IntegrationOutboxEvent,
    delivery_count: int = 0,
    *,
    include_payload: bool = False,
    payload: dict[str, Any] | None = None,
) -> IntegrationEventOut:
    resolved_payload = None
    if include_payload:
        resolved_payload = payload if payload is not None else _unseal_event_payload(row)
    return IntegrationEventOut(
        id=row.id,
        namespace=row.namespace,
        barrier_group=row.barrier_group,
        event_type=row.event_type,
        schema_version=row.schema_version,
        aggregate_type=row.aggregate_type,
        aggregate_id=row.aggregate_id,
        source_event_id=row.source_event_id,
        correlation_id=row.correlation_id,
        idempotency_key=row.idempotency_key,
        payload=resolved_payload,
        payload_included=include_payload,
        payload_hash=row.payload_hash,
        occurred_at=row.occurred_at,
        enqueued_at=row.enqueued_at,
        delivery_count=delivery_count,
    )


@dataclass(frozen=True)
class _HttpResult:
    status_code: int | None
    error_code: str | None
    error_digest: str | None
    response_digest: str | None
    retry_after_seconds: float | None

    @property
    def delivered(self) -> bool:
        return self.status_code is not None and 200 <= self.status_code < 300

    @property
    def retryable(self) -> bool:
        return (
            self.status_code is None
            or self.status_code in _RETRYABLE_STATUS_CODES
            or self.status_code >= 500
        )


def _cloud_event(event: IntegrationOutboxEvent, payload: dict[str, Any]) -> dict[str, Any]:
    subject = None
    if event.aggregate_type and event.aggregate_id:
        subject = f"{event.aggregate_type}/{event.aggregate_id}"
    envelope: dict[str, Any] = {
        "specversion": "1.0",
        "id": str(event.id),
        "source": f"urn:lians:namespace:{event.namespace}",
        "type": event.event_type,
        "time": event.occurred_at.astimezone(UTC).isoformat(),
        "datacontenttype": "application/json",
        "dataschema": f"urn:lians:integration-event:{event.schema_version}",
        "lianspayloadhash": event.payload_hash,
        "data": payload,
    }
    if subject:
        envelope["subject"] = subject
    if event.correlation_id:
        envelope["lianscorrelationid"] = event.correlation_id
    return envelope


def _request_material(
    destination: IntegrationDestination,
    event: IntegrationOutboxEvent,
    delivery: IntegrationDelivery,
) -> tuple[str, bytes, dict[str, str]]:
    secret_config = _unseal_secret_config(destination)
    event_payload = _unseal_event_payload(event)
    payload = (
        event_payload
        if destination.payload_profile == "raw"
        else _cloud_event(event, event_payload)
    )
    body = _canonical_json(payload).encode("utf-8")
    timestamp = str(int(time.time()))
    signature = hmac.new(
        secret_config["signing_secret"].encode("utf-8"),
        timestamp.encode("ascii") + b"." + body,
        hashlib.sha256,
    ).hexdigest()
    headers = {
        "Content-Type": "application/json",
        "User-Agent": "Lians-Integration-Outbox/1.0",
        "Idempotency-Key": delivery.idempotency_key,
        "X-Lians-Event-ID": str(event.id),
        "X-Lians-Delivery-ID": str(delivery.id),
        "X-Lians-Signature": f"t={timestamp},v1={signature}",
    }
    auth = secret_config["auth"]
    kind = auth.get("kind", "none")
    if kind == "bearer":
        headers["Authorization"] = f"Bearer {auth['token']}"
    elif kind == "splunk_hec":
        headers["Authorization"] = f"Splunk {auth['token']}"
    elif kind == "basic":
        token = base64.b64encode(f"{auth['username']}:{auth['password']}".encode()).decode("ascii")
        headers["Authorization"] = f"Basic {token}"
    elif kind == "api_key_header":
        headers[auth["header_name"]] = auth["token"]
    for name, value in (auth.get("custom_headers") or {}).items():
        headers[name] = value
    return secret_config["url"], body, headers


def _retry_after(value: str | None) -> float | None:
    if not value:
        return None
    try:
        return max(0.0, float(value))
    except ValueError:
        try:
            when = parsedate_to_datetime(value)
            if when.tzinfo is None:
                when = when.replace(tzinfo=UTC)
            return max(0.0, (when.astimezone(UTC) - datetime.now(UTC)).total_seconds())
        except (TypeError, ValueError, OverflowError):
            return None


async def _post(
    *,
    url: str,
    destination: IntegrationDestination,
    body: bytes,
    headers: dict[str, str],
) -> _HttpResult:
    try:
        host, port, addresses = await _resolve_destination_addresses(url)
    except IntegrationConfigurationError as exc:
        detail = str(exc)
        return _HttpResult(None, "destination_blocked", _sha256(detail), None, None)
    # Connect to the validated IP itself. HTTP Host and TLS SNI/certificate
    # verification retain the configured hostname, so DNS cannot change the
    # destination between validation and connect.
    pinned_url, host_authority = _pinned_destination_url(
        url, host, port, addresses[0]
    )
    request_headers = {**headers, "Host": host_authority}
    try:
        async with (
            httpx.AsyncClient(
                timeout=httpx.Timeout(destination.timeout_seconds),
                follow_redirects=False,
                trust_env=False,
            ) as client,
            client.stream(
                "POST",
                pinned_url,
                content=body,
                headers=request_headers,
                extensions={"sni_hostname": host},
            ) as response,
        ):
            remaining = get_settings().integration_max_response_digest_bytes
            response_hasher = hashlib.sha256()
            if remaining:
                async for chunk in response.aiter_bytes():
                    if not chunk:
                        continue
                    selected = chunk[:remaining]
                    response_hasher.update(selected)
                    remaining -= len(selected)
                    if remaining <= 0:
                        break
            return _HttpResult(
                response.status_code,
                None if response.is_success else f"http_{response.status_code}",
                None,
                response_hasher.hexdigest(),
                _retry_after(response.headers.get("Retry-After")),
            )
    except httpx.TimeoutException as exc:
        return _HttpResult(None, "timeout", _sha256(type(exc).__name__), None, None)
    except httpx.NetworkError as exc:
        return _HttpResult(None, "network_error", _sha256(type(exc).__name__), None, None)
    except Exception as exc:
        logger.error(
            "Unexpected integration transport failure",
            extra={"error_type": type(exc).__name__},
        )
        return _HttpResult(None, "transport_error", _sha256(type(exc).__name__), None, None)


def _backoff_seconds(delivery_id: UUID, attempt_number: int) -> float:
    settings = get_settings()
    ceiling = min(
        settings.integration_retry_max_seconds,
        settings.integration_retry_base_seconds * (2 ** max(0, attempt_number - 1)),
    )
    digest = hashlib.sha256(f"{delivery_id}:{attempt_number}".encode()).digest()
    jitter = 0.5 + int.from_bytes(digest[:4], "big") / (2**32) * 0.5
    return max(0.1, ceiling * jitter)


async def claim_due_deliveries(
    db: AsyncSession,
    *,
    worker_id: str,
    batch_size: int,
    lease_seconds: int,
) -> list[UUID]:
    now = datetime.now(UTC)
    eligible = or_(
        and_(
            IntegrationDelivery.status.in_(("pending", "retry")),
            IntegrationDelivery.next_attempt_at <= now,
        ),
        and_(
            IntegrationDelivery.status == "leased",
            IntegrationDelivery.lease_expires_at <= now,
        ),
    )
    stmt = (
        select(IntegrationDelivery)
        .where(eligible)
        .order_by(IntegrationDelivery.next_attempt_at, IntegrationDelivery.created_at)
        .limit(batch_size)
        .with_for_update(skip_locked=db.get_bind().dialect.name == "postgresql")
    )
    rows = (await db.execute(stmt)).scalars().all()
    expires = now + timedelta(seconds=lease_seconds)
    for row in rows:
        row.status = "leased"
        row.lease_owner = worker_id
        row.lease_expires_at = expires
        row.updated_at = now
    await db.commit()
    return [row.id for row in rows]


async def _cancel_claimed_delivery(
    db: AsyncSession,
    *,
    delivery: IntegrationDelivery,
    worker_id: str,
    reason: str,
) -> None:
    now = datetime.now(UTC)
    attempt_number = delivery.attempt_count + 1
    delivery.status = "cancelled"
    delivery.attempt_count = attempt_number
    delivery.last_attempt_at = now
    delivery.cancelled_at = now
    delivery.last_error_code = reason
    delivery.last_error_digest = _sha256(reason)
    delivery.lease_owner = None
    delivery.lease_expires_at = None
    delivery.updated_at = now
    db.add(
        IntegrationDeliveryAttempt(
            id=uuid.uuid4(),
            namespace=delivery.namespace,
            barrier_group=delivery.barrier_group,
            delivery_id=delivery.id,
            attempt_number=attempt_number,
            worker_id=worker_id,
            outcome="cancelled",
            error_code=reason,
            error_digest=_sha256(reason),
            duration_ms=0,
            started_at=now,
            finished_at=now,
        )
    )
    await db.commit()
    record_integration_attempt("cancelled")


async def deliver_claimed(
    session_factory: async_sessionmaker[AsyncSession],
    *,
    delivery_id: UUID,
    worker_id: str,
) -> None:
    set_current_namespace("__admin__")
    set_current_barrier_group(None)
    try:
        prepared: tuple[IntegrationDestination, str, bytes, dict[str, str]] | None = None
        result: _HttpResult | None = None
        elapsed_ms = 0
        async with session_factory() as db:
            delivery = await db.get(IntegrationDelivery, delivery_id)
            if delivery is None or delivery.status != "leased" or delivery.lease_owner != worker_id:
                return
            destination = await db.get(
                IntegrationDestination,
                delivery.destination_id,
            )
            event = await db.get(IntegrationOutboxEvent, delivery.event_id)
            if destination is None or event is None:
                await _cancel_claimed_delivery(
                    db,
                    delivery=delivery,
                    worker_id=worker_id,
                    reason="referenced_resource_missing",
                )
                return
            if not _delivery_resources_match_boundary(delivery, destination, event):
                await _cancel_claimed_delivery(
                    db,
                    delivery=delivery,
                    worker_id=worker_id,
                    reason="referenced_resource_boundary_mismatch",
                )
                return
            if destination.revoked_at is not None or not destination.enabled:
                await _cancel_claimed_delivery(
                    db,
                    delivery=delivery,
                    worker_id=worker_id,
                    reason="destination_inactive",
                )
                return
            try:
                url, body, headers = _request_material(destination, event, delivery)
            except (
                IntegrationConfigurationError,
                ValueError,
                KeyError,
                json.JSONDecodeError,
            ) as exc:
                result = _HttpResult(
                    None,
                    "secret_configuration_invalid",
                    _sha256(type(exc).__name__),
                    None,
                    None,
                )
            else:
                prepared = (destination, url, body, headers)

        # Never hold a database transaction or pooled connection across the
        # network call. The lease and receiver idempotency key protect the gap.
        if prepared is not None:
            destination, url, body, headers = prepared
            started = time.monotonic()
            result = await _post(
                url=url,
                destination=destination,
                body=body,
                headers=headers,
            )
            elapsed_ms = max(0, int((time.monotonic() - started) * 1000))
        if result is None:
            raise RuntimeError(
                "Integration delivery reached persistence without a transport result"
            )

        async with session_factory() as db:
            delivery = (
                await db.execute(
                    select(IntegrationDelivery)
                    .where(IntegrationDelivery.id == delivery_id)
                    .with_for_update()
                )
            ).scalar_one_or_none()
            if delivery is None or delivery.status != "leased" or delivery.lease_owner != worker_id:
                logger.warning(
                    "Discarding integration result after lease loss"
                )
                record_integration_attempt("lease_lost")
                return
            destination = (
                await db.execute(
                    select(IntegrationDestination)
                    .options(
                        defer(
                            IntegrationDestination.secret_config_encrypted,
                            raiseload=True,
                        )
                    )
                    .where(IntegrationDestination.id == delivery.destination_id)
                )
            ).scalar_one_or_none()
            event = (
                await db.execute(
                    select(IntegrationOutboxEvent)
                    .options(
                        defer(
                            IntegrationOutboxEvent.payload_encrypted,
                            raiseload=True,
                        )
                    )
                    .where(IntegrationOutboxEvent.id == delivery.event_id)
                )
            ).scalar_one_or_none()
            if destination is None or event is None:
                await _cancel_claimed_delivery(
                    db,
                    delivery=delivery,
                    worker_id=worker_id,
                    reason="referenced_resource_missing",
                )
                return
            if not _delivery_resources_match_boundary(delivery, destination, event):
                await _cancel_claimed_delivery(
                    db,
                    delivery=delivery,
                    worker_id=worker_id,
                    reason="referenced_resource_boundary_mismatch",
                )
                return
            now = datetime.now(UTC)
            attempt_number = delivery.attempt_count + 1
            next_attempt_at: datetime | None = None
            if result.delivered:
                outcome = "delivered"
                delivery.status = "delivered"
                delivery.delivered_at = now
                destination.last_success_at = now
            elif result.retryable and attempt_number < destination.max_attempts:
                outcome = "retry"
                delay = result.retry_after_seconds
                if delay is None:
                    delay = _backoff_seconds(delivery.id, attempt_number)
                delay = min(delay, get_settings().integration_retry_max_seconds)
                next_attempt_at = now + timedelta(seconds=delay)
                delivery.status = "retry"
                delivery.next_attempt_at = next_attempt_at
                destination.last_failure_at = now
            else:
                outcome = "dead_letter"
                delivery.status = "dead_letter"
                delivery.dead_lettered_at = now
                destination.last_failure_at = now
            delivery.attempt_count = attempt_number
            delivery.last_attempt_at = now
            delivery.last_status_code = result.status_code
            delivery.last_error_code = result.error_code
            delivery.last_error_digest = result.error_digest
            delivery.last_response_digest = result.response_digest
            delivery.lease_owner = None
            delivery.lease_expires_at = None
            delivery.updated_at = now
            db.add(
                IntegrationDeliveryAttempt(
                    id=uuid.uuid4(),
                    namespace=delivery.namespace,
                    barrier_group=delivery.barrier_group,
                    delivery_id=delivery.id,
                    attempt_number=attempt_number,
                    worker_id=worker_id,
                    outcome=outcome,
                    status_code=result.status_code,
                    error_code=result.error_code,
                    error_digest=result.error_digest,
                    response_digest=result.response_digest,
                    duration_ms=elapsed_ms,
                    started_at=now - timedelta(milliseconds=elapsed_ms),
                    finished_at=now,
                    next_attempt_at=next_attempt_at,
                )
            )
            await db.commit()
            record_integration_attempt(outcome)
    finally:
        set_current_namespace(None)
        set_current_barrier_group(None)


async def run_integration_worker(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """Continuously lease and deliver outbox work; safe across many replicas."""
    global _worker_last_heartbeat_at, _worker_last_poll_at
    settings = get_settings()
    worker_id = f"{socket.gethostname()}:{os.getpid()}:{uuid.uuid4().hex[:12]}"
    delivery_concurrency = settings.integration_delivery_concurrency
    # Leases begin when rows are claimed. Bound each claim to the number that
    # can enter a provider call immediately so no row expires while merely
    # waiting behind this process's semaphore.
    claim_batch_size = min(
        settings.integration_worker_batch_size,
        delivery_concurrency,
    )
    semaphore = asyncio.Semaphore(delivery_concurrency)

    async def deliver_one(delivery_id: UUID) -> None:
        async with semaphore:
            try:
                await deliver_claimed(session_factory, delivery_id=delivery_id, worker_id=worker_id)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                _set_integration_worker_health(False)
                logger.error(
                    "Integration delivery worker iteration failed",
                    extra={"error_type": type(exc).__name__},
                )

    async def deliver_batch(delivery_ids: list[UUID]) -> None:
        """Maintain a per-replica heartbeat while bounded provider calls run."""

        global _worker_last_heartbeat_at
        batch = asyncio.gather(*(deliver_one(item) for item in delivery_ids))
        heartbeat_seconds = min(
            5.0,
            max(1.0, settings.integration_worker_poll_seconds * 2),
        )
        try:
            while not batch.done():
                done, _ = await asyncio.wait({batch}, timeout=heartbeat_seconds)
                if batch in done:
                    break
                _worker_last_heartbeat_at = datetime.now(UTC)
                refresh_integration_process_metrics()
            await batch
        finally:
            if not batch.done():
                batch.cancel()
                await asyncio.gather(batch, return_exceptions=True)

    _worker_last_heartbeat_at = datetime.now(UTC)
    _set_integration_worker_health(False)
    logger.info("Integration outbox worker started")
    while True:
        try:
            set_current_namespace("__admin__")
            set_current_barrier_group(None)
            try:
                async with session_factory() as db:
                    claimed = await claim_due_deliveries(
                        db,
                        worker_id=worker_id,
                        batch_size=claim_batch_size,
                        lease_seconds=settings.integration_lease_seconds,
                    )
                    _worker_last_poll_at = datetime.now(UTC)
                    _worker_last_heartbeat_at = _worker_last_poll_at
                    _set_integration_worker_health(True)
            finally:
                set_current_namespace(None)
                set_current_barrier_group(None)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            _set_integration_worker_health(False)
            logger.error(
                "Integration outbox claim failed",
                extra={"error_type": type(exc).__name__},
            )
            await asyncio.sleep(settings.integration_worker_poll_seconds)
            continue
        if claimed:
            await deliver_batch(claimed)
        else:
            await asyncio.sleep(settings.integration_worker_poll_seconds)


def _set_integration_worker_health(healthy: bool) -> None:
    """Persist bounded process health until a later successful claim poll."""

    global _worker_last_iteration_healthy
    _worker_last_iteration_healthy = healthy
    set_integration_worker_state(delivery_enabled=True, healthy=healthy)


def integration_worker_status() -> tuple[bool, datetime | None]:
    settings = get_settings()
    last_poll = _worker_last_heartbeat_at or _worker_last_poll_at
    if not settings.integration_worker_enabled or settings.airgap_mode:
        return False, last_poll
    threshold = max(30.0, settings.integration_worker_poll_seconds * 5)
    heartbeat_fresh = last_poll is not None and (
        datetime.now(UTC) - last_poll
    ).total_seconds() <= threshold
    healthy = _worker_last_iteration_healthy and heartbeat_fresh
    return healthy, last_poll


def refresh_integration_process_metrics() -> None:
    """Refresh time-sensitive per-replica integration worker gauges at scrape."""

    settings = get_settings()
    enabled = bool(settings.integration_worker_enabled and not settings.airgap_mode)
    healthy, _ = integration_worker_status()
    set_integration_worker_state(delivery_enabled=enabled, healthy=healthy)


async def integration_inventory(
    db: AsyncSession,
    *,
    namespace: str,
    barrier_group: str | None = None,
) -> dict[str, Any]:
    destination_filters: list[Any] = [
        IntegrationDestination.namespace == namespace,
        IntegrationDestination.enabled.is_(True),
        IntegrationDestination.revoked_at.is_(None),
    ]
    delivery_filters: list[Any] = [IntegrationDelivery.namespace == namespace]
    if barrier_group is not None:
        destination_filters.append(IntegrationDestination.barrier_group == barrier_group)
        delivery_filters.append(
            or_(
                IntegrationDelivery.barrier_group.is_(None),
                IntegrationDelivery.barrier_group == barrier_group,
            )
        )
    active_destinations = int(
        (
            await db.execute(
                select(func.count()).select_from(IntegrationDestination).where(*destination_filters)
            )
        ).scalar_one()
    )
    statuses = dict(
        (
            await db.execute(
                select(IntegrationDelivery.status, func.count())
                .where(*delivery_filters)
                .group_by(IntegrationDelivery.status)
            )
        ).all()
    )
    oldest_due = (
        await db.execute(
            select(func.min(IntegrationDelivery.next_attempt_at)).where(
                *delivery_filters,
                IntegrationDelivery.status.in_(("pending", "retry")),
            )
        )
    ).scalar_one_or_none()
    return {
        "active_destinations": active_destinations,
        "pending_deliveries": int(statuses.get("pending", 0)),
        "retry_deliveries": int(statuses.get("retry", 0)),
        "leased_deliveries": int(statuses.get("leased", 0)),
        "dead_letter_deliveries": int(statuses.get("dead_letter", 0)),
        "oldest_due_at": oldest_due,
    }
