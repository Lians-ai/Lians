"""Transactional Stripe metering outbox and multi-replica delivery worker.

Product-native decision/protected-action units and compatibility memory units
are inserted through :func:`enqueue_usage_event` in the same SQLAlchemy
transaction as the billable source mutation/audit event. A PostgreSQL-backed
worker leases rows with ``SKIP LOCKED`` and records an append-only start/result
ledger around every provider call. No process-local queue exists.
"""

from __future__ import annotations

import asyncio
import hashlib
import importlib.util
import json
import logging
import os
import re
import socket
import time
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from email.utils import parsedate_to_datetime
from typing import Any, Literal
from uuid import UUID

from sqlalchemy import and_, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from .config import get_settings
from .db import set_current_barrier_group, set_current_namespace
from .metering_models import MeteringAttemptRecord, MeteringEvent

logger = logging.getLogger("lians.metering")

_EVENT_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,99}$")
_SAFE_CODE = re.compile(r"[^A-Za-z0-9_.:-]+")

_worker_last_poll_at: datetime | None = None
_worker_last_heartbeat_at: datetime | None = None
_worker_last_delivery_at: datetime | None = None
_worker_last_error_at: datetime | None = None
_worker_last_error_digest: str | None = None
_worker_terminal_error: str | None = None
_worker_last_iteration_healthy = False
_worker_backlog: dict[str, int] = {}
_worker_oldest_due_at: datetime | None = None


class MeteringConfigurationError(RuntimeError):
    """The durable metering contract cannot safely process a request."""


class MeteringConflictError(RuntimeError):
    """One billable source identity was reused with different billing facts."""


def validate_metering_configuration(settings: Any, *, production: bool) -> list[str]:
    """Return fail-closed startup errors for the durable delivery contract."""

    errors: list[str] = []
    meter_names = (
        ("STRIPE_METER_DECISION_EVENT", settings.stripe_meter_decision_event),
        (
            "STRIPE_METER_PROTECTED_ACTION_EVENT",
            settings.stripe_meter_protected_action_event,
        ),
        ("STRIPE_METER_WRITE_EVENT", settings.stripe_meter_write_event),
        ("STRIPE_METER_RECALL_EVENT", settings.stripe_meter_recall_event),
    )
    normalized_meter_names: list[str] = []
    for label, value in meter_names:
        if not _EVENT_NAME.fullmatch(value.strip()):
            errors.append(f"{label} is not a valid 1-100 character Stripe event name")
        else:
            normalized_meter_names.append(value.strip())
    if len(set(normalized_meter_names)) != len(normalized_meter_names):
        errors.append("Stripe meter event names must be distinct across usage dimensions")
    if settings.stripe_meter_worker_poll_seconds <= 0:
        errors.append("STRIPE_METER_WORKER_POLL_SECONDS must be positive")
    if not 1 <= settings.stripe_meter_worker_batch_size <= 1_000:
        errors.append("STRIPE_METER_WORKER_BATCH_SIZE must be between 1 and 1000")
    if not 1 <= settings.stripe_meter_delivery_concurrency <= 100:
        errors.append("STRIPE_METER_DELIVERY_CONCURRENCY must be between 1 and 100")
    if not 1 <= settings.stripe_meter_provider_timeout_seconds <= 120:
        errors.append("STRIPE_METER_PROVIDER_TIMEOUT_SECONDS must be between 1 and 120")
    if settings.stripe_meter_lease_seconds < settings.stripe_meter_provider_timeout_seconds + 15:
        errors.append(
            "STRIPE_METER_LEASE_SECONDS must exceed the provider timeout by at least 15 seconds"
        )
    if settings.stripe_meter_retry_base_seconds <= 0:
        errors.append("STRIPE_METER_RETRY_BASE_SECONDS must be positive")
    if (
        settings.stripe_meter_retry_max_seconds < settings.stripe_meter_retry_base_seconds
        or settings.stripe_meter_retry_max_seconds > 3_600
    ):
        errors.append(
            "STRIPE_METER_RETRY_MAX_SECONDS must be at least the base and no more than 3600"
        )
    if not 1 <= settings.stripe_meter_max_attempts <= 100:
        errors.append("STRIPE_METER_MAX_ATTEMPTS must be between 1 and 100")
    if not 3_600 <= settings.stripe_meter_idempotency_window_seconds <= 82_800:
        errors.append(
            "STRIPE_METER_IDEMPOTENCY_WINDOW_SECONDS must be between 3600 and 82800"
        )
    maximum_retry_horizon = (
        max(0, settings.stripe_meter_max_attempts - 1)
        * settings.stripe_meter_retry_max_seconds
    )
    if maximum_retry_horizon >= settings.stripe_meter_idempotency_window_seconds:
        errors.append(
            "Stripe metering retry horizon must remain inside the idempotency safety window"
        )
    if not 86_400 <= settings.stripe_meter_max_event_age_seconds <= 2_937_600:
        errors.append(
            "STRIPE_METER_MAX_EVENT_AGE_SECONDS must be between one and 34 days"
        )
    if production and settings.stripe_api_key:
        if not settings.stripe_api_key.startswith(("sk_live_", "rk_live_")):
            errors.append(
                "Production STRIPE_API_KEY must be a live secret or restricted key"
            )
        if not settings.stripe_meter_async_error_destination_configured:
            errors.append(
                "STRIPE_METER_ASYNC_ERROR_DESTINATION_CONFIGURED must attest a "
                "durable Stripe thin-event destination"
            )
        if not settings.stripe_meter_worker_enabled:
            errors.append(
                "STRIPE_METER_WORKER_ENABLED must be true when STRIPE_API_KEY is configured"
            )
        if importlib.util.find_spec("stripe") is None:
            errors.append(
                "The Stripe SDK is required when STRIPE_API_KEY is configured; "
                "install lians-platform[billing]"
            )
    return errors


