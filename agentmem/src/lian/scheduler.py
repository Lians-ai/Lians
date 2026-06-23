"""
Background retention scheduler — runs prune_expired_content for every namespace
with an active content_ttl_days policy on a configurable interval.

Started as an asyncio.Task inside the FastAPI lifespan.  Cancelled on shutdown.
Legal-hold namespaces are excluded from the query so they are never pruned
automatically; manual pruning via the admin API also blocks them (409).
"""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from .models import NamespacePolicy

logger = logging.getLogger("agentmem.scheduler")


async def run_retention_scheduler(
    session_factory: async_sessionmaker[AsyncSession],
    interval_hours: float,
) -> None:
    """
    Loop: sleep *interval_hours* then prune all qualifying namespaces.

    Cancelled cleanly by task.cancel() during lifespan shutdown.
    """
    logger.info("Retention scheduler started", extra={"interval_hours": interval_hours})
    try:
        while True:
            await asyncio.sleep(interval_hours * 3600)
            await _run_prune_cycle(session_factory)
    except asyncio.CancelledError:
        logger.info("Retention scheduler stopped")
        raise


async def _run_prune_cycle(session_factory: async_sessionmaker[AsyncSession]) -> None:
    """Prune one cycle across all namespaces with an active content TTL."""
    from .memory_service import prune_expired_content

    started_at = datetime.now(timezone.utc)
    total_pruned = 0
    errors = 0

    async with session_factory() as db:
        stmt = select(NamespacePolicy).where(
            NamespacePolicy.content_ttl_days.is_not(None),
            NamespacePolicy.legal_hold.is_(False),
        )
        result = await db.execute(stmt)
        namespaces = [p.namespace for p in result.scalars().all()]

    for namespace in namespaces:
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
