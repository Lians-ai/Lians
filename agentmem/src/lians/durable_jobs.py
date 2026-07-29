"""Database-backed leasing and retry primitives for reliable side effects."""

from __future__ import annotations

import asyncio
import logging
import socket
import uuid
from collections.abc import Awaitable, Callable, Iterable
from datetime import datetime, timedelta, timezone

from sqlalchemy import and_, or_, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from .db import set_current_barrier_group, set_current_namespace
from .degradation import record_degradation
from .models import DurableJob


logger = logging.getLogger("lians.jobs")
JobHandler = Callable[[AsyncSession, DurableJob], Awaitable[None]]


def _now() -> datetime:
    return datetime.now(timezone.utc)


async def enqueue_job(
    db: AsyncSession,
    *,
    namespace: str,
    kind: str,
    payload: dict,
    dedupe_key: str | None = None,
    max_attempts: int = 8,
    available_at: datetime | None = None,
) -> DurableJob:
    """Add a non-secret work item in the caller's current transaction."""
    if dedupe_key:
        existing = (
            await db.execute(
                select(DurableJob).where(
                    DurableJob.namespace == namespace,
                    DurableJob.kind == kind,
                    DurableJob.dedupe_key == dedupe_key,
                )
            )
        ).scalar_one_or_none()
        if existing is not None:
            return existing
    row = DurableJob(
        namespace=namespace,
        kind=kind,
        payload=dict(payload),
        dedupe_key=dedupe_key,
        max_attempts=max(1, max_attempts),
        available_at=available_at or _now(),
    )
    db.add(row)
    await db.flush()
    return row


async def lease_jobs(
    db: AsyncSession,
    *,
    worker_id: str,
    kinds: Iterable[str],
    limit: int = 25,
    lease_seconds: int = 60,
) -> list[DurableJob]:
    """Atomically claim available or expired jobs using SKIP LOCKED on Postgres."""
    now = _now()
    statement = (
        select(DurableJob)
        .where(
            DurableJob.kind.in_(tuple(kinds)),
            DurableJob.attempts < DurableJob.max_attempts,
            or_(
                and_(
                    DurableJob.status == "pending",
                    DurableJob.available_at <= now,
                ),
                and_(
                    DurableJob.status == "leased",
                    DurableJob.lease_until < now,
                ),
            ),
        )
        .order_by(DurableJob.available_at, DurableJob.created_at)
        .limit(max(1, min(limit, 250)))
    )
    if db.get_bind().dialect.name == "postgresql":
        statement = statement.with_for_update(skip_locked=True)
    jobs = list((await db.execute(statement)).scalars().all())
    lease_until = now + timedelta(seconds=max(10, lease_seconds))
    for job in jobs:
        job.status = "leased"
        job.leased_by = worker_id
        job.lease_until = lease_until
        job.attempts += 1
        job.updated_at = now
    await db.commit()
    return jobs


async def complete_job(db: AsyncSession, job_id: uuid.UUID, worker_id: str) -> bool:
    row = await db.get(DurableJob, job_id)
    if row is None or row.status != "leased" or row.leased_by != worker_id:
        return False
    now = _now()
    row.status = "completed"
    row.completed_at = now
    row.updated_at = now
    row.lease_until = None
    row.leased_by = None
    row.last_error = None
    await db.commit()
    return True


async def fail_job(
    db: AsyncSession,
    job_id: uuid.UUID,
    worker_id: str,
    *,
    reason: str,
) -> bool:
    row = await db.get(DurableJob, job_id)
    if row is None or row.status != "leased" or row.leased_by != worker_id:
        return False
    now = _now()
    exhausted = row.attempts >= row.max_attempts
    row.status = "dead" if exhausted else "pending"
    row.available_at = now + timedelta(seconds=min(3600, 2 ** min(row.attempts, 10)))
    row.updated_at = now
    row.lease_until = None
    row.leased_by = None
    row.last_error = reason[:500]
    await db.commit()
    if exhausted:
        record_degradation("durable_jobs", f"{row.kind}_dead_letter")
    return True


async def drain_jobs_once(
    session_factory: async_sessionmaker[AsyncSession],
    handlers: dict[str, JobHandler],
    *,
    worker_id: str,
    limit: int = 25,
) -> int:
    """Lease and dispatch one bounded batch."""
    set_current_namespace("__admin__")
    set_current_barrier_group(None)
    try:
        async with session_factory() as lease_db:
            jobs = await lease_jobs(
                lease_db,
                worker_id=worker_id,
                kinds=handlers,
                limit=limit,
            )
        for job in jobs:
            try:
                async with session_factory() as handler_db:
                    await handlers[job.kind](handler_db, job)
                async with session_factory() as result_db:
                    await complete_job(result_db, job.id, worker_id)
            except Exception as exc:
                logger.exception(
                    "Durable job failed",
                    extra={"job_id": str(job.id), "kind": job.kind},
                )
                async with session_factory() as result_db:
                    await fail_job(
                        result_db,
                        job.id,
                        worker_id,
                        reason=type(exc).__name__,
                    )
        return len(jobs)
    finally:
        set_current_namespace(None)
        set_current_barrier_group(None)


async def run_durable_job_worker(
    session_factory: async_sessionmaker[AsyncSession],
    handlers: dict[str, JobHandler],
    *,
    poll_seconds: float = 1.0,
    worker_id: str | None = None,
) -> None:
    """Continuously drain jobs. Safe to run in multiple processes."""
    identity = worker_id or f"{socket.gethostname()}:{uuid.uuid4().hex[:12]}"
    logger.info("Durable job worker started", extra={"worker_id": identity})
    try:
        while True:
            count = await drain_jobs_once(
                session_factory,
                handlers,
                worker_id=identity,
            )
            if count == 0:
                await asyncio.sleep(max(0.1, poll_seconds))
    except asyncio.CancelledError:
        logger.info("Durable job worker stopped", extra={"worker_id": identity})
        raise