@dataclass(frozen=True)
class _ProviderEvent:
    id: UUID
    event_name: str
    customer_id: str
    quantity: int
    provider_identifier: str
    occurred_at: datetime
    attempt_number: int


@dataclass(frozen=True)
class _StripeResult:
    delivered: bool
    retryable: bool
    status_code: int | None
    error_code: str | None
    error_digest: str | None
    response_digest: str | None
    retry_after_seconds: float | None
    duration_ms: int


def _sha256(value: str | bytes) -> str:
    raw = value if isinstance(value, bytes) else value.encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def _canonical_hash(value: dict[str, Any]) -> str:
    return _sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    )


def _utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


async def _database_now(db: AsyncSession) -> datetime:
    """Use the database clock for leases, retries, and delivery state changes."""

    dialect = db.get_bind().dialect.name
    clock = func.clock_timestamp() if dialect == "postgresql" else func.current_timestamp()
    value = (await db.execute(select(clock))).scalar_one()
    if not isinstance(value, datetime):
        raise MeteringConfigurationError("The database did not return a valid UTC timestamp")
    return _utc(value)


def _validate_event_name(event_name: str) -> str:
    normalized = event_name.strip()
    if not _EVENT_NAME.fullmatch(normalized):
        raise MeteringConfigurationError(
            "Stripe meter event names must be 1-100 characters using letters, "
            "numbers, '.', '_', ':', or '-'"
        )
    return normalized


def _validate_customer_id(customer_id: str) -> str:
    normalized = customer_id.strip()
    if not normalized or len(normalized) > 255 or any(char.isspace() for char in normalized):
        raise MeteringConfigurationError(
            "Stripe customer IDs must be non-empty, whitespace-free, and at most 255 characters"
        )
    return normalized


def invalidate_customer_cache(namespace: str) -> None:
    """Compatibility no-op: durable metering deliberately performs no customer cache."""

    del namespace


async def get_customer_id(db: AsyncSession, namespace: str) -> str | None:
    """Read the current billing destination without a cross-replica stale cache."""

    from .models import NamespacePolicy

    policy = await db.get(NamespacePolicy, namespace)
    if policy is None or policy.stripe_customer_id is None:
        return None
    return _validate_customer_id(policy.stripe_customer_id)


