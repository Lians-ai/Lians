"""Durable fixed-snapshot indexing for Recorder events predating decisions."""

from __future__ import annotations

import asyncio
import hashlib
import logging
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from uuid import UUID

from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from .audit_chain import chain_log
from .config import Settings, get_settings
from .db import set_current_barrier_group, set_current_namespace
from .models import DecisionRecord
from .recorder_models import RecorderEvent, RecorderEvidenceIndexJob
from .recorder_service import (
    RecorderIntegrityError,
    _acquire_decision_recorder_fence,
    _decision_recorder_filters,
    _mark_recorder_job_coverage,
    index_recorder_rows_batch,
)

logger = logging.getLogger("lians.recorder_index_worker")


class RecorderIndexSnapshotInvariantError(RuntimeError):
    """A frozen Recorder event snapshot cannot be traversed exactly."""


class RecorderIndexLeaseConflict(RuntimeError):
    """A Recorder indexing job is no longer owned by this worker."""


@dataclass(frozen=True, slots=True)
class ClaimedRecorderIndexJob:
    job_id: UUID
    namespace: str
    barrier_group: str | None


_worker_last_poll_at: datetime | None = None
_worker_last_heartbeat_at: datetime | None = None
_worker_last_iteration_healthy = False


def _utc(value: datetime) -> datetime:
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


async def _database_now(db: AsyncSession) -> datetime:
    return _utc((await db.execute(select(func.now()))).scalar_one())


def _error_digest(exc: BaseException) -> str:
    identity = f"{type(exc).__module__}.{type(exc).__qualname__}"
    return hashlib.sha256(identity.encode()).hexdigest()


def _terminal_content_hash(job: RecorderEvidenceIndexJob, state: str) -> str:
    value = (
        f"{job.id}:{job.decision_id}:{state}:{job.snapshot_event_count}:"
        f"{job.events_indexed}:{job.cursor_event_id}"
    )
    return hashlib.sha256(value.encode()).hexdigest()


def _classify_error(exc: BaseException) -> tuple[str, bool]:
    if isinstance(exc, RecorderIntegrityError):
        return "recorder_integrity", True
    if isinstance(exc, RecorderIndexSnapshotInvariantError):
        return "snapshot_invariant", True
    if isinstance(exc, RecorderIndexLeaseConflict):
        return "lease_lost", False
    if isinstance(exc, TimeoutError):
        return "timeout", False
    return "processing_error", False


def _backoff_seconds(job_id: UUID, failure_number: int) -> float:
    settings = get_settings()
    ceiling = min(
        settings.recorder_evidence_index_worker_retry_max_seconds,
        settings.recorder_evidence_index_worker_retry_base_seconds
        * (2 ** max(0, failure_number - 1)),
    )
    digest = hashlib.sha256(f"{job_id}:{failure_number}".encode()).digest()
    jitter = 0.5 + int.from_bytes(digest[:4], "big") / (2**32) * 0.5
    return max(0.1, ceiling * jitter)


def validate_recorder_index_worker_configuration(
    settings: Settings,
    *,
    production: bool,
) -> list[str]:
    errors: list[str] = []
    if production and not settings.recorder_evidence_index_worker_enabled:
        errors.append("RECORDER_EVIDENCE_INDEX_WORKER_ENABLED must be true in production")
    if not 0.05 <= settings.recorder_evidence_index_worker_poll_seconds <= 60:
        errors.append(
            "RECORDER_EVIDENCE_INDEX_WORKER_POLL_SECONDS must be between 0.05 and 60"
        )
    if not 1 <= settings.recorder_evidence_index_worker_batch_size <= 100:
        errors.append(
            "RECORDER_EVIDENCE_INDEX_WORKER_BATCH_SIZE must be between 1 and 100"
        )
    if not 1 <= settings.recorder_evidence_index_worker_concurrency <= 32:
        errors.append(
            "RECORDER_EVIDENCE_INDEX_WORKER_CONCURRENCY must be between 1 and 32"
        )
    minimum_lease = settings.database_statement_timeout_ms / 1000 + 15
    if not minimum_lease <= settings.recorder_evidence_index_worker_lease_seconds <= 3600:
        errors.append(
            "RECORDER_EVIDENCE_INDEX_WORKER_LEASE_SECONDS must exceed the database "
            "statement timeout by at least 15 seconds and be no more than 3600"
        )
    if not 1 <= settings.recorder_evidence_index_worker_page_size <= 100:
        errors.append(
            "RECORDER_EVIDENCE_INDEX_WORKER_PAGE_SIZE must be between 1 and 100"
        )
    if not 1 <= settings.recorder_evidence_index_worker_max_pages_per_claim <= 20:
        errors.append(
            "RECORDER_EVIDENCE_INDEX_WORKER_MAX_PAGES_PER_CLAIM must be between 1 and 20"
        )
    if settings.recorder_evidence_index_worker_retry_base_seconds <= 0:
        errors.append(
            "RECORDER_EVIDENCE_INDEX_WORKER_RETRY_BASE_SECONDS must be positive"
        )
    if not (
        settings.recorder_evidence_index_worker_retry_base_seconds
        <= settings.recorder_evidence_index_worker_retry_max_seconds
        <= 3600
    ):
        errors.append(
            "RECORDER_EVIDENCE_INDEX_WORKER_RETRY_MAX_SECONDS must be at least the "
            "base and no more than 3600"
        )
    if not 1 <= settings.recorder_evidence_index_worker_max_attempts <= 100:
        errors.append(
            "RECORDER_EVIDENCE_INDEX_WORKER_MAX_ATTEMPTS must be between 1 and 100"
        )
    return errors


