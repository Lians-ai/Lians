"""Durable autonomous processing for exhaustive impact-assessment snapshots.

Queue discovery is the only operation performed under the internal admin RLS
sentinel.  Every claimed assessment is subsequently loaded and processed under
its persisted namespace and exact information-barrier context.  One bounded
page of matches, aggregate counters, and the keyset cursor commits atomically;
after a crash, another replica can therefore replay at most one idempotent page.
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any, Awaitable, Callable
from uuid import UUID

from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from .api.deps import AuthContext
from .config import Settings, get_settings
from .db import set_current_barrier_group, set_current_namespace
from .evidence_models import (
    DecisionEvidenceCoverageSet,
    DecisionImpactAssessmentJob,
)
from .evidence_service import impact_barrier_scope, upsert_impact_assessment_match
from .metrics import record_impact_job_outcome, set_impact_worker_state
from .models import DecisionRecord

logger = logging.getLogger("lians.impact_worker")

PageMatcher = Callable[
    [
        DecisionImpactAssessmentJob,
        list[tuple[DecisionEvidenceCoverageSet, DecisionRecord]],
        AuthContext,
        AsyncSession,
    ],
    Awaitable[tuple[dict[UUID, tuple[Any, set[str]]], int, int]],
]
Completer = Callable[
    [DecisionImpactAssessmentJob, AsyncSession],
    Awaitable[None],
]


class ImpactAssessmentLeaseConflict(RuntimeError):
    """The requested job is actively owned by another processor."""


class ImpactAssessmentTerminal(RuntimeError):
    """The requested job is terminal and cannot be advanced."""


class ImpactSnapshotInvariantError(RuntimeError):
    """The frozen registration snapshot cannot be traversed exactly."""


@dataclass(frozen=True, slots=True)
class ClaimedImpactAssessment:
    job_id: UUID
    namespace: str
    barrier_group: str | None
    principal_ref: str
    auth_method: str


@dataclass(frozen=True, slots=True)
class ImpactProcessingResult:
    job_id: UUID
    status: str
    pages_processed: int
    completed: bool
    error_code: str | None = None


_worker_last_poll_at: datetime | None = None
_worker_last_heartbeat_at: datetime | None = None
_worker_last_success_at: datetime | None = None
_worker_last_error_at: datetime | None = None
_worker_last_iteration_healthy = False


def _as_utc(value: datetime) -> datetime:
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


async def _database_now(db: AsyncSession) -> datetime:
    value = (await db.execute(select(func.now()))).scalar_one()
    return _as_utc(value)


def _error_digest(exc: BaseException) -> str:
    identity = f"{type(exc).__module__}.{type(exc).__qualname__}"
    return hashlib.sha256(identity.encode("utf-8")).hexdigest()


def _classify_error(exc: BaseException) -> str:
    if isinstance(exc, ImpactSnapshotInvariantError):
        return "snapshot_invariant"
    if isinstance(exc, TimeoutError):
        return "timeout"
    return "processing_error"


def _backoff_seconds(job_id: UUID, failure_number: int) -> float:
    settings = get_settings()
    ceiling = min(
        settings.impact_assessment_worker_retry_max_seconds,
        settings.impact_assessment_worker_retry_base_seconds
        * (2 ** max(0, failure_number - 1)),
    )
    digest = hashlib.sha256(f"{job_id}:{failure_number}".encode("ascii")).digest()
    # Stable 50-100% jitter keeps replicas from synchronizing after a shared
    # database outage while retaining deterministic deferred tests.
    jitter = 0.5 + int.from_bytes(digest[:4], "big") / (2**32) * 0.5
    return max(0.1, ceiling * jitter)


def validate_impact_worker_configuration(
    settings: Settings,
    *,
    production: bool,
) -> list[str]:
    """Return closed, credential-free startup configuration failures."""

    errors: list[str] = []
    if production and not settings.impact_assessment_worker_enabled:
        errors.append("IMPACT_ASSESSMENT_WORKER_ENABLED must be true in production")
    if not 0.05 <= settings.impact_assessment_worker_poll_seconds <= 60:
        errors.append("IMPACT_ASSESSMENT_WORKER_POLL_SECONDS must be between 0.05 and 60")
    if not 1 <= settings.impact_assessment_worker_batch_size <= 100:
        errors.append("IMPACT_ASSESSMENT_WORKER_BATCH_SIZE must be between 1 and 100")
    if not 1 <= settings.impact_assessment_worker_concurrency <= 32:
        errors.append("IMPACT_ASSESSMENT_WORKER_CONCURRENCY must be between 1 and 32")
    minimum_lease = settings.database_statement_timeout_ms / 1000 + 15
    if not minimum_lease <= settings.impact_assessment_worker_lease_seconds <= 3600:
        errors.append(
            "IMPACT_ASSESSMENT_WORKER_LEASE_SECONDS must exceed the database "
            "statement timeout by at least 15 seconds and be no more than 3600"
        )
    if not 1 <= settings.impact_assessment_worker_page_size <= 500:
        errors.append("IMPACT_ASSESSMENT_WORKER_PAGE_SIZE must be between 1 and 500")
    if not 1 <= settings.impact_assessment_worker_max_pages_per_claim <= 20:
        errors.append(
            "IMPACT_ASSESSMENT_WORKER_MAX_PAGES_PER_CLAIM must be between 1 and 20"
        )
    if settings.impact_assessment_worker_retry_base_seconds <= 0:
        errors.append("IMPACT_ASSESSMENT_WORKER_RETRY_BASE_SECONDS must be positive")
    if not (
        settings.impact_assessment_worker_retry_base_seconds
        <= settings.impact_assessment_worker_retry_max_seconds
        <= 3600
    ):
        errors.append(
            "IMPACT_ASSESSMENT_WORKER_RETRY_MAX_SECONDS must be at least the base "
            "and no more than 3600"
        )
    if not 1 <= settings.impact_assessment_worker_max_attempts <= 100:
        errors.append("IMPACT_ASSESSMENT_WORKER_MAX_ATTEMPTS must be between 1 and 100")
    return errors


def _terminal_after_expired_lease(
    row: DecisionImpactAssessmentJob,
    *,
    now: datetime,
) -> bool:
    """Account for a crashed owner before a new owner takes the lease."""

    if row.lease_owner is None or row.lease_expires_at is None:
        return False
    if _as_utc(row.lease_expires_at) > now:
        return False
    row.consecutive_failures += 1
    row.last_error_code = "lease_expired"
    row.last_error_digest = hashlib.sha256(b"lease_expired").hexdigest()
    if row.consecutive_failures < row.attempt_limit:
        return False
    row.status = "failed"
    row.failure_code = "worker_attempt_limit_exhausted"
    row.failed_at = now
    row.lease_owner = None
    row.lease_expires_at = None
    row.heartbeat_at = now
    row.updated_at = now
    return True


def _claim_row(
    row: DecisionImpactAssessmentJob,
    *,
    worker_id: str,
    now: datetime,
    lease_seconds: int,
) -> tuple[ClaimedImpactAssessment | None, bool, bool]:
    lease_expired = bool(
        row.lease_owner is not None
        and row.lease_expires_at is not None
        and _as_utc(row.lease_expires_at) <= now
    )
    if _terminal_after_expired_lease(row, now=now):
        return None, lease_expired, True
    if row.started_at is None:
        row.started_at = now
    row.status = "running"
    row.processing_attempts += 1
    row.last_attempt_at = now
    row.heartbeat_at = now
    row.next_attempt_at = now
    row.lease_owner = worker_id
    row.lease_expires_at = now + timedelta(seconds=lease_seconds)
    row.updated_at = now
    return (
        ClaimedImpactAssessment(
            job_id=row.id,
            namespace=row.namespace,
            barrier_group=row.barrier_group,
            principal_ref=row.requested_by_principal_ref,
            auth_method=row.requested_by_auth_method,
        ),
        lease_expired,
        False,
    )


async def claim_due_impact_assessments(
    db: AsyncSession,
    *,
    worker_id: str,
    batch_size: int,
    lease_seconds: int,
) -> list[ClaimedImpactAssessment]:
    """Claim due jobs globally; caller must hold the internal admin RLS context."""

    now = await _database_now(db)
    eligible = (
        DecisionImpactAssessmentJob.status.in_(("pending", "running")),
        DecisionImpactAssessmentJob.next_attempt_at <= now,
        or_(
            DecisionImpactAssessmentJob.lease_owner.is_(None),
            DecisionImpactAssessmentJob.lease_expires_at <= now,
        ),
    )
    statement = (
        select(DecisionImpactAssessmentJob)
        .where(*eligible)
        .order_by(
            DecisionImpactAssessmentJob.next_attempt_at,
            DecisionImpactAssessmentJob.created_at,
            DecisionImpactAssessmentJob.id,
        )
        .limit(max(1, min(100, batch_size)))
        .with_for_update(skip_locked=db.get_bind().dialect.name == "postgresql")
    )
    rows = list((await db.execute(statement)).scalars())
    claimed: list[ClaimedImpactAssessment] = []
    expired_count = 0
    failed_count = 0
    for row in rows:
        claim, expired, failed = _claim_row(
            row,
            worker_id=worker_id,
            now=now,
            lease_seconds=lease_seconds,
        )
        expired_count += int(expired)
        failed_count += int(failed)
        if claim is not None:
            claimed.append(claim)
    await db.commit()
    for _ in range(expired_count):
        record_impact_job_outcome("lease_lost")
    for _ in range(failed_count):
        record_impact_job_outcome("failed")
    for _ in claimed:
        record_impact_job_outcome("claimed")
    return claimed


async def claim_impact_assessment_for_request(
    db: AsyncSession,
    *,
    job_id: UUID,
    namespace: str,
    barrier_group: str | None,
    worker_id: str,
    lease_seconds: int,
) -> tuple[DecisionImpactAssessmentJob, ClaimedImpactAssessment | None]:
    """Claim one caller-visible job without bypassing an autonomous owner."""

    row = (
        await db.execute(
            select(DecisionImpactAssessmentJob)
            .where(
                DecisionImpactAssessmentJob.id == job_id,
                DecisionImpactAssessmentJob.namespace == namespace,
                DecisionImpactAssessmentJob.barrier_scope
                == impact_barrier_scope(barrier_group),
            )
            .with_for_update()
        )
    ).scalar_one_or_none()
    if row is None:
        raise LookupError("Impact assessment not found")
    if row.status == "completed":
        # Do not retain a row lock for the remainder of a read-only response.
        await db.commit()
        return row, None
    if row.status == "failed":
        raise ImpactAssessmentTerminal("Failed assessment requires operator review")
    now = await _database_now(db)
    if (
        row.lease_owner is not None
        and row.lease_owner != worker_id
        and row.lease_expires_at is not None
        and _as_utc(row.lease_expires_at) > now
    ):
        raise ImpactAssessmentLeaseConflict(
            "Impact assessment is currently advancing on another processor"
        )
    claim, expired, failed = _claim_row(
        row,
        worker_id=worker_id,
        now=now,
        lease_seconds=lease_seconds,
    )
    await db.commit()
    if expired:
        record_impact_job_outcome("lease_lost")
    if failed:
        record_impact_job_outcome("failed")
        raise ImpactAssessmentTerminal("Impact assessment exhausted its retry limit")
    if claim is not None:
        record_impact_job_outcome("claimed")
    return row, claim


def _processing_callbacks() -> tuple[PageMatcher, Completer]:
    # Imported only after routes_decisions has finished initialization.  The
    # reusable service owns leasing, pagination, persistence, and retries; the
    # route module still owns the shared indexed/legacy matching algorithm.
    from .api.routes_decisions import (
        _assessment_page_matches,
        _complete_impact_assessment,
    )

    return _assessment_page_matches, _complete_impact_assessment


async def _record_processing_failure(
    db: AsyncSession,
    *,
    claim: ClaimedImpactAssessment,
    worker_id: str,
    exc: BaseException,
) -> str:
    row = (
        await db.execute(
            select(DecisionImpactAssessmentJob)
            .where(
                DecisionImpactAssessmentJob.id == claim.job_id,
                DecisionImpactAssessmentJob.namespace == claim.namespace,
                DecisionImpactAssessmentJob.barrier_scope
                == impact_barrier_scope(claim.barrier_group),
            )
            .with_for_update()
        )
    ).scalar_one_or_none()
    if row is None or row.status in {"completed", "failed"}:
        status = row.status if row is not None else "lease_lost"
        await db.rollback()
        return status
    if row.lease_owner != worker_id:
        await db.rollback()
        record_impact_job_outcome("lease_lost")
        return "lease_lost"
    now = await _database_now(db)
    error_code = _classify_error(exc)
    row.consecutive_failures += 1
    row.last_error_code = error_code
    row.last_error_digest = _error_digest(exc)
    row.heartbeat_at = now
    row.lease_owner = None
    row.lease_expires_at = None
    row.updated_at = now
    terminal = isinstance(exc, ImpactSnapshotInvariantError) or (
        row.consecutive_failures >= row.attempt_limit
    )
    if terminal:
        row.status = "failed"
        row.failure_code = (
            "snapshot_visibility_invariant"
            if isinstance(exc, ImpactSnapshotInvariantError)
            else "worker_attempt_limit_exhausted"
        )
        row.failed_at = now
        row.next_attempt_at = now
        outcome = "failed"
    else:
        row.next_attempt_at = now + timedelta(
            seconds=_backoff_seconds(row.id, row.consecutive_failures)
        )
        outcome = "retry"
    await db.commit()
    record_impact_job_outcome(outcome)
    return row.status


async def _release_claim_after_cancellation(
    db: AsyncSession,
    *,
    claim: ClaimedImpactAssessment,
    worker_id: str,
) -> None:
    """Yield a cancelled claim without recording a poison-job failure."""

    row = (
        await db.execute(
            select(DecisionImpactAssessmentJob)
            .where(
                DecisionImpactAssessmentJob.id == claim.job_id,
                DecisionImpactAssessmentJob.namespace == claim.namespace,
                DecisionImpactAssessmentJob.barrier_scope
                == impact_barrier_scope(claim.barrier_group),
            )
            .with_for_update()
        )
    ).scalar_one_or_none()
    if (
        row is None
        or row.status in {"completed", "failed"}
        or row.lease_owner != worker_id
    ):
        await db.rollback()
        return
    now = await _database_now(db)
    row.lease_owner = None
    row.lease_expires_at = None
    row.next_attempt_at = now
    row.heartbeat_at = now
    row.updated_at = now
    await db.commit()


async def advance_claimed_impact_assessment(
    db: AsyncSession,
    *,
    claim: ClaimedImpactAssessment,
    worker_id: str,
    auth: AuthContext,
    page_size: int,
    max_pages: int,
    lease_seconds: int,
    page_matcher: PageMatcher | None = None,
    completer: Completer | None = None,
    raise_on_error: bool = False,
) -> tuple[DecisionImpactAssessmentJob, ImpactProcessingResult]:
    """Advance a claimed job through crash-safe, independently committed pages."""

    if page_matcher is None or completer is None:
        default_matcher, default_completer = _processing_callbacks()
        page_matcher = page_matcher or default_matcher
        completer = completer or default_completer
    bounded_page_size = max(1, min(500, page_size))
    bounded_max_pages = max(1, min(20, max_pages))
    pages_processed = 0
    try:
        for page_index in range(bounded_max_pages):
            job = (
                await db.execute(
                    select(DecisionImpactAssessmentJob)
                    .where(
                        DecisionImpactAssessmentJob.id == claim.job_id,
                        DecisionImpactAssessmentJob.namespace == claim.namespace,
                        DecisionImpactAssessmentJob.barrier_scope
                        == impact_barrier_scope(claim.barrier_group),
                    )
                    .with_for_update()
                )
            ).scalar_one_or_none()
            if job is None:
                raise ImpactAssessmentLeaseConflict("Impact assessment lease was lost")
            if job.status in {"completed", "failed"}:
                return job, ImpactProcessingResult(
                    job_id=job.id,
                    status=job.status,
                    pages_processed=pages_processed,
                    completed=job.status == "completed",
                )
            if job.lease_owner != worker_id:
                raise ImpactAssessmentLeaseConflict("Impact assessment lease was lost")

            page_filters = [
                DecisionEvidenceCoverageSet.namespace == job.namespace,
                DecisionEvidenceCoverageSet.sequence > job.cursor_coverage_sequence,
                DecisionEvidenceCoverageSet.sequence
                <= job.snapshot_max_coverage_sequence,
                DecisionRecord.namespace == job.namespace,
            ]
            if job.barrier_group is not None:
                for column in (
                    DecisionEvidenceCoverageSet.barrier_group,
                    DecisionRecord.barrier_group,
                ):
                    page_filters.append(
                        or_(column.is_(None), column == job.barrier_group)
                    )
            raw_page = (
                await db.execute(
                    select(DecisionEvidenceCoverageSet, DecisionRecord)
                    .join(
                        DecisionRecord,
                        DecisionRecord.id
                        == DecisionEvidenceCoverageSet.decision_id,
                    )
                    .where(*page_filters)
                    .order_by(DecisionEvidenceCoverageSet.sequence)
                    .limit(bounded_page_size + 1)
                )
            ).all()
            has_more = len(raw_page) > bounded_page_size
            page = list(raw_page[:bounded_page_size])
            if not page:
                now = await _database_now(db)
                if job.cursor_coverage_sequence != job.snapshot_max_coverage_sequence:
                    raise ImpactSnapshotInvariantError(
                        "Frozen impact snapshot has an unreachable coverage watermark"
                    )
                if job.decisions_scanned != job.snapshot_decision_count:
                    raise ImpactSnapshotInvariantError(
                        "Frozen impact snapshot row count does not match its scan"
                    )
                await completer(job, db)
                job.consecutive_failures = 0
                job.last_error_code = None
                job.last_error_digest = None
                job.heartbeat_at = now
                job.lease_owner = None
                job.lease_expires_at = None
                job.next_attempt_at = now
                await db.commit()
                await db.refresh(job)
                record_impact_job_outcome("completed")
                return job, ImpactProcessingResult(
                    job_id=job.id,
                    status=job.status,
                    pages_processed=pages_processed,
                    completed=True,
                )

            matches, indexed_matches, legacy_matches = await page_matcher(
                job,
                page,
                auth,
                db,
            )
            for decision_id, (item, sources) in matches.items():
                persisted_match, created = await upsert_impact_assessment_match(
                    db,
                    job=job,
                    decision_id=decision_id,
                    impact_status=item.impact_status,
                    match_basis=item.match_basis,
                    match_sources=sources,
                    risk_score=item.risk_score,
                    risk_level=item.priority,
                )
                if created:
                    job.matches_found += 1
                    if persisted_match.impact_status == "direct_reference":
                        job.direct_count += 1
                    else:
                        job.reachable_count += 1
            now = await _database_now(db)
            job.decisions_scanned += len(page)
            job.fallback_candidates_scanned += len(page)
            job.indexed_decisions_matched += indexed_matches
            job.legacy_decisions_matched += legacy_matches
            job.cursor_coverage_sequence = page[-1][0].sequence
            job.pages_completed += 1
            pages_processed += 1
            job.consecutive_failures = 0
            job.last_error_code = None
            job.last_error_digest = None
            job.heartbeat_at = now
            job.updated_at = now

            if not has_more:
                if job.cursor_coverage_sequence != job.snapshot_max_coverage_sequence:
                    raise ImpactSnapshotInvariantError(
                        "Frozen impact snapshot ended before its coverage watermark"
                    )
                if job.decisions_scanned != job.snapshot_decision_count:
                    raise ImpactSnapshotInvariantError(
                        "Frozen impact snapshot row count does not match its scan"
                    )
                await completer(job, db)
            if job.status == "completed" or page_index + 1 >= bounded_max_pages:
                job.lease_owner = None
                job.lease_expires_at = None
                job.next_attempt_at = now
            else:
                job.lease_expires_at = now + timedelta(seconds=lease_seconds)
            await db.commit()
            await db.refresh(job)
            record_impact_job_outcome("advanced")
            if job.status == "completed":
                record_impact_job_outcome("completed")
                return job, ImpactProcessingResult(
                    job_id=job.id,
                    status=job.status,
                    pages_processed=pages_processed,
                    completed=True,
                )

        return job, ImpactProcessingResult(
            job_id=job.id,
            status=job.status,
            pages_processed=pages_processed,
            completed=False,
        )
    except asyncio.CancelledError:
        await db.rollback()
        try:
            await _release_claim_after_cancellation(
                db,
                claim=claim,
                worker_id=worker_id,
            )
        except Exception:
            # The outer worker releases all remaining owner leases as a second
            # cleanup boundary; request-owned leases remain expiry-recoverable.
            await db.rollback()
        raise
    except Exception as exc:
        await db.rollback()
        status = await _record_processing_failure(
            db,
            claim=claim,
            worker_id=worker_id,
            exc=exc,
        )
        logger.error(
            "Impact assessment page processing failed",
            extra={
                "error_code": _classify_error(exc),
                "terminal": status == "failed",
            },
        )
        if raise_on_error:
            raise
        job = (
            await db.execute(
                select(DecisionImpactAssessmentJob).where(
                    DecisionImpactAssessmentJob.id == claim.job_id,
                    DecisionImpactAssessmentJob.namespace == claim.namespace,
                )
            )
        ).scalar_one()
        return job, ImpactProcessingResult(
            job_id=claim.job_id,
            status=status,
            pages_processed=pages_processed,
            completed=False,
            error_code=_classify_error(exc),
        )


async def release_impact_worker_leases(
    db: AsyncSession,
    *,
    worker_id: str,
) -> int:
    """Make this process's unfinished claims immediately recoverable on shutdown."""

    rows = list(
        (
            await db.execute(
                select(DecisionImpactAssessmentJob)
                .where(
                    DecisionImpactAssessmentJob.status.in_(("pending", "running")),
                    DecisionImpactAssessmentJob.lease_owner == worker_id,
                )
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
        row.heartbeat_at = now
        row.updated_at = now
    await db.commit()
    return len(rows)


def _set_worker_health(healthy: bool) -> None:
    global _worker_last_iteration_healthy
    _worker_last_iteration_healthy = healthy
    set_impact_worker_state(
        enabled=get_settings().impact_assessment_worker_enabled,
        healthy=healthy,
        heartbeat_at=_worker_last_heartbeat_at,
    )


def impact_worker_status() -> tuple[bool, datetime | None]:
    settings = get_settings()
    heartbeat = _worker_last_heartbeat_at or _worker_last_poll_at
    if not settings.impact_assessment_worker_enabled:
        return False, heartbeat
    threshold = max(30.0, settings.impact_assessment_worker_poll_seconds * 5)
    fresh = heartbeat is not None and (
        datetime.now(UTC) - heartbeat
    ).total_seconds() <= threshold
    return _worker_last_iteration_healthy and fresh, heartbeat


def refresh_impact_worker_process_metrics() -> None:
    healthy, heartbeat = impact_worker_status()
    set_impact_worker_state(
        enabled=get_settings().impact_assessment_worker_enabled,
        healthy=healthy,
        heartbeat_at=heartbeat,
    )


async def impact_assessment_inventory(
    db: AsyncSession,
    *,
    namespace: str,
    barrier_group: str | None,
) -> dict[str, Any]:
    """Return tenant-authorized queue inventory without internal lease owners."""

    filters = [
        DecisionImpactAssessmentJob.namespace == namespace,
        DecisionImpactAssessmentJob.barrier_scope
        == impact_barrier_scope(barrier_group),
    ]
    counts = {
        str(status): int(count)
        for status, count in (
            await db.execute(
                select(
                    DecisionImpactAssessmentJob.status,
                    func.count(DecisionImpactAssessmentJob.id),
                )
                .where(*filters)
                .group_by(DecisionImpactAssessmentJob.status)
            )
        ).all()
    }
    now = await _database_now(db)
    active_leases = int(
        (
            await db.execute(
                select(func.count(DecisionImpactAssessmentJob.id)).where(
                    *filters,
                    DecisionImpactAssessmentJob.status == "running",
                    DecisionImpactAssessmentJob.lease_owner.is_not(None),
                    DecisionImpactAssessmentJob.lease_expires_at > now,
                )
            )
        ).scalar_one()
    )
    retry_wait = int(
        (
            await db.execute(
                select(func.count(DecisionImpactAssessmentJob.id)).where(
                    *filters,
                    DecisionImpactAssessmentJob.status == "running",
                    DecisionImpactAssessmentJob.lease_owner.is_(None),
                    DecisionImpactAssessmentJob.last_error_code.is_not(None),
                )
            )
        ).scalar_one()
    )
    healthy, heartbeat = impact_worker_status()
    return {
        "worker_enabled": get_settings().impact_assessment_worker_enabled,
        "worker_healthy": healthy,
        "worker_last_heartbeat_at": heartbeat,
        "pending_jobs": counts.get("pending", 0),
        "running_jobs": counts.get("running", 0),
        "completed_jobs": counts.get("completed", 0),
        "failed_jobs": counts.get("failed", 0),
        "active_leases": active_leases,
        "retry_wait_jobs": retry_wait,
    }


async def run_impact_assessment_worker(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """Continuously advance frozen impact snapshots across many API replicas."""

    global _worker_last_error_at
    global _worker_last_heartbeat_at
    global _worker_last_poll_at
    global _worker_last_success_at

    settings = get_settings()
    if not settings.impact_assessment_worker_enabled:
        _set_worker_health(False)
        return
    worker_id = f"impact:{uuid.uuid4().hex}"
    concurrency = min(
        settings.impact_assessment_worker_batch_size,
        settings.impact_assessment_worker_concurrency,
    )
    semaphore = asyncio.Semaphore(concurrency)

    async def process_one(claim: ClaimedImpactAssessment) -> None:
        global _worker_last_success_at
        async with semaphore:
            set_current_namespace(claim.namespace)
            set_current_barrier_group(claim.barrier_group)
            try:
                auth = AuthContext(
                    namespace=claim.namespace,
                    scopes=["read", "write"],
                    barrier_group=claim.barrier_group,
                    principal_id=claim.principal_ref,
                    principal_type="impact_assessment_worker",
                    auth_method=claim.auth_method,
                )
                async with session_factory() as db:
                    _job, result = await advance_claimed_impact_assessment(
                        db,
                        claim=claim,
                        worker_id=worker_id,
                        auth=auth,
                        page_size=settings.impact_assessment_worker_page_size,
                        max_pages=settings.impact_assessment_worker_max_pages_per_claim,
                        lease_seconds=settings.impact_assessment_worker_lease_seconds,
                    )
                if result.error_code is None:
                    _worker_last_success_at = datetime.now(UTC)
            finally:
                set_current_namespace(None)
                set_current_barrier_group(None)

    async def process_batch(claims: list[ClaimedImpactAssessment]) -> None:
        global _worker_last_heartbeat_at
        batch = asyncio.gather(
            *(process_one(claim) for claim in claims),
            return_exceptions=True,
        )
        heartbeat_seconds = min(
            5.0,
            max(1.0, settings.impact_assessment_worker_poll_seconds * 2),
        )
        try:
            while not batch.done():
                done, _ = await asyncio.wait({batch}, timeout=heartbeat_seconds)
                if batch in done:
                    break
                _worker_last_heartbeat_at = datetime.now(UTC)
                refresh_impact_worker_process_metrics()
            results = await batch
            if any(isinstance(result, BaseException) for result in results):
                # Individual errors are already persisted by the page boundary
                # when possible. Raise only a closed aggregate signal after all
                # sibling claims have reached a safe boundary.
                raise RuntimeError("impact worker batch task failed")
        finally:
            if not batch.done():
                batch.cancel()
                await asyncio.gather(batch, return_exceptions=True)

    _worker_last_heartbeat_at = datetime.now(UTC)
    _set_worker_health(False)
    logger.info("Autonomous impact-assessment worker started")
    try:
        while True:
            try:
                set_current_namespace("__admin__")
                set_current_barrier_group(None)
                try:
                    async with session_factory() as db:
                        claims = await claim_due_impact_assessments(
                            db,
                            worker_id=worker_id,
                            batch_size=concurrency,
                            lease_seconds=(
                                settings.impact_assessment_worker_lease_seconds
                            ),
                        )
                    _worker_last_poll_at = datetime.now(UTC)
                    _worker_last_heartbeat_at = _worker_last_poll_at
                    _set_worker_health(True)
                finally:
                    set_current_namespace(None)
                    set_current_barrier_group(None)
            except asyncio.CancelledError:
                raise
            except Exception:
                _worker_last_error_at = datetime.now(UTC)
                _set_worker_health(False)
                logger.error(
                    "Autonomous impact-assessment queue poll failed",
                    extra={"error_code": "queue_poll_failure"},
                )
                await asyncio.sleep(
                    settings.impact_assessment_worker_poll_seconds
                )
                continue
            if claims:
                try:
                    await process_batch(claims)
                except asyncio.CancelledError:
                    raise
                except Exception:
                    _worker_last_error_at = datetime.now(UTC)
                    _set_worker_health(False)
                    logger.error(
                        "Autonomous impact-assessment processing batch failed",
                        extra={"error_code": "processing_batch_failure"},
                    )
                    await asyncio.sleep(
                        settings.impact_assessment_worker_poll_seconds
                    )
            else:
                await asyncio.sleep(settings.impact_assessment_worker_poll_seconds)
    finally:
        set_current_namespace("__admin__")
        set_current_barrier_group(None)
        try:
            async with session_factory() as db:
                released = await release_impact_worker_leases(
                    db,
                    worker_id=worker_id,
                )
            if released:
                logger.info(
                    "Released impact-assessment leases during shutdown",
                    extra={"released_count": released},
                )
        except Exception:
            logger.error(
                "Failed to release impact-assessment leases on shutdown",
                extra={"error_code": "shutdown_release_failure"},
            )
        finally:
            set_current_namespace(None)
            set_current_barrier_group(None)
            _set_worker_health(False)