async def enqueue_usage_event(
    db: AsyncSession,
    *,
    namespace: str,
    event_name: str,
    quantity: int,
    source_identifier: str,
    occurred_at: datetime | None = None,
) -> MeteringEvent | None:
    """Stage one usage fact in the caller's current database transaction.

    The function never commits.  Callers must await it before the same commit
    that makes the corresponding source mutation or durable recall audit
    authoritative.  A configured customer is snapshotted so later billing
    configuration changes cannot redirect already-earned usage.
    """

    settings = get_settings()
    if settings.airgap_mode:
        return None
    if not namespace or len(namespace) > 255:
        raise MeteringConfigurationError("A bounded namespace is required for metering")
    if not isinstance(quantity, int) or isinstance(quantity, bool) or quantity <= 0:
        raise MeteringConfigurationError("Meter quantities must be positive whole numbers")
    if quantity > 9_223_372_036_854_775_807:
        raise MeteringConfigurationError("Meter quantity exceeds signed BIGINT")
    if not source_identifier or len(source_identifier) > 2_048:
        raise MeteringConfigurationError(
            "A source identifier between 1 and 2048 characters is required"
        )
    # Validate the configured semantic dimension before looking up tenant
    # billing. A malformed deployment must not appear healthy merely because
    # the first request happened to belong to an unbilled namespace.
    normalized_event_name = _validate_event_name(event_name)

    now = await _database_now(db)
    event_occurred_at = _utc(occurred_at or now)
    if event_occurred_at > now + timedelta(minutes=5):
        raise MeteringConfigurationError(
            "Meter event timestamps cannot be more than five minutes in the future"
        )

    customer_id = await get_customer_id(db, namespace)
    if customer_id is None:
        return None
    source_hash = _sha256(source_identifier)
    provider_identifier = "lians_" + _canonical_hash(
        {
            "event_name": normalized_event_name,
            "namespace": namespace,
            "source_identifier": source_identifier,
        }
    )
    request_hash = _canonical_hash(
        {
            "customer_id": customer_id,
            "event_name": normalized_event_name,
            "namespace": namespace,
            "occurred_at": event_occurred_at.isoformat(timespec="microseconds"),
            "quantity": quantity,
            "source_identifier_hash": source_hash,
        }
    )

    existing = (
        await db.execute(
            select(MeteringEvent).where(
                MeteringEvent.namespace == namespace,
                MeteringEvent.event_name == normalized_event_name,
                MeteringEvent.source_identifier_hash == source_hash,
            )
        )
    ).scalar_one_or_none()
    if existing is not None:
        if existing.request_hash != request_hash:
            raise MeteringConflictError(
                "The billable source identifier is already bound to different usage facts"
            )
        return existing

    event = MeteringEvent(
        id=uuid.uuid4(),
        namespace=namespace,
        event_name=normalized_event_name,
        customer_id=customer_id,
        quantity=quantity,
        source_identifier_hash=source_hash,
        request_hash=request_hash,
        provider_identifier=provider_identifier,
        status="pending",
        attempt_limit=settings.stripe_meter_max_attempts,
        next_attempt_at=now,
        occurred_at=event_occurred_at,
        created_at=now,
        updated_at=now,
    )
    db.add(event)
    await db.flush()
    return event


async def enqueue_authoritative_decision_usage_event(
    db: AsyncSession,
    *,
    namespace: str,
    decision_id: UUID,
    occurred_at: datetime,
) -> MeteringEvent | None:
    """Stage exactly one product-native unit for an authoritative decision."""

    return await enqueue_usage_event(
        db,
        namespace=namespace,
        event_name=get_settings().stripe_meter_decision_event,
        quantity=1,
        source_identifier=f"decision:{decision_id}",
        occurred_at=occurred_at,
    )


async def enqueue_protected_action_usage_event(
    db: AsyncSession,
    *,
    namespace: str,
    permit_id: UUID,
    occurred_at: datetime,
) -> MeteringEvent | None:
    """Stage one unit only after a single-use Gate permit is consumed."""

    return await enqueue_usage_event(
        db,
        namespace=namespace,
        event_name=get_settings().stripe_meter_protected_action_event,
        quantity=1,
        source_identifier=f"gate-permit:{permit_id}",
        occurred_at=occurred_at,
    )


def queue_usage_event(
    event_name: str,
    customer_id: str,
    quantity: int,
    identifier: str,
) -> None:
    """Reject the retired process-local queue instead of silently losing money."""

    del event_name, customer_id, quantity, identifier
    settings = get_settings()
    if settings.airgap_mode:
        return
    raise MeteringConfigurationError(
        "queue_usage_event() was removed; use await enqueue_usage_event() inside "
        "the authoritative database transaction"
    )


def _backoff_seconds(event_id: UUID, attempt_number: int) -> float:
    settings = get_settings()
    ceiling = min(
        settings.stripe_meter_retry_max_seconds,
        settings.stripe_meter_retry_base_seconds * (2 ** max(0, attempt_number - 1)),
    )
    digest = hashlib.sha256(f"{event_id}:{attempt_number}".encode()).digest()
    jitter = 0.5 + int.from_bytes(digest[:4], "big") / (2**32) * 0.5
    return max(0.1, ceiling * jitter)


def _retry_after(headers: Any) -> float | None:
    if not headers:
        return None
    try:
        raw = headers.get("Retry-After") or headers.get("retry-after")
    except (AttributeError, TypeError):
        return None
    if raw is None:
        return None
    try:
        return max(0.0, float(raw))
    except (TypeError, ValueError):
        try:
            when = parsedate_to_datetime(str(raw))
            return max(0.0, (_utc(when) - datetime.now(UTC)).total_seconds())
        except (TypeError, ValueError, OverflowError):
            return None