async def claim_due_recorder_index_jobs(
    db: AsyncSession,
    *,
    worker_id: str,
    batch_size: int,
    lease_seconds: int,
) -> list[ClaimedRecorderIndexJob]:
    """Claim due work globally under the internal admin tenant context."""

    now = await _database_now(db)
    statement = (
        select(RecorderEvidenceIndexJob)
        .where(
            RecorderEvidenceIndexJob.status.in_(("pending", "running")),
            RecorderEvidenceIndexJob.next_attempt_at <= now,
            or_(
                RecorderEvidenceIndexJob.lease_owner.is_(None),
                RecorderEvidenceIndexJob.lease_expires_at <= now,
            ),
        )
        .order_by(
            RecorderEvidenceIndexJob.next_attempt_at,
            RecorderEvidenceIndexJob.created_at,
            RecorderEvidenceIndexJob.id,
        )
        .limit(max(1, min(100, batch_size)))
        .with_for_update(skip_locked=db.get_bind().dialect.name == "postgresql")
    )
    rows = list((await db.execute(statement)).scalars())
    claims: list[ClaimedRecorderIndexJob] = []
    for row in rows:
        expired = bool(
            row.lease_owner is not None
            and row.lease_expires_at is not None
            and _utc(row.lease_expires_at) <= now
        )
        if expired:
            row.consecutive_failures += 1
            row.last_error_code = "lease_expired"
            row.last_error_digest = hashlib.sha256(b"lease_expired").hexdigest()
            if row.consecutive_failures >= row.attempt_limit:
                row.status = "failed"
                row.failure_code = "worker_attempt_limit_exhausted"
                row.failed_at = now
                row.lease_owner = None
                row.lease_expires_at = None
                decision = await db.get(DecisionRecord, row.decision_id)
                if decision is None or decision.namespace != row.namespace:
                    raise RecorderIndexSnapshotInvariantError(
                        "Recorder indexing job references a missing decision"
                    )
                await _mark_recorder_job_coverage(
                    db, decision=decision, job=row, state="failed"
                )
                await chain_log(
                    db,
                    row.namespace,
                    row.queued_by_principal_ref,
                    "recorder_evidence_index_failed",
                    content_hash=_terminal_content_hash(row, "failed"),
                    payload={
                        "job_id": str(row.id),
                        "decision_id": str(row.decision_id),
                        "failure_code": row.failure_code,
                        "events_indexed": int(row.events_indexed),
                        "snapshot_event_count": int(row.snapshot_event_count),
                    },
                )
                continue
        row.status = "running"
        row.processing_attempts += 1
        row.last_attempt_at = now
        row.heartbeat_at = now
        row.lease_owner = worker_id
        row.lease_expires_at = now + timedelta(seconds=lease_seconds)
        row.updated_at = now
        if row.started_at is None:
            row.started_at = now
        claims.append(
            ClaimedRecorderIndexJob(
                job_id=row.id,
                namespace=row.namespace,
                barrier_group=row.barrier_group,
            )
        )
    await db.commit()
    return claims


def _snapshot_filter(job: RecorderEvidenceIndexJob):
    return or_(
        RecorderEvent.recorded_at < job.snapshot_max_recorded_at,
        (
            RecorderEvent.recorded_at == job.snapshot_max_recorded_at
        )
        & (RecorderEvent.id <= job.snapshot_max_event_id),
    )


