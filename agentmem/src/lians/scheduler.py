"""
Background retention scheduler - runs prune_expired_content for every namespace
with an active content_ttl_days policy on a configurable interval.

Started as an asyncio.Task inside the FastAPI lifespan.  Cancelled on shutdown.
Legal-hold namespaces are excluded from the query so they are never pruned
automatically; manual pruning via the admin API also blocks them (409).
"""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from .models import MemoryFeedback, NamespacePolicy

logger = logging.getLogger("agentmem.scheduler")


async def _try_scheduler_lock(db: AsyncSession, name: str) -> bool:
    """Acquire a process-independent scheduler lock for the current connection."""
    if db.get_bind().dialect.name != "postgresql":
        return True
    return bool(
        (
            await db.execute(
                text("SELECT pg_try_advisory_lock(hashtext(:name))"),
                {"name": name},
            )
        ).scalar_one()
    )


async def _release_scheduler_lock(db: AsyncSession, name: str) -> None:
    if db.get_bind().dialect.name == "postgresql":
        await db.execute(
            text("SELECT pg_advisory_unlock(hashtext(:name))"),
            {"name": name},
        )


async def run_retention_scheduler(
    session_factory: async_sessionmaker[AsyncSession],
    interval_hours: float,
) -> None:
    """
    Prune all qualifying namespaces immediately, then sleep *interval_hours*.

    Cancelled cleanly by task.cancel() during lifespan shutdown.
    """
    logger.info("Retention scheduler started", extra={"interval_hours": interval_hours})
    try:
        while True:
            try:
                await _run_prune_cycle(session_factory)
            except Exception:
                # A transient database/Redis failure must not silently disable
                # retention until the next process restart. Namespace-specific
                # failures are handled inside the cycle; this boundary keeps the
                # scheduler alive when discovery or lock acquisition fails.
                logger.exception("Retention prune cycle failed; retrying next interval")
            await asyncio.sleep(interval_hours * 3600)
    except asyncio.CancelledError:
        logger.info("Retention scheduler stopped")
        raise


async def _run_prune_cycle(session_factory: async_sessionmaker[AsyncSession]) -> None:
    """Prune one cycle across all namespaces with an active content TTL."""
    from .db import current_barrier_group, current_namespace
    from .memory_service import prune_expired_content

    started_at = datetime.now(timezone.utc)
    total_pruned = 0
    errors = 0

    async def prune_namespaces(namespaces: list[str]) -> None:
        nonlocal total_pruned, errors
        for namespace in namespaces:
            namespace_token = current_namespace.set(namespace)
            barrier_token = current_barrier_group.set(None)
            try:
                async with session_factory() as db:
                    pruned = await prune_expired_content(db, namespace)
                    total_pruned += pruned.memories_pruned
                    if pruned.memories_pruned:
                        logger.info(
                            "Scheduler prune completed",
                            extra={
                                "namespace": namespace,
                                "memories_pruned": pruned.memories_pruned,
                                "cutoff_date": pruned.cutoff_date.isoformat(),
                            },
                        )
            except Exception as exc:
                errors += 1
                logger.error(
                    "Scheduler prune error",
                    extra={"namespace": namespace, "error": str(exc)},
                )
            finally:
                current_barrier_group.reset(barrier_token)
                current_namespace.reset(namespace_token)

    stmt = select(NamespacePolicy).where(
        NamespacePolicy.content_ttl_days.is_not(None),
        NamespacePolicy.legal_hold.is_(False),
    )
    async with session_factory() as probe_db:
        is_postgres = probe_db.get_bind().dialect.name == "postgresql"

    if not is_postgres:
        admin_namespace_token = current_namespace.set("__admin__")
        admin_barrier_token = current_barrier_group.set(None)
        try:
            async with session_factory() as db:
                result = await db.execute(stmt)
                namespaces = [p.namespace for p in result.scalars().all()]
        finally:
            current_barrier_group.reset(admin_barrier_token)
            current_namespace.reset(admin_namespace_token)
        await prune_namespaces(namespaces)
    else:
        lock_name = "lians:retention"
        async with session_factory() as lock_db:
            lock_acquired = False
            try:
                admin_namespace_token = current_namespace.set("__admin__")
                admin_barrier_token = current_barrier_group.set(None)
                try:
                    lock_acquired = await _try_scheduler_lock(lock_db, lock_name)
                    if not lock_acquired:
                        logger.info(
                            "Retention cycle skipped; another instance owns the lock"
                        )
                        return
                    result = await lock_db.execute(stmt)
                    namespaces = [p.namespace for p in result.scalars().all()]
                finally:
                    current_barrier_group.reset(admin_barrier_token)
                    current_namespace.reset(admin_namespace_token)
                await prune_namespaces(namespaces)
            finally:
                if lock_acquired:
                    await _release_scheduler_lock(lock_db, lock_name)

    elapsed_ms = round((datetime.now(timezone.utc) - started_at).total_seconds() * 1000, 1)
    logger.info(
        "Retention prune cycle done",
        extra={
            "namespaces_scanned": len(namespaces),
            "total_pruned": total_pruned,
            "errors": errors,
            "elapsed_ms": elapsed_ms,
        },
    )


async def run_learning_maintenance_scheduler(
    session_factory: async_sessionmaker[AsyncSession],
    interval_hours: float,
    min_signals: int = 3,
) -> None:
    """Periodically apply bounded outcome-driven decay across namespaces."""
    logger.info("Learning maintenance scheduler started")
    try:
        while True:
            await asyncio.sleep(interval_hours * 3600)
            async def maintain(namespaces: list[str]) -> None:
                from .feedback_service import run_memory_maintenance

                for namespace in namespaces:
                    try:
                        async with session_factory() as db:
                            result = await run_memory_maintenance(
                                db, namespace, min_signals=min_signals,
                            )
                        logger.info(
                            "Learning maintenance completed",
                            extra={
                                "namespace": namespace,
                                "demoted": result.memories_demoted,
                                "candidates": result.consolidation_candidates,
                            },
                        )
                    except Exception as exc:
                        logger.error(
                            "Learning maintenance error",
                            extra={"namespace": namespace, "error": str(exc)},
                        )

            namespace_stmt = select(MemoryFeedback.namespace).distinct()
            async with session_factory() as probe_db:
                is_postgres = probe_db.get_bind().dialect.name == "postgresql"
            if not is_postgres:
                async with session_factory() as db:
                    namespaces = list(
                        (await db.execute(namespace_stmt)).scalars().all()
                    )
                await maintain(namespaces)
                continue

            lock_name = "lians:learning-maintenance"
            async with session_factory() as lock_db:
                if not await _try_scheduler_lock(lock_db, lock_name):
                    logger.info(
                        "Learning maintenance skipped; another instance owns the lock"
                    )
                    continue
                try:
                    namespaces = list(
                        (await lock_db.execute(namespace_stmt)).scalars().all()
                    )
                    await maintain(namespaces)
                finally:
                    await _release_scheduler_lock(lock_db, lock_name)
    except asyncio.CancelledError:
        logger.info("Learning maintenance scheduler stopped")
        raise