def _error_code(exc: Exception, status_code: int | None) -> str:
    provider_code = getattr(exc, "code", None)
    raw = str(provider_code or type(exc).__name__)
    safe = _SAFE_CODE.sub("_", raw).strip("_")[:70] or "provider_error"
    return f"stripe_{status_code}_{safe}"[:100] if status_code else f"stripe_{safe}"[:100]


async def _send_to_stripe(
    stripe_module: Any,
    *,
    api_key: str,
    event: _ProviderEvent,
) -> _StripeResult:
    settings = get_settings()
    started = time.monotonic()
    try:
        async with asyncio.timeout(settings.stripe_meter_provider_timeout_seconds):
            response = await stripe_module.billing.MeterEvent.create_async(
                event_name=event.event_name,
                payload={
                    "stripe_customer_id": event.customer_id,
                    "value": str(event.quantity),
                },
                identifier=event.provider_identifier,
                timestamp=int(event.occurred_at.timestamp()),
                api_key=api_key,
                idempotency_key=event.provider_identifier,
            )
        last_response = getattr(response, "last_response", None)
        request_id = getattr(last_response, "request_id", None)
        response_id = getattr(response, "id", None) or getattr(response, "identifier", None)
        digest_material = f"{request_id or ''}:{response_id or event.provider_identifier}"
        duration_ms = max(0, int((time.monotonic() - started) * 1_000))
        return _StripeResult(
            delivered=True,
            retryable=False,
            status_code=int(getattr(last_response, "code", 200) or 200),
            error_code=None,
            error_digest=None,
            response_digest=_sha256(digest_material),
            retry_after_seconds=None,
            duration_ms=duration_ms,
        )
    except asyncio.CancelledError:
        raise
    except Exception as exc:  # noqa: BLE001 - normalize every Stripe SDK failure
        duration_ms = max(0, int((time.monotonic() - started) * 1_000))
        raw_status = getattr(exc, "http_status", None) or getattr(exc, "status_code", None)
        try:
            status_code = int(raw_status) if raw_status is not None else None
        except (TypeError, ValueError):
            status_code = None
        retryable = status_code is None or status_code in {408, 425, 429} or status_code >= 500
        code = _error_code(exc, status_code)
        return _StripeResult(
            delivered=False,
            retryable=retryable,
            status_code=status_code,
            error_code=code,
            error_digest=_sha256(f"{type(exc).__name__}:{status_code}:{getattr(exc, 'code', None)}"),
            response_digest=None,
            retry_after_seconds=_retry_after(getattr(exc, "headers", None)),
            duration_ms=duration_ms,
        )


async def claim_due_metering_events(
    db: AsyncSession,
    *,
    worker_id: str,
    batch_size: int,
    lease_seconds: int,
) -> list[UUID]:
    """Lease due events atomically; safe for concurrent workers and replicas."""

    now = await _database_now(db)
    eligible = or_(
        and_(
            MeteringEvent.status.in_(("pending", "retry")),
            MeteringEvent.next_attempt_at <= now,
        ),
        and_(
            MeteringEvent.status == "leased",
            MeteringEvent.lease_expires_at <= now,
        ),
    )
    rows = (
        await db.execute(
            select(MeteringEvent)
            .where(eligible)
            .order_by(MeteringEvent.next_attempt_at, MeteringEvent.created_at)
            .limit(batch_size)
            .with_for_update(skip_locked=db.get_bind().dialect.name == "postgresql")
        )
    ).scalars().all()
    expires_at = now + timedelta(seconds=lease_seconds)
    for row in rows:
        row.status = "leased"
        row.lease_owner = worker_id
        row.lease_expires_at = expires_at
        row.updated_at = now
    await db.commit()
    return [row.id for row in rows]


def _mark_dead_letter(row: MeteringEvent, *, now: datetime, code: str) -> None:
    row.status = "dead_letter"
    row.dead_lettered_at = now
    row.lease_owner = None
    row.lease_expires_at = None
    row.last_error_code = code
    row.last_error_digest = _sha256(code)
    row.updated_at = now


