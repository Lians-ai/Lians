"""Durable, cross-worker recall-cache invalidation barriers.

Privacy erasure and reviewer-driven restoration change what recall is allowed to
return.  Their invalidation intent is therefore committed in the same database
transaction as the mutation.  Every recall checks for an unfinished intent
before trusting Redis, so an unavailable cache can reduce availability but can
never expose a stale pre-mutation result from another worker.
"""
from __future__ import annotations

import hashlib
from datetime import datetime, timezone
from typing import Iterable, Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from .cache import invalidate_agent
from .durable_jobs import enqueue_job
from .models import DurableJob


RECALL_INVALIDATION_JOB = "recall_cache.invalidate"


def _reference_hash(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def invalidation_reference(*parts: object) -> str:
    """Return a stable, non-secret operation reference."""
    return _reference_hash("\0".join(str(part) for part in parts))


async def queue_recall_invalidation(
    db: AsyncSession,
    namespace: str,
    agent_id: str,
    *,
    operation: str,
    operation_ref: str,
    memory_ids: Iterable[object] = (),
) -> DurableJob:
    """Persist an invalidation barrier in the caller's current transaction."""
    identity = "\0".join(
        [operation, operation_ref, agent_id]
        + sorted(str(memory_id) for memory_id in memory_ids)
    )
    return await enqueue_job(
        db,
        namespace=namespace,
        kind=RECALL_INVALIDATION_JOB,
        payload={
            "agent_id": agent_id,
            "operation": operation,
            "operation_ref": operation_ref,
        },
        dedupe_key=_reference_hash(identity),
        # Exhaustion must not silently reopen stale cache reads. A dead job is
        # still treated as an active barrier, and explicit retries can repair it.
        max_attempts=1_000_000,
    )


async def pending_recall_invalidations(
    db: AsyncSession,
    namespace: str,
    *,
    agent_id: Optional[str] = None,
    operation: Optional[str] = None,
    operation_ref: Optional[str] = None,
) -> list[DurableJob]:
    rows = list((await db.execute(
        select(DurableJob).where(
            DurableJob.namespace == namespace,
            DurableJob.kind == RECALL_INVALIDATION_JOB,
            DurableJob.status != "completed",
        )
    )).scalars().all())

    def matches(job: DurableJob) -> bool:
        payload = dict(job.payload or {})
        return (
            (agent_id is None or payload.get("agent_id") == agent_id)
            and (operation is None or payload.get("operation") == operation)
            and (
                operation_ref is None
                or payload.get("operation_ref") == operation_ref
            )
        )

    return [job for job in rows if matches(job)]


async def has_pending_recall_invalidation(
    db: AsyncSession,
    namespace: str,
    agent_id: str,
) -> bool:
    return bool(await pending_recall_invalidations(
        db, namespace, agent_id=agent_id,
    ))


async def flush_recall_invalidation(
    db: AsyncSession,
    job: DurableJob,
) -> None:
    """Bump Redis, then durably release one database-backed barrier."""
    if job.status == "completed":
        return
    payload = dict(job.payload or {})
    agent_id = str(payload["agent_id"])
    await invalidate_agent(job.namespace, agent_id, fail_closed=True)
    now = datetime.now(timezone.utc)
    job.status = "completed"
    job.completed_at = now
    job.updated_at = now
    job.lease_until = None
    job.leased_by = None
    job.last_error = None
    await db.commit()


async def flush_pending_recall_invalidations(
    db: AsyncSession,
    namespace: str,
    *,
    agent_id: Optional[str] = None,
    operation: Optional[str] = None,
    operation_ref: Optional[str] = None,
) -> int:
    jobs = await pending_recall_invalidations(
        db,
        namespace,
        agent_id=agent_id,
        operation=operation,
        operation_ref=operation_ref,
    )
    for job in jobs:
        await flush_recall_invalidation(db, job)
    return len(jobs)


async def handle_recall_invalidation_job(
    _db: AsyncSession,
    job: DurableJob,
) -> None:
    """Durable-worker handler; the worker marks the job complete afterward."""
    payload = dict(job.payload or {})
    await invalidate_agent(
        job.namespace,
        str(payload["agent_id"]),
        fail_closed=True,
    )
