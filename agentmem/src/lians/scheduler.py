"""
Background retention scheduler — runs prune_expired_content for every namespace
with an active content_ttl_days policy on a configurable interval.

Started as an asyncio.Task inside the FastAPI lifespan.  Cancelled on shutdown.
Legal-hold namespaces are excluded from the query so they are never pruned
automatically; manual pruning via the admin API also blocks them (409).
"""
from __future__ import annotations

import asyncio
import hashlib
import logging
from datetime import datetime, timezone

from sqlalchemy import select, text, update
from sqlalchemy.ext.asyncio import (
    AsyncConnection,
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
)

from .config import get_settings
from .db import set_current_barrier_group, set_current_namespace
from .metrics import (
    record_retention_cycle,
    record_retention_leadership,
    record_retention_pruned,
    set_retention_scheduler_state,
)
from .models import NamespacePolicy, RetentionSchedulerState

logger = logging.getLogger("lians.scheduler")

# Stable, product-specific signed bigint used only for PostgreSQL leader election.
_RETENTION_ADVISORY_LOCK_ID = 0x4C49414E5352544E
_RETENTION_PRUNE_BATCH_LIMIT = 500
_RETENTION_MAX_BATCHES_PER_NAMESPACE = 100
_RETENTION_CONTINUATION_DELAY_SECONDS = 1.0
_scheduler_enabled = False
_scheduler_healthy = False
_scheduler_interval_seconds = 0.0
_scheduler_last_heartbeat_at: datetime | None = None


def _tenant_log_ref(namespace: str) -> str:
    """Return a bounded, domain-separated tenant reference for operator logs."""

    digest = hashlib.sha256(
        b"lians/retention-tenant-log-ref/v1\0" + namespace.encode("utf-8")
    ).hexdigest()
    return f"tenant_{digest[:16]}"


def _eligible_namespace_statement(*, after: str | None, limit: int):
    stmt = select(NamespacePolicy.namespace).where(
        NamespacePolicy.content_ttl_days.is_not(None),
        NamespacePolicy.legal_hold.is_(False),
    )
    if after is not None:
        stmt = stmt.where(NamespacePolicy.namespace > after)
    return stmt.order_by(NamespacePolicy.namespace).limit(limit)


async def _eligible_namespace_page(
    session_factory: async_sessionmaker[AsyncSession],
    *,
    lock_connection: AsyncConnection | None,
    after: str | None,
    limit: int,
) -> list[str]:
    stmt = _eligible_namespace_statement(after=after, limit=limit)
    if lock_connection is not None:
        # set_config(..., true) is transaction-local, so establish the internal
        # enumeration context separately for every committed keyset page.
        await lock_connection.execute(
            text("SELECT set_config('app.current_namespace', '__admin__', true)")
        )
        rows = list((await lock_connection.execute(stmt)).scalars())
        await lock_connection.commit()
        return rows
    async with session_factory() as db:
        return list((await db.execute(stmt)).scalars())


async def _load_durable_cursor(
    session_factory: async_sessionmaker[AsyncSession],
    *,
    lock_connection: AsyncConnection | None,
) -> str | None:
    stmt = select(
        RetentionSchedulerState.id,
        RetentionSchedulerState.namespace_cursor,
    ).where(RetentionSchedulerState.id == 1)
    if lock_connection is not None:
        row = (await lock_connection.execute(stmt)).one_or_none()
        await lock_connection.commit()
    else:
        async with session_factory() as db:
            row = (await db.execute(stmt)).one_or_none()
            if row is None:
                # SQLite metadata-created development databases do not execute
                # Alembic seed DML. Production PostgreSQL always fails closed
                # on a missing migration-owned singleton instead.
                db.add(RetentionSchedulerState(id=1, sweep_generation=0))
                await db.commit()
                return None
    if row is None:
        raise RuntimeError("Retention scheduler durable cursor row is missing")
    return row.namespace_cursor


async def _persist_durable_cursor(
    session_factory: async_sessionmaker[AsyncSession],
    *,
    lock_connection: AsyncConnection | None,
    cursor: str | None,
    completed_sweep: bool,
) -> None:
    values: dict[str, object] = {
        "namespace_cursor": cursor,
        "updated_at": datetime.now(timezone.utc),
    }
    if completed_sweep:
        values["sweep_generation"] = RetentionSchedulerState.sweep_generation + 1
    stmt = (
        update(RetentionSchedulerState)
        .where(RetentionSchedulerState.id == 1)
        .values(**values)
    )
    if lock_connection is not None:
        result = await lock_connection.execute(stmt)
        await lock_connection.commit()
    else:
        async with session_factory() as db:
            result = await db.execute(stmt)
            await db.commit()
    if result.rowcount != 1:
        raise RuntimeError("Retention scheduler durable cursor update failed")