async def _prepare_provider_attempt(
    session_factory: async_sessionmaker[AsyncSession],
    *,
    event_id: UUID,
    worker_id: str,
) -> _ProviderEvent | None:
    settings = get_settings()
    async with session_factory() as db:
        row = (
            await db.execute(
                select(MeteringEvent)
                .where(MeteringEvent.id == event_id)
                .with_for_update()
            )
        ).scalar_one_or_none()
        if row is None or row.status != "leased" or row.lease_owner != worker_id:
            return None
        now = await _database_now(db)
        occurred_at = _utc(row.occurred_at)
        first_attempt_at = _utc(row.first_attempt_at) if row.first_attempt_at else None
        if row.attempt_count >= row.attempt_limit:
            _mark_dead_letter(row, now=now, code="attempt_limit_exhausted")
            await db.commit()
            return None
        if (now - occurred_at).total_seconds() > settings.stripe_meter_max_event_age_seconds:
            _mark_dead_letter(row, now=now, code="provider_event_age_exceeded")
            await db.commit()
            return None
        if first_attempt_at is not None and (
            now - first_attempt_at
        ).total_seconds() > settings.stripe_meter_idempotency_window_seconds:
            # Stripe only promises identifier de-duplication for at least 24h.
            # Past our safety window, human reconciliation is safer than a
            # possibly duplicated bill after an ambiguous accepted response.
            _mark_dead_letter(row, now=now, code="idempotency_window_expired")
            await db.commit()
            return None

        attempt_number = row.attempt_count + 1
        row.attempt_count = attempt_number
        row.first_attempt_at = row.first_attempt_at or now
        row.last_attempt_at = now
        # A row may have waited behind other claims from the same batch. Renew
        # immediately before the provider call so the lease always covers the
        # configured timeout plus its validated safety margin.
        row.lease_expires_at = now + timedelta(
            seconds=settings.stripe_meter_lease_seconds
        )
        row.updated_at = now
        db.add(
            MeteringAttemptRecord(
                id=uuid.uuid4(),
                namespace=row.namespace,
                event_id=row.id,
                attempt_number=attempt_number,
                record_type="started",
                outcome="started",
                worker_id=worker_id,
                recorded_at=now,
            )
        )
        provider_event = _ProviderEvent(
            id=row.id,
            event_name=row.event_name,
            customer_id=row.customer_id,
            quantity=int(row.quantity),
            provider_identifier=row.provider_identifier,
            occurred_at=occurred_at,
            attempt_number=attempt_number,
        )
        await db.commit()
        return provider_event


async def _record_provider_result(
    session_factory: async_sessionmaker[AsyncSession],
    *,
    event: _ProviderEvent,
    worker_id: str,
    result: _StripeResult,
) -> None:
    global _worker_last_delivery_at
    settings = get_settings()
    async with session_factory() as db:
        row = (
            await db.execute(
                select(MeteringEvent)
                .where(MeteringEvent.id == event.id)
                .with_for_update()
            )
        ).scalar_one_or_none()
        now = await _database_now(db)
        if row is None:
            return
        if row.status != "leased" or row.lease_owner != worker_id:
            db.add(
                MeteringAttemptRecord(
                    id=uuid.uuid4(),
                    namespace=row.namespace,
                    event_id=row.id,
                    attempt_number=event.attempt_number,
                    record_type="finished",
                    outcome="lease_lost",
                    worker_id=worker_id,
                    status_code=result.status_code,
                    error_code="lease_lost",
                    error_digest=_sha256("lease_lost"),
                    response_digest=result.response_digest,
                    duration_ms=result.duration_ms,
                    recorded_at=now,
                )
            )
            await db.commit()
            return

        next_attempt_at: datetime | None = None
        error_code = result.error_code
        error_digest = result.error_digest
        if result.delivered:
            outcome = "delivered"
            row.status = "delivered"
            row.delivered_at = now
            _worker_last_delivery_at = now
        else:
            first_attempt_at = _utc(row.first_attempt_at or now)
            idempotency_safe = (
                now - first_attempt_at
            ).total_seconds() <= settings.stripe_meter_idempotency_window_seconds
            if result.retryable and row.attempt_count < row.attempt_limit and idempotency_safe:
                outcome = "retry"
                delay = result.retry_after_seconds
                if delay is None:
                    delay = _backoff_seconds(row.id, row.attempt_count)
                delay = min(max(0.1, delay), settings.stripe_meter_retry_max_seconds)
                next_attempt_at = now + timedelta(seconds=delay)
                row.status = "retry"
                row.next_attempt_at = next_attempt_at
            else:
                outcome = "dead_letter"
                row.status = "dead_letter"
                row.dead_lettered_at = now
                if result.retryable and not idempotency_safe:
                    error_code = "idempotency_window_expired_after_failure"
                    error_digest = _sha256(error_code)

        row.lease_owner = None
        row.lease_expires_at = None
        row.last_status_code = result.status_code
        row.last_error_code = error_code
        row.last_error_digest = error_digest
        row.last_response_digest = result.response_digest
        row.updated_at = now
        db.add(
            MeteringAttemptRecord(
                id=uuid.uuid4(),
                namespace=row.namespace,
                event_id=row.id,
                attempt_number=event.attempt_number,
                record_type="finished",
                outcome=outcome,
                worker_id=worker_id,
                status_code=result.status_code,
                error_code=error_code,
                error_digest=error_digest,
                response_digest=result.response_digest,
                duration_ms=result.duration_ms,
                next_attempt_at=next_attempt_at,
                recorded_at=now,
            )
        )
        await db.commit()
        from .metrics import record_metering_attempt

        record_metering_attempt(outcome)