def _cursor_filter(job: RecorderEvidenceIndexJob):
    if job.cursor_recorded_at is None or job.cursor_event_id is None:
        return None
    return or_(
        RecorderEvent.recorded_at > job.cursor_recorded_at,
        (RecorderEvent.recorded_at == job.cursor_recorded_at)
        & (RecorderEvent.id > job.cursor_event_id),
    )


async def _advance_one_page(
    db: AsyncSession,
    *,
    claim: ClaimedRecorderIndexJob,
    worker_id: str,
    page_size: int,
    lease_seconds: int,
) -> bool:
    job = (
        await db.execute(
            select(RecorderEvidenceIndexJob)
            .where(
                RecorderEvidenceIndexJob.id == claim.job_id,
                RecorderEvidenceIndexJob.namespace == claim.namespace,
            )
            .with_for_update()
        )
    ).scalar_one_or_none()
    if (
        job is None
        or job.status != "running"
        or job.lease_owner != worker_id
        or job.lease_expires_at is None
    ):
        raise RecorderIndexLeaseConflict("Recorder indexing lease was lost")
    now = await _database_now(db)
    if _utc(job.lease_expires_at) <= now:
        raise RecorderIndexLeaseConflict("Recorder indexing lease expired")
    decision = (
        await db.execute(
            select(DecisionRecord).where(
                DecisionRecord.id == job.decision_id,
                DecisionRecord.namespace == job.namespace,
            )
        )
    ).scalar_one_or_none()
    if decision is None or decision.barrier_group != job.barrier_group:
        raise RecorderIndexSnapshotInvariantError(
            "Recorder indexing decision identity is unavailable"
        )
    await _acquire_decision_recorder_fence(
        db,
        namespace=job.namespace,
        decision_id=job.decision_id,
    )
    filters = [*_decision_recorder_filters(decision), _snapshot_filter(job)]
    cursor = _cursor_filter(job)
    if cursor is not None:
        filters.append(cursor)
    rows = list(
        (
            await db.execute(
                select(RecorderEvent)
                .where(*filters)
                .order_by(RecorderEvent.recorded_at, RecorderEvent.id)
                .limit(page_size)
            )
        ).scalars()
    )
    if not rows:
        raise RecorderIndexSnapshotInvariantError(
            "Recorder indexing snapshot ended before its exact count"
        )
    artifacts_created, links_created = await index_recorder_rows_batch(
        db,
        decision=decision,
        rows=rows,
    )
    job.events_indexed += len(rows)
    job.artifacts_created += artifacts_created
    job.links_created += links_created
    job.pages_completed += 1
    job.cursor_recorded_at = rows[-1].recorded_at
    job.cursor_event_id = rows[-1].id
    job.heartbeat_at = now
    job.updated_at = now
    job.consecutive_failures = 0
    job.last_error_code = None
    job.last_error_digest = None
    if job.events_indexed > job.snapshot_event_count:
        raise RecorderIndexSnapshotInvariantError(
            "Recorder indexing exceeded its exact snapshot count"
        )
    completed = job.events_indexed == job.snapshot_event_count
    if completed:
        if (
            _utc(job.cursor_recorded_at) != _utc(job.snapshot_max_recorded_at)
            or job.cursor_event_id != job.snapshot_max_event_id
        ):
            raise RecorderIndexSnapshotInvariantError(
                "Recorder indexing terminal cursor differs from its frozen boundary"
            )
        job.status = "completed"
        job.completed_at = now
        job.lease_owner = None
        job.lease_expires_at = None
        await _mark_recorder_job_coverage(
            db, decision=decision, job=job, state="completed"
        )
        await chain_log(
            db,
            job.namespace,
            job.queued_by_principal_ref,
            "recorder_evidence_index_completed",
            content_hash=_terminal_content_hash(job, "completed"),
            payload={
                "job_id": str(job.id),
                "decision_id": str(job.decision_id),
                "events_indexed": int(job.events_indexed),
                "snapshot_event_count": int(job.snapshot_event_count),
            },
        )
    else:
        job.lease_expires_at = now + timedelta(seconds=lease_seconds)
    await db.commit()
    return completed