async def run_retention_scheduler(
    session_factory: async_sessionmaker[AsyncSession],
    interval_hours: float,
    engine: AsyncEngine | None = None,
) -> None:
    """
    Loop: sleep *interval_hours* then prune all qualifying namespaces.

    Cancelled cleanly by task.cancel() during lifespan shutdown.
    """
    global _scheduler_enabled, _scheduler_healthy
    global _scheduler_interval_seconds, _scheduler_last_heartbeat_at

    _scheduler_enabled = True
    _scheduler_healthy = True
    _scheduler_interval_seconds = max(0.0, interval_hours * 3600)
    _scheduler_last_heartbeat_at = datetime.now(timezone.utc)
    refresh_retention_process_metrics()
    logger.info("Retention scheduler started", extra={"interval_hours": interval_hours})
    try:
        while True:
            await asyncio.sleep(interval_hours * 3600)
            try:
                more_namespaces = await _run_prune_cycle(session_factory, engine)
                _scheduler_healthy = True
                _scheduler_last_heartbeat_at = datetime.now(timezone.utc)
                refresh_retention_process_metrics()
                # A hard per-cycle ceiling bounds work and memory. Continue a
                # large sweep promptly from the durable database cursor rather
                # than making later key ranges wait another full interval.
                while more_namespaces:
                    await asyncio.sleep(_RETENTION_CONTINUATION_DELAY_SECONDS)
                    more_namespaces = await _run_prune_cycle(session_factory, engine)
                    _scheduler_last_heartbeat_at = datetime.now(timezone.utc)
                    refresh_retention_process_metrics()
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                # One provider/DB incident must not permanently kill retention
                # enforcement until the pod happens to restart.
                record_retention_cycle("failed")
                _scheduler_healthy = False
                _scheduler_last_heartbeat_at = datetime.now(timezone.utc)
                refresh_retention_process_metrics()
                # Exception text and tracebacks may contain SQL parameters or
                # a tenant key. Health and metrics carry the actionable signal.
                logger.error(
                    "Retention prune cycle failed",
                    extra={"error_type": type(exc).__name__},
                )
    except asyncio.CancelledError:
        _scheduler_enabled = False
        _scheduler_healthy = False
        refresh_retention_process_metrics()
        logger.info("Retention scheduler stopped")
        raise