async def deliver_claimed_metering_event(
    session_factory: async_sessionmaker[AsyncSession],
    *,
    event_id: UUID,
    worker_id: str,
    stripe_module: Any,
    api_key: str,
) -> None:
    set_current_namespace("__admin__")
    set_current_barrier_group(None)
    try:
        event = await _prepare_provider_attempt(
            session_factory,
            event_id=event_id,
            worker_id=worker_id,
        )
        if event is None:
            return
        result = await _send_to_stripe(stripe_module, api_key=api_key, event=event)
        await _record_provider_result(
            session_factory,
            event=event,
            worker_id=worker_id,
            result=result,
        )
    finally:
        set_current_namespace(None)
        set_current_barrier_group(None)


async def _refresh_worker_inventory(db: AsyncSession) -> None:
    global _worker_backlog, _worker_oldest_due_at
    rows = (
        await db.execute(
            select(MeteringEvent.status, func.count(MeteringEvent.id)).group_by(
                MeteringEvent.status
            )
        )
    ).all()
    _worker_backlog = {str(status): int(count) for status, count in rows}
    _worker_oldest_due_at = (
        await db.execute(
            select(func.min(MeteringEvent.next_attempt_at)).where(
                MeteringEvent.status.in_(("pending", "retry", "leased"))
            )
        )
    ).scalar_one_or_none()
    from .metrics import set_metering_backlog

    set_metering_backlog(
        counts=_worker_backlog,
        oldest_due_at=_worker_oldest_due_at,
    )


async def run_metering_worker(
    session_factory: async_sessionmaker[AsyncSession],
    *,
    api_key: str,
) -> None:
    """Continuously claim and report usage; safe across multiple replicas."""

    global _worker_last_heartbeat_at, _worker_last_poll_at, _worker_terminal_error
    settings = get_settings()
    from .metrics import set_metering_worker_state

    if settings.airgap_mode or not settings.stripe_meter_worker_enabled or not api_key:
        _set_metering_worker_health(False)
        set_metering_worker_state(delivery_enabled=False, healthy=False)
        return
    _set_metering_worker_health(False)
    set_metering_worker_state(delivery_enabled=True, healthy=False)
    try:
        import stripe as stripe_module  # type: ignore[import-not-found]
    except ImportError as exc:
        _worker_terminal_error = "stripe_sdk_unavailable"
        set_metering_worker_state(delivery_enabled=True, healthy=False)
        logger.error(
            "Stripe SDK is required for configured durable metering",
            extra={"error_digest": _sha256(type(exc).__name__)[:16]},
        )
        return

    # Database retries are authoritative. Provider-library retries are disabled
    # so every application-level attempt has one durable start/result pair.
    stripe_module.api_key = api_key
    stripe_module.max_network_retries = 0
    worker_id = f"{socket.gethostname()}:{os.getpid()}:{uuid.uuid4().hex[:12]}"
    delivery_concurrency = settings.stripe_meter_delivery_concurrency
    # A lease starts at claim time. Never claim work that would sit behind an
    # in-process semaphore while its lease ages without making progress.
    claim_batch_size = min(
        settings.stripe_meter_worker_batch_size,
        delivery_concurrency,
    )
    semaphore = asyncio.Semaphore(delivery_concurrency)

    async def deliver_one(event_id: UUID) -> None:
        async with semaphore:
            try:
                await deliver_claimed_metering_event(
                    session_factory,
                    event_id=event_id,
                    worker_id=worker_id,
                    stripe_module=stripe_module,
                    api_key=api_key,
                )
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                logger.error(
                    "Durable metering delivery iteration failed",
                    extra={"error_digest": _sha256(type(exc).__name__)[:16]},
                )
                _record_worker_error(exc)

    async def deliver_batch(event_ids: list[UUID]) -> None:
        """Keep liveness observable while a bounded provider batch is in flight."""

        global _worker_last_heartbeat_at
        batch = asyncio.gather(*(deliver_one(event_id) for event_id in event_ids))
        heartbeat_seconds = min(
            5.0,
            max(1.0, settings.stripe_meter_worker_poll_seconds * 2),
        )
        try:
            while not batch.done():
                done, _ = await asyncio.wait({batch}, timeout=heartbeat_seconds)
                if batch in done:
                    break
                _worker_last_heartbeat_at = datetime.now(UTC)
                refresh_metering_process_metrics()
            await batch
        finally:
            if not batch.done():
                batch.cancel()
                await asyncio.gather(batch, return_exceptions=True)

    logger.info("Durable metering worker started")
    while True:
        try:
            set_current_namespace("__admin__")
            set_current_barrier_group(None)
            try:
                async with session_factory() as db:
                    claimed = await claim_due_metering_events(
                        db,
                        worker_id=worker_id,
                        batch_size=claim_batch_size,
                        lease_seconds=settings.stripe_meter_lease_seconds,
                    )
                    await _refresh_worker_inventory(db)
                    _worker_last_poll_at = datetime.now(UTC)
                    _worker_last_heartbeat_at = _worker_last_poll_at
                    _set_metering_worker_health(True)
            finally:
                set_current_namespace(None)
                set_current_barrier_group(None)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.error(
                "Durable metering claim failed",
                extra={"error_digest": _sha256(type(exc).__name__)[:16]},
            )
            _record_worker_error(exc)
            await asyncio.sleep(settings.stripe_meter_worker_poll_seconds)
            continue
        if claimed:
            await deliver_batch(claimed)
        else:
            await asyncio.sleep(settings.stripe_meter_worker_poll_seconds)