async def _record_processing_failure(
    session_factory: async_sessionmaker[AsyncSession],
    *,
    claim: ClaimedRecorderIndexJob,
    worker_id: str,
    exc: BaseException,
) -> None:
    code, inherently_terminal = _classify_error(exc)
    if code == "lease_lost":
        return
    set_current_namespace(claim.namespace)
    set_current_barrier_group(claim.barrier_group)
    async with session_factory() as db:
        job = (
            await db.execute(
                select(RecorderEvidenceIndexJob)
                .where(
                    RecorderEvidenceIndexJob.id == claim.job_id,
                    RecorderEvidenceIndexJob.namespace == claim.namespace,
                )
                .with_for_update()
            )
        ).scalar_one_or_none()
        if job is None or job.status not in {"pending", "running"}:
            return
        if job.lease_owner not in {None, worker_id}:
            return
        now = await _database_now(db)
        job.consecutive_failures += 1
        job.last_error_code = code
        job.last_error_digest = _error_digest(exc)
        terminal = inherently_terminal or job.consecutive_failures >= job.attempt_limit
        job.lease_owner = None
        job.lease_expires_at = None
        job.heartbeat_at = now
        job.updated_at = now
        if terminal:
            job.status = "failed"
            job.failure_code = (
                code if inherently_terminal else "worker_attempt_limit_exhausted"
            )
            job.failed_at = now
            decision = await db.get(DecisionRecord, job.decision_id)
            if decision is not None and decision.namespace == job.namespace:
                await _mark_recorder_job_coverage(
                    db, decision=decision, job=job, state="failed"
                )
                await chain_log(
                    db,
                    job.namespace,
                    job.queued_by_principal_ref,
                    "recorder_evidence_index_failed",
                    content_hash=_terminal_content_hash(job, "failed"),
                    payload={
                        "job_id": str(job.id),
                        "decision_id": str(job.decision_id),
                        "failure_code": job.failure_code,
                        "events_indexed": int(job.events_indexed),
                        "snapshot_event_count": int(job.snapshot_event_count),
                    },
                )
        else:
            job.status = "pending"
            job.next_attempt_at = now + timedelta(
                seconds=_backoff_seconds(job.id, job.consecutive_failures)
            )
        await db.commit()


async def process_recorder_index_job(
    session_factory: async_sessionmaker[AsyncSession],
    *,
    claim: ClaimedRecorderIndexJob,
    worker_id: str,
    page_size: int,
    max_pages: int,
    lease_seconds: int,
) -> None:
    set_current_namespace(claim.namespace)
    set_current_barrier_group(claim.barrier_group)
    try:
        for _ in range(max_pages):
            async with session_factory() as db:
                if await _advance_one_page(
                    db,
                    claim=claim,
                    worker_id=worker_id,
                    page_size=page_size,
                    lease_seconds=lease_seconds,
                ):
                    return
        async with session_factory() as db:
            job = (
                await db.execute(
                    select(RecorderEvidenceIndexJob)
                    .where(
                        RecorderEvidenceIndexJob.id == claim.job_id,
                        RecorderEvidenceIndexJob.namespace == claim.namespace,
                    )
                    .with_for_update()
                )
            ).scalar_one_or_none()
            if job is not None and job.status == "running" and job.lease_owner == worker_id:
                job.lease_owner = None
                job.lease_expires_at = None
                job.next_attempt_at = await _database_now(db)
                await db.commit()
    except asyncio.CancelledError:
        raise
    except BaseException as exc:
        await _record_processing_failure(
            session_factory,
            claim=claim,
            worker_id=worker_id,
            exc=exc,
        )
    finally:
        set_current_namespace(None)
        set_current_barrier_group(None)


def recorder_index_worker_status() -> tuple[bool, datetime | None]:
    settings = get_settings()
    if not settings.recorder_evidence_index_worker_enabled:
        return False, _worker_last_heartbeat_at
    threshold = max(30.0, settings.recorder_evidence_index_worker_poll_seconds * 5)
    healthy = bool(
        _worker_last_iteration_healthy
        and _worker_last_heartbeat_at is not None
        and (datetime.now(UTC) - _worker_last_heartbeat_at).total_seconds() <= threshold
    )
    return healthy, _worker_last_heartbeat_at


def refresh_recorder_index_worker_process_metrics() -> None:
    from .metrics import set_recorder_index_worker_state

    healthy, heartbeat = recorder_index_worker_status()
    set_recorder_index_worker_state(
        enabled=get_settings().recorder_evidence_index_worker_enabled,
        healthy=healthy,
        heartbeat_at=heartbeat,
    )


async def recorder_index_inventory(db: AsyncSession) -> dict[str, object]:
    counts = {
        str(status): int(count)
        for status, count in (
            await db.execute(
                select(
                    RecorderEvidenceIndexJob.status,
                    func.count(RecorderEvidenceIndexJob.id),
                ).group_by(RecorderEvidenceIndexJob.status)
            )
        ).all()
    }
    progress = (
        await db.execute(
            select(
                func.coalesce(func.sum(RecorderEvidenceIndexJob.events_indexed), 0),
                func.coalesce(func.sum(RecorderEvidenceIndexJob.snapshot_event_count), 0),
                func.min(RecorderEvidenceIndexJob.created_at),
            ).where(RecorderEvidenceIndexJob.status.in_(("pending", "running")))
        )
    ).one()
    healthy, heartbeat = recorder_index_worker_status()
    return {
        "counts": counts,
        "events_indexed": int(progress[0] or 0),
        "snapshot_events": int(progress[1] or 0),
        "oldest_active_at": progress[2],
        "worker_enabled": get_settings().recorder_evidence_index_worker_enabled,
        "worker_healthy": healthy,
        "worker_heartbeat_at": heartbeat,
    }