async def _run_prune_cycle(
    session_factory: async_sessionmaker[AsyncSession],
    engine: AsyncEngine | None = None,
) -> bool:
    """Prune one bounded, leader-elected keyset page sequence.

    Returns true when the hard per-cycle ceiling was reached and the next
    invocation should continue from the durable database cursor.
    """
    from .memory_service import prune_expired_content

    started_at = datetime.now(timezone.utc)
    total_pruned = 0
    errors = 0
    namespaces_scanned = 0
    resolved_engine = engine or session_factory.kw.get("bind")
    if not isinstance(resolved_engine, AsyncEngine):
        raise RuntimeError("Retention scheduler requires an AsyncEngine-bound session factory")

    settings = get_settings()
    page_size = settings.retention_namespace_page_size
    max_namespaces = settings.retention_max_namespaces_per_cycle

    lock_connection = None
    try:
        if resolved_engine.dialect.name == "postgresql":
            lock_connection = await resolved_engine.connect()
            acquired = bool(
                await lock_connection.scalar(
                    text("SELECT pg_try_advisory_lock(:lock_id)"),
                    {"lock_id": _RETENTION_ADVISORY_LOCK_ID},
                )
            )
            await lock_connection.commit()
            if not acquired:
                record_retention_leadership("contended")
                record_retention_cycle("skipped")
                logger.info("Retention prune skipped; another replica is leader")
                return False
            record_retention_leadership("acquired")
        else:
            record_retention_leadership("local")

        cursor = await _load_durable_cursor(
            session_factory,
            lock_connection=lock_connection,
        )
        more_namespaces = False
        while namespaces_scanned < max_namespaces:
            requested = min(page_size, max_namespaces - namespaces_scanned)
            namespaces = await _eligible_namespace_page(
                session_factory,
                lock_connection=lock_connection,
                after=cursor,
                limit=requested,
            )
            if not namespaces:
                # The previous page may have landed exactly on the final key.
                # Reset for the next scheduled sweep; never wrap inside the
                # same continuation chain and accidentally prune keys twice.
                await _persist_durable_cursor(
                    session_factory,
                    lock_connection=lock_connection,
                    cursor=None,
                    completed_sweep=True,
                )
                break

            for namespace in namespaces:
                try:
                    # Establish the exact tenant context before the first statement;
                    # the engine begin listener re-applies it after any mid-operation
                    # commit performed by the pruning service.
                    set_current_namespace(namespace)
                    set_current_barrier_group(None)
                    async with session_factory() as db:
                        namespace_pruned = 0
                        remaining = 0
                        for _batch in range(_RETENTION_MAX_BATCHES_PER_NAMESPACE):
                            pruned = await prune_expired_content(
                                db,
                                namespace,
                                batch_limit=_RETENTION_PRUNE_BATCH_LIMIT,
                            )
                            namespace_pruned += pruned.memories_pruned
                            remaining = pruned.remaining
                            if pruned.complete or pruned.memories_pruned == 0:
                                break
                        total_pruned += namespace_pruned
                        record_retention_pruned(namespace_pruned)
                        if namespace_pruned:
                            logger.info(
                                "Scheduler prune completed",
                                extra={
                                    "tenant_ref": _tenant_log_ref(namespace),
                                    "memories_pruned": namespace_pruned,
                                    "remaining": remaining,
                                    "cutoff_date": pruned.cutoff_date.isoformat(),
                                },
                            )
                except Exception as exc:
                    errors += 1
                    logger.error(
                        "Scheduler prune error",
                        extra={
                            "tenant_ref": _tenant_log_ref(namespace),
                            "error_type": type(exc).__name__,
                        },
                    )
                finally:
                    set_current_namespace(None)
                    set_current_barrier_group(None)
                    cursor = namespace
                    namespaces_scanned += 1

            if len(namespaces) < requested:
                await _persist_durable_cursor(
                    session_factory,
                    lock_connection=lock_connection,
                    cursor=None,
                    completed_sweep=True,
                )
                break
            await _persist_durable_cursor(
                session_factory,
                lock_connection=lock_connection,
                cursor=cursor,
                completed_sweep=False,
            )
        else:
            more_namespaces = True

        elapsed_ms = round(
            (datetime.now(timezone.utc) - started_at).total_seconds() * 1000,
            1,
        )
        logger.info(
            "Retention prune cycle done",
            extra={
                "namespaces_scanned": namespaces_scanned,
                "total_pruned": total_pruned,
                "errors": errors,
                "elapsed_ms": elapsed_ms,
                "cursor_pending": more_namespaces,
            },
        )
        record_retention_cycle("partial_failure" if errors else "completed")
        return more_namespaces
    finally:
        if lock_connection is not None:
            try:
                await lock_connection.execute(
                    text("SELECT pg_advisory_unlock(:lock_id)"),
                    {"lock_id": _RETENTION_ADVISORY_LOCK_ID},
                )
                await lock_connection.commit()
            finally:
                await lock_connection.close()


def refresh_retention_process_metrics() -> None:
    """Publish current per-replica scheduler posture immediately at scrape."""

    set_retention_scheduler_state(
        enabled=_scheduler_enabled,
        healthy=_scheduler_healthy,
        interval_seconds=_scheduler_interval_seconds,
        heartbeat_at=_scheduler_last_heartbeat_at,
    )


def retention_scheduler_status() -> tuple[bool, datetime | None]:
    """Return process-lifetime scheduler health without tenant details."""

    heartbeat = _scheduler_last_heartbeat_at
    if get_settings().retention_prune_interval_hours <= 0:
        return False, heartbeat
    # The first cycle intentionally begins after one configured interval. A
    # heartbeat remains fresh across that sleep plus a bounded execution grace.
    threshold = max(60.0, _scheduler_interval_seconds + 300.0)
    fresh = heartbeat is not None and (
        datetime.now(timezone.utc) - heartbeat
    ).total_seconds() <= threshold
    return _scheduler_enabled and _scheduler_healthy and fresh, heartbeat