def _record_worker_error(exc: Exception) -> None:
    global _worker_last_error_at, _worker_last_error_digest
    _worker_last_error_at = datetime.now(UTC)
    _worker_last_error_digest = _sha256(type(exc).__name__)
    from .metrics import set_metering_worker_state

    settings = get_settings()
    _set_metering_worker_health(False)
    set_metering_worker_state(
        delivery_enabled=bool(
            settings.stripe_api_key
            and settings.stripe_meter_worker_enabled
            and not settings.airgap_mode
        ),
        healthy=False,
    )


def _set_metering_worker_health(healthy: bool) -> None:
    """Persist process health until a later successful database poll."""

    global _worker_last_iteration_healthy
    _worker_last_iteration_healthy = healthy


def metering_worker_status() -> tuple[bool, datetime | None]:
    settings = get_settings()
    last_heartbeat = _worker_last_heartbeat_at or _worker_last_poll_at
    if (
        not settings.stripe_api_key
        or not settings.stripe_meter_worker_enabled
        or settings.airgap_mode
        or _worker_terminal_error is not None
    ):
        return False, last_heartbeat
    threshold = max(30.0, settings.stripe_meter_worker_poll_seconds * 5)
    heartbeat_fresh = last_heartbeat is not None and (
        datetime.now(UTC) - last_heartbeat
    ).total_seconds() <= threshold
    healthy = _worker_last_iteration_healthy and heartbeat_fresh
    return healthy, last_heartbeat


def refresh_metering_process_metrics() -> None:
    """Refresh time-sensitive process gauges immediately before a scrape."""

    from .metrics import set_metering_backlog, set_metering_worker_state

    settings = get_settings()
    healthy, _ = metering_worker_status()
    delivery_enabled = bool(
        settings.stripe_api_key
        and settings.stripe_meter_worker_enabled
        and not settings.airgap_mode
    )
    set_metering_worker_state(
        delivery_enabled=delivery_enabled,
        healthy=healthy,
    )
    set_metering_backlog(
        counts=_worker_backlog,
        oldest_due_at=_worker_oldest_due_at,
    )