async def run_recorder_index_worker(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """Poll, lease, and advance Recorder snapshots until cancellation."""

    global _worker_last_poll_at, _worker_last_heartbeat_at
    global _worker_last_iteration_healthy
    settings = get_settings()
    if not settings.recorder_evidence_index_worker_enabled:
        return
    worker_id = f"recorder-index:{uuid.uuid4()}"
    logger.info("Durable Recorder evidence indexing worker started")
    try:
        while True:
            now = datetime.now(UTC)
            _worker_last_poll_at = now
            _worker_last_heartbeat_at = now
            try:
                set_current_namespace("__admin__")
                set_current_barrier_group(None)
                async with session_factory() as db:
                    claims = await claim_due_recorder_index_jobs(
                        db,
                        worker_id=worker_id,
                        batch_size=min(
                            settings.recorder_evidence_index_worker_batch_size,
                            settings.recorder_evidence_index_worker_concurrency,
                        ),
                        lease_seconds=settings.recorder_evidence_index_worker_lease_seconds,
                    )
                set_current_namespace(None)
                set_current_barrier_group(None)
                await asyncio.gather(
                    *(
                        process_recorder_index_job(
                            session_factory,
                            claim=claim,
                            worker_id=worker_id,
                            page_size=settings.recorder_evidence_index_worker_page_size,
                            max_pages=(
                                settings.recorder_evidence_index_worker_max_pages_per_claim
                            ),
                            lease_seconds=(
                                settings.recorder_evidence_index_worker_lease_seconds
                            ),
                        )
                        for claim in claims
                    )
                )
                _worker_last_iteration_healthy = True
            except asyncio.CancelledError:
                raise
            except Exception:
                _worker_last_iteration_healthy = False
                logger.warning("Recorder evidence indexing poll failed")
            finally:
                set_current_namespace(None)
                set_current_barrier_group(None)
                _worker_last_heartbeat_at = datetime.now(UTC)
            await asyncio.sleep(settings.recorder_evidence_index_worker_poll_seconds)
    finally:
        set_current_namespace("__admin__")
        set_current_barrier_group(None)
        try:
            async with session_factory() as db:
                rows = list(
                    (
                        await db.execute(
                            select(RecorderEvidenceIndexJob)
                            .where(RecorderEvidenceIndexJob.lease_owner == worker_id)
                            .with_for_update(
                                skip_locked=db.get_bind().dialect.name == "postgresql"
                            )
                        )
                    ).scalars()
                )
                now = await _database_now(db)
                for row in rows:
                    row.lease_owner = None
                    row.lease_expires_at = None
                    row.next_attempt_at = now
                    row.updated_at = now
                await db.commit()
        finally:
            set_current_namespace(None)
            set_current_barrier_group(None)


async def retry_recorder_index_job(
    db: AsyncSession,
    *,
    job: RecorderEvidenceIndexJob,
) -> RecorderEvidenceIndexJob:
    """Reset a failed fixed snapshot after an operator remedies its stable cause."""

    locked = (
        await db.execute(
            select(RecorderEvidenceIndexJob)
            .where(
                RecorderEvidenceIndexJob.id == job.id,
                RecorderEvidenceIndexJob.namespace == job.namespace,
            )
            .with_for_update()
        )
    ).scalar_one()
    if locked.status != "failed":
        raise ValueError("Only failed Recorder evidence indexing jobs can be retried")
    decision = await db.get(DecisionRecord, locked.decision_id)
    if decision is None or decision.namespace != locked.namespace:
        raise RecorderIndexSnapshotInvariantError(
            "Recorder indexing job references a missing decision"
        )
    now = await _database_now(db)
    locked.status = "pending"
    locked.failed_at = None
    locked.failure_code = None
    locked.consecutive_failures = 0
    locked.next_attempt_at = now
    locked.last_error_code = None
    locked.last_error_digest = None
    locked.updated_at = now
    await _mark_recorder_job_coverage(
        db, decision=decision, job=locked, state="pending"
    )
    return locked