async def metering_inventory(
    db: AsyncSession,
    *,
    namespace: str | None = None,
) -> dict[str, Any]:
    settings = get_settings()
    filters = [MeteringEvent.namespace == namespace] if namespace else []
    counts = {
        str(status): int(count)
        for status, count in (
            await db.execute(
                select(MeteringEvent.status, func.count(MeteringEvent.id))
                .where(*filters)
                .group_by(MeteringEvent.status)
            )
        ).all()
    }
    due_filters = [*filters, MeteringEvent.status.in_(("pending", "retry", "leased"))]
    oldest_due_at = (
        await db.execute(
            select(func.min(MeteringEvent.next_attempt_at)).where(*due_filters)
        )
    ).scalar_one_or_none()
    healthy, _ = metering_worker_status()
    provider_configured = bool(settings.stripe_api_key)
    worker_enabled = bool(settings.stripe_meter_worker_enabled)
    return {
        "delivery_enabled": provider_configured and worker_enabled and not settings.airgap_mode,
        "worker_enabled": worker_enabled,
        "provider_configured": provider_configured,
        "async_error_destination_configured": bool(
            settings.stripe_meter_async_error_destination_configured
        ),
        "worker_healthy": healthy,
        "worker_last_poll_at": _worker_last_poll_at,
        "worker_last_heartbeat_at": _worker_last_heartbeat_at,
        "worker_last_delivery_at": _worker_last_delivery_at,
        "worker_last_error_at": _worker_last_error_at,
        "worker_last_error_digest": _worker_last_error_digest,
        "worker_terminal_error": _worker_terminal_error,
        "pending_events": counts.get("pending", 0),
        "leased_events": counts.get("leased", 0),
        "retry_events": counts.get("retry", 0),
        "delivered_events": counts.get("delivered", 0),
        "dead_letter_events": counts.get("dead_letter", 0),
        "oldest_due_at": oldest_due_at,
    }


async def list_metering_events(
    db: AsyncSession,
    *,
    status: str | None = None,
    namespace: str | None = None,
    limit: int = 100,
) -> list[MeteringEvent]:
    filters: list[Any] = []
    if status:
        filters.append(MeteringEvent.status == status)
    if namespace:
        filters.append(MeteringEvent.namespace == namespace)
    return list(
        (
            await db.execute(
                select(MeteringEvent)
                .where(*filters)
                .order_by(MeteringEvent.updated_at.desc(), MeteringEvent.id)
                .limit(min(max(1, limit), 500))
            )
        ).scalars()
    )


async def list_metering_events_page(
    db: AsyncSession,
    *,
    status: str | None = None,
    namespace: str | None = None,
    limit: int = 100,
    before_updated_at: datetime | None = None,
    before_id: UUID | None = None,
) -> tuple[int, list[MeteringEvent]]:
    """Return exact filtered cardinality plus a stable descending page."""

    filters: list[Any] = []
    if status:
        filters.append(MeteringEvent.status == status)
    if namespace:
        filters.append(MeteringEvent.namespace == namespace)
    total = int(
        (
            await db.execute(select(func.count(MeteringEvent.id)).where(*filters))
        ).scalar_one()
        or 0
    )
    page_filters = list(filters)
    if before_updated_at is not None and before_id is not None:
        page_filters.append(
            or_(
                MeteringEvent.updated_at < before_updated_at,
                and_(
                    MeteringEvent.updated_at == before_updated_at,
                    MeteringEvent.id < before_id,
                ),
            )
        )
    page_limit = min(max(1, limit), 500)
    rows = list(
        (
            await db.execute(
                select(MeteringEvent)
                .where(*page_filters)
                .order_by(MeteringEvent.updated_at.desc(), MeteringEvent.id.desc())
                .limit(page_limit + 1)
            )
        ).scalars()
    )
    return total, rows


async def replay_dead_letter_event(
    db: AsyncSession,
    event_id: UUID,
    *,
    reconciliation: Literal["provider_confirmed_not_accepted"],
) -> MeteringEvent:
    """Re-arm a dead letter only after Stripe confirms it was not accepted.

    The immutable provider identifier and append-only attempt ledger remain
    intact. Resetting ``first_attempt_at`` starts a new, explicitly reconciled
    idempotency-safety epoch; it must never happen after an ambiguous lookup.
    """

    if reconciliation != "provider_confirmed_not_accepted":
        raise MeteringConflictError(
            "Replay requires provider confirmation that the event was not accepted"
        )

    row = (
        await db.execute(
            select(MeteringEvent)
            .where(MeteringEvent.id == event_id)
            .with_for_update()
        )
    ).scalar_one_or_none()
    if row is None:
        raise LookupError("Metering event not found")
    if row.status != "dead_letter":
        raise MeteringConflictError("Only dead-letter metering events can be replayed")
    settings = get_settings()
    new_limit = row.attempt_limit + settings.stripe_meter_max_attempts
    if new_limit > 1_000:
        raise MeteringConflictError("Metering event has reached its lifetime attempt ceiling")
    now = await _database_now(db)
    row.status = "retry"
    row.attempt_limit = new_limit
    row.replay_count += 1
    row.next_attempt_at = now
    row.lease_owner = None
    row.lease_expires_at = None
    row.first_attempt_at = None
    row.last_attempt_at = None
    row.dead_lettered_at = None
    row.last_status_code = None
    row.last_error_code = None
    row.last_error_digest = None
    row.last_response_digest = None
    row.updated_at = now
    await db.flush()
    return row
