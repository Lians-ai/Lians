"""Durable, idempotent, fixed-snapshot data-subject erasure.

The enqueue transaction is the irreversible privacy boundary: it serializes
subject-bearing writes, fences the whole tenant recall-cache generation, and
destroys the subject DEK.  No unbounded row set is locked or materialized.
Physical and derivative-store scrubbing then advances in restart-safe keyset
pages whose exact counts and terminal proof are durable database state.
"""

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
from .cache import invalidate_namespace
from .cache_fence import acquire_namespace_cache_lock
from .config import Settings, get_settings
from .db import set_current_barrier_group, set_current_namespace
from .dek_cache import evict_dek
from .models import (
    EventLog,
    LiveFact,
    Memory,
    PendingAdmission,
    Relationship,
    SubjectKey,
)
from .pii import destroy_subject_key, lock_subject_key_for_update
from .secret_storage import (
    PENDING_CONTENT_PURPOSE,
    SUBJECT_ERASURE_LOCATOR_PURPOSE,
    seal_text,
    unseal_text,
)
from .subject_erasure_models import (
    SubjectErasureJob,
    SubjectErasureMemoryEvidence,
)
from .subject_privacy import erasure_request_reference, subject_reference

_MANIFEST_DOMAIN = b"lians/subject-erasure-memory-manifest/v1"
_MANIFEST_EMPTY = hashlib.sha256(_MANIFEST_DOMAIN).hexdigest()
_TERMINAL_PHASES = {"completed"}

_worker_last_heartbeat_at: datetime | None = None
_worker_last_iteration_healthy = False
logger = logging.getLogger("lians.subject_erasure_worker")


class SubjectErasureInvariantError(RuntimeError):
    """A frozen erasure snapshot cannot be traversed or certified exactly."""


class SubjectErasureLeaseConflict(RuntimeError):
    """The worker no longer owns the durable erasure lease."""


class SubjectErasureNotComplete(RuntimeError):
    """A certificate was requested before bounded physical scrubbing completed."""

    def __init__(self, job: SubjectErasureJob):
        super().__init__("Subject erasure is not complete")
        self.job = job


@dataclass(frozen=True, slots=True)
class ClaimedSubjectErasureJob:
    job_id: UUID
    namespace: str


def _utc(value: datetime) -> datetime:
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


async def _database_now(db: AsyncSession) -> datetime:
    value = (await db.execute(select(func.now()))).scalar_one()
    return _utc(value)


def _error_digest(exc: BaseException) -> str:
    identity = f"{type(exc).__module__}.{type(exc).__qualname__}"
    return hashlib.sha256(identity.encode()).hexdigest()


def _backoff_seconds(job_id: UUID, failures: int) -> float:
    settings = get_settings()
    base = settings.subject_erasure_worker_retry_base_seconds
    maximum = settings.subject_erasure_worker_retry_max_seconds
    raw = min(maximum, base * (2 ** max(0, failures - 1)))
    jitter = int.from_bytes(job_id.bytes[-2:], "big") / 65_535
    return min(maximum, raw * (0.75 + 0.5 * jitter))


def _classify_error(exc: BaseException) -> tuple[str, bool]:
    if isinstance(exc, SubjectErasureLeaseConflict):
        return "lease_lost", False
    if isinstance(exc, SubjectErasureInvariantError):
        return "snapshot_invariant_failed", True
    return "processing_failed", False


async def _snapshot_boundary(
    db: AsyncSession,
    model,
    *conditions,
) -> tuple[int, UUID | None]:
    boundary = (
        await db.execute(
            select(model.id, func.count(model.id).over())
            .where(*conditions)
            .order_by(model.id.desc())
            .limit(1)
        )
    ).one_or_none()
    if boundary is None:
        return 0, None
    maximum, count = boundary
    return int(count), maximum


def _locator_context(namespace: str, job_id: UUID) -> str:
    return f"{namespace}:{job_id}"


def _subject_candidates(job: SubjectErasureJob) -> tuple[str, ...]:
    encrypted = job.subject_locator_encrypted
    if not encrypted:
        raise SubjectErasureInvariantError("Active erasure job has no subject locator")
    raw = unseal_text(
        encrypted,
        purpose=SUBJECT_ERASURE_LOCATOR_PURPOSE,
        context=_locator_context(job.namespace, job.id),
    )
    return tuple(sorted({raw, job.subject_ref}))


def subject_erasure_job_dict(job: SubjectErasureJob, *, replayed: bool = False) -> dict:
    total = (
        int(job.snapshot_memory_count)
        + int(job.snapshot_live_fact_count)
        + int(job.snapshot_relationship_count)
        + int(job.snapshot_pending_admission_count)
    )
    scrubbed = (
        int(job.memories_scrubbed)
        + int(job.live_facts_scrubbed)
        + int(job.relationships_scrubbed)
        + int(job.pending_admissions_scrubbed)
    )
    ratio = 1.0 if job.status == "completed" else (scrubbed / total if total else 0.0)
    return {
        "job_id": job.id,
        "namespace": job.namespace,
        "subject_ref": job.subject_ref,
        "request_ref": job.request_ref,
        "status": job.status,
        "phase": job.phase,
        "key_destroyed_at": job.key_destroyed_at,
        "cache_fenced_at": job.cache_fenced_at,
        "snapshot": {
            "memories": int(job.snapshot_memory_count),
            "live_facts": int(job.snapshot_live_fact_count),
            "relationships": int(job.snapshot_relationship_count),
            "pending_admissions": int(job.snapshot_pending_admission_count),
            "total_rows": total,
        },
        "progress": {
            "memories": int(job.memories_scrubbed),
            "live_facts": int(job.live_facts_scrubbed),
            "relationships": int(job.relationships_scrubbed),
            "pending_admissions": int(job.pending_admissions_scrubbed),
            "rows_scrubbed": scrubbed,
            "pages_completed": int(job.pages_completed),
            "ratio": max(0.0, min(1.0, ratio)),
        },
        "processing_attempts": int(job.processing_attempts),
        "next_attempt_at": job.next_attempt_at,
        "last_error_code": job.last_error_code,
        "last_error_digest": job.last_error_digest,
        "failure_code": job.failure_code,
        "created_at": job.created_at,
        "started_at": job.started_at,
        "updated_at": job.updated_at,
        "completed_at": job.completed_at,
        "replayed": replayed,
    }


async def enqueue_subject_erasure(
    db: AsyncSession,
    *,
    namespace: str,
    subject_id: str,
    request_ref: str,
    principal_ref: str,
    auth_method: str,
) -> tuple[SubjectErasureJob, bool]:
    """Commit the crypto-shred boundary and an exact durable scrub snapshot."""

    persisted_subject_ref = await lock_subject_key_for_update(db, subject_id, namespace)
    existing = (
        await db.execute(
            select(SubjectErasureJob).where(
                SubjectErasureJob.namespace == namespace,
                SubjectErasureJob.subject_ref == persisted_subject_ref,
            )
        )
    ).scalar_one_or_none()
    if existing is not None:
        return existing, True

    keyed_request_ref = erasure_request_reference(namespace, request_ref)
    if keyed_request_ref is None:
        raise ValueError("request_ref must not be empty")

    candidates = tuple(sorted({subject_id, persisted_subject_ref}))
    memory_conditions = (
        Memory.namespace == namespace,
        Memory.subject_id.in_(candidates),
    )
    live_fact_conditions = (
        LiveFact.namespace == namespace,
        LiveFact.subject_id.in_(candidates),
    )
    relationship_conditions = (
        Relationship.namespace == namespace,
        Relationship.subject_id.in_(candidates),
    )
    pending_conditions = (
        PendingAdmission.namespace == namespace,
        PendingAdmission.subject_id.in_(candidates),
    )
    memory_count, memory_max = await _snapshot_boundary(
        db, Memory, *memory_conditions
    )
    live_fact_count, live_fact_max = await _snapshot_boundary(
        db, LiveFact, *live_fact_conditions
    )
    relationship_count, relationship_max = await _snapshot_boundary(
        db, Relationship, *relationship_conditions
    )
    pending_count, pending_max = await _snapshot_boundary(
        db, PendingAdmission, *pending_conditions
    )

    # Keep the namespace-wide cache exclusion out of the aggregate snapshot
    # phase. It covers only generation replacement through transaction commit.
    # This prevents an in-flight recall from filling the new generation with
    # pre-erasure plaintext without making large snapshots a tenant-wide pause.
    postgres_cache_fence = await acquire_namespace_cache_lock(
        db, namespace, shared=False
    )
    if postgres_cache_fence and get_settings().recall_cache_enabled:
        await invalidate_namespace(namespace)
    cache_fenced_at = await _database_now(db)

    job_id = uuid.uuid4()
    await destroy_subject_key(db, subject_id, namespace)
    key_row = await db.get(SubjectKey, (namespace, persisted_subject_ref))
    if key_row is None or key_row.destroyed_at is None:
        raise SubjectErasureInvariantError("Subject-key tombstone was not persisted")
    destroyed_at = _utc(key_row.destroyed_at)
    job = SubjectErasureJob(
        id=job_id,
        namespace=namespace,
        subject_ref=persisted_subject_ref,
        request_ref=keyed_request_ref,
        subject_locator_encrypted=seal_text(
            subject_id,
            purpose=SUBJECT_ERASURE_LOCATOR_PURPOSE,
            context=_locator_context(namespace, job_id),
        ),
        queued_by_principal_ref=principal_ref,
        queued_by_auth_method=auth_method,
        key_destroyed_at=destroyed_at,
        cache_fenced_at=cache_fenced_at,
        snapshot_memory_count=memory_count,
        snapshot_memory_max_id=memory_max,
        snapshot_live_fact_count=live_fact_count,
        snapshot_live_fact_max_id=live_fact_max,
        snapshot_relationship_count=relationship_count,
        snapshot_relationship_max_id=relationship_max,
        snapshot_pending_admission_count=pending_count,
        snapshot_pending_admission_max_id=pending_max,
        attempt_limit=get_settings().subject_erasure_worker_max_attempts,
        manifest_sha256=_MANIFEST_EMPTY,
        # Lease scheduling is compared against the database clock.  Using the
        # application-generated key-destruction timestamp can place a fresh
        # job in the future when clocks differ (and SQLite truncates DB time to
        # whole seconds), delaying an otherwise immediately runnable scrub.
        next_attempt_at=cache_fenced_at,
        created_at=destroyed_at,
        updated_at=destroyed_at,
    )
    db.add(job)
    await db.flush()
    await chain_log(
        db,
        namespace=namespace,
        agent_id=principal_ref,
        op="subject_erasure_requested",
        content_hash=None,
        payload={
            "job_id": str(job.id),
            "subject_ref": persisted_subject_ref,
            "request_ref": keyed_request_ref,
            "key_destroyed_at": destroyed_at.isoformat(),
            "snapshot_memory_count": memory_count,
            "snapshot_live_fact_count": live_fact_count,
            "snapshot_relationship_count": relationship_count,
            "snapshot_pending_admission_count": pending_count,
        },
    )
    await db.commit()
    evict_dek(namespace, persisted_subject_ref)
    if subject_id != persisted_subject_ref:
        evict_dek(namespace, subject_id)
    return job, False


async def get_subject_erasure_job(
    db: AsyncSession,
    *,
    namespace: str,
    job_id: UUID,
) -> SubjectErasureJob | None:
    return (
        await db.execute(
            select(SubjectErasureJob).where(
                SubjectErasureJob.id == job_id,
                SubjectErasureJob.namespace == namespace,
            )
        )
    ).scalar_one_or_none()


async def get_subject_erasure_job_for_subject(
    db: AsyncSession,
    *,
    namespace: str,
    subject_id: str,
) -> SubjectErasureJob | None:
    persisted_subject_ref = subject_reference(namespace, subject_id)
    if persisted_subject_ref is None:
        return None
    return (
        await db.execute(
            select(SubjectErasureJob).where(
                SubjectErasureJob.namespace == namespace,
                SubjectErasureJob.subject_ref == persisted_subject_ref,
            )
        )
    ).scalar_one_or_none()


async def retry_subject_erasure_job(
    db: AsyncSession,
    *,
    namespace: str,
    job_id: UUID,
) -> SubjectErasureJob | None:
    job = (
        await db.execute(
            select(SubjectErasureJob)
            .where(
                SubjectErasureJob.id == job_id,
                SubjectErasureJob.namespace == namespace,
            )
            .with_for_update()
        )
    ).scalar_one_or_none()
    if job is None:
        return None
    if job.status != "failed":
        return job
    job.status = "pending"
    job.failed_at = None
    job.failure_code = None
    job.consecutive_failures = 0
    job.last_error_code = None
    job.last_error_digest = None
    job.next_attempt_at = await _database_now(db)
    job.updated_at = job.next_attempt_at
    await db.commit()
    return job


async def erasure_certificate_dict(
    db: AsyncSession,
    *,
    job: SubjectErasureJob,
    limit: int,
    after_memory_id: UUID | None,
) -> dict:
    """Return an exact certificate plus one bounded evidence-hash page."""

    if job.status != "completed":
        raise SubjectErasureNotComplete(job)
    bounded_limit = max(1, min(500, limit))
    evidence_total = int(
        (
            await db.execute(
                select(func.count(SubjectErasureMemoryEvidence.memory_id)).where(
                    SubjectErasureMemoryEvidence.namespace == job.namespace,
                    SubjectErasureMemoryEvidence.job_id == job.id,
                )
            )
        ).scalar_one()
        or 0
    )
    if evidence_total != int(job.snapshot_memory_count):
        raise SubjectErasureInvariantError(
            "Completed erasure evidence count differs from its snapshot"
        )
    terminal_event = (
        await db.execute(
            select(EventLog).where(
                EventLog.id == job.completion_event_id,
                EventLog.namespace == job.namespace,
            )
        )
    ).scalar_one_or_none()
    if (
        terminal_event is None
        or terminal_event.op != "subject_erasure_completed"
        or terminal_event.row_hash != job.completion_event_hash
        or terminal_event.content_hash != job.manifest_sha256
        or dict(terminal_event.payload or {}).get("job_id") != str(job.id)
    ):
        raise SubjectErasureInvariantError(
            "Completed erasure terminal audit binding is unavailable"
        )
    conditions = [
        SubjectErasureMemoryEvidence.namespace == job.namespace,
        SubjectErasureMemoryEvidence.job_id == job.id,
    ]
    if after_memory_id is not None:
        conditions.append(SubjectErasureMemoryEvidence.memory_id > after_memory_id)
    rows = list(
        (
            await db.execute(
                select(SubjectErasureMemoryEvidence)
                .where(*conditions)
                .order_by(SubjectErasureMemoryEvidence.memory_id)
                .limit(bounded_limit + 1)
            )
        ).scalars()
    )
    has_more = len(rows) > bounded_limit
    page = rows[:bounded_limit]
    return {
        "certificate_id": uuid.uuid5(
            uuid.NAMESPACE_URL, f"lians-subject-erasure:{job.id}"
        ),
        "job_id": job.id,
        "namespace": job.namespace,
        "subject_ref": job.subject_ref,
        "request_ref": job.request_ref,
        "key_destroyed_at": job.key_destroyed_at,
        "completed_at": job.completed_at,
        "memories_erased": evidence_total,
        "live_facts_erased": int(job.live_facts_scrubbed),
        "relationships_erased": int(job.relationships_scrubbed),
        "pending_admissions_erased": int(job.pending_admissions_scrubbed),
        "manifest_sha256": job.manifest_sha256,
        "manifest_algorithm": "lians-subject-erasure-memory-manifest-v1",
        "evidence": [
            {"memory_id": row.memory_id, "content_hash": row.content_hash}
            for row in page
        ],
        "content_hashes": [row.content_hash for row in page],
        "hashes_returned": len(page),
        "hashes_total": evidence_total,
        "hashes_complete": after_memory_id is None and not has_more,
        "has_more": has_more,
        "next_memory_id": page[-1].memory_id if has_more and page else None,
        "audit_event_id": job.completion_event_id,
        "audit_row_hash": job.completion_event_hash,
        # Full-chain verification is a separately bounded operator workflow;
        # certificate reads never silently downgrade after hitting its byte cap.
        "chain_status": "unchecked",
        "generated_at": datetime.now(UTC),
    }


async def claim_due_subject_erasure_jobs(
    db: AsyncSession,
    *,
    worker_id: str,
    batch_size: int,
    lease_seconds: int,
) -> list[ClaimedSubjectErasureJob]:
    now = await _database_now(db)
    rows = list(
        (
            await db.execute(
                select(SubjectErasureJob)
                .where(
                    SubjectErasureJob.status.in_(("pending", "running")),
                    SubjectErasureJob.next_attempt_at <= now,
                    or_(
                        SubjectErasureJob.lease_owner.is_(None),
                        SubjectErasureJob.lease_expires_at <= now,
                    ),
                )
                .order_by(
                    SubjectErasureJob.next_attempt_at,
                    SubjectErasureJob.created_at,
                    SubjectErasureJob.id,
                )
                .limit(max(1, min(100, batch_size)))
                .with_for_update(skip_locked=db.get_bind().dialect.name == "postgresql")
            )
        ).scalars()
    )
    claims: list[ClaimedSubjectErasureJob] = []
    for job in rows:
        expired = bool(
            job.lease_owner is not None
            and job.lease_expires_at is not None
            and _utc(job.lease_expires_at) <= now
        )
        if expired:
            job.consecutive_failures += 1
            job.last_error_code = "lease_expired"
            job.last_error_digest = hashlib.sha256(b"lease_expired").hexdigest()
            if job.consecutive_failures >= job.attempt_limit:
                job.status = "failed"
                job.failure_code = "worker_attempt_limit_exhausted"
                job.failed_at = now
                job.lease_owner = None
                job.lease_expires_at = None
                continue
        job.status = "running"
        job.processing_attempts += 1
        job.last_attempt_at = now
        job.heartbeat_at = now
        job.lease_owner = worker_id
        job.lease_expires_at = now + timedelta(seconds=lease_seconds)
        job.updated_at = now
        if job.started_at is None:
            job.started_at = now
        claims.append(ClaimedSubjectErasureJob(job.id, job.namespace))
    await db.commit()
    return claims


def _bounded_page_statement(
    model,
    job: SubjectErasureJob,
    candidates: tuple[str, ...],
    cursor,
    maximum,
    page_size: int,
):
    conditions = [
        model.namespace == job.namespace,
        model.subject_id.in_(candidates),
        model.id <= maximum,
    ]
    if cursor is not None:
        conditions.append(model.id > cursor)
    return (
        select(model)
        .where(*conditions)
        .order_by(model.id)
        .limit(page_size)
        .with_for_update()
    )


def _advance_manifest(current: str, rows: list[Memory]) -> str:
    digest = bytes.fromhex(current)
    for row in rows:
        content_hash = str(row.content_hash or "").lower()
        if len(content_hash) != 64 or any(ch not in "0123456789abcdef" for ch in content_hash):
            raise SubjectErasureInvariantError("Memory has no canonical content hash")
        digest = hashlib.sha256(
            _MANIFEST_DOMAIN + b"\0" + digest + row.id.bytes + bytes.fromhex(content_hash)
        ).digest()
    return digest.hex()


def _assert_terminal_cursor(
    *,
    count: int,
    progress: int,
    cursor: UUID | None,
    maximum: UUID | None,
    label: str,
) -> None:
    if progress != count or (count > 0 and cursor != maximum):
        raise SubjectErasureInvariantError(
            f"{label} snapshot ended at a non-terminal cursor"
        )


async def _complete_job(db: AsyncSession, job: SubjectErasureJob, now: datetime) -> None:
    _assert_terminal_cursor(
        count=int(job.snapshot_memory_count),
        progress=int(job.memories_scrubbed),
        cursor=job.memory_cursor_id,
        maximum=job.snapshot_memory_max_id,
        label="Memory",
    )
    _assert_terminal_cursor(
        count=int(job.snapshot_live_fact_count),
        progress=int(job.live_facts_scrubbed),
        cursor=job.live_fact_cursor_id,
        maximum=job.snapshot_live_fact_max_id,
        label="Live-fact",
    )
    _assert_terminal_cursor(
        count=int(job.snapshot_relationship_count),
        progress=int(job.relationships_scrubbed),
        cursor=job.relationship_cursor_id,
        maximum=job.snapshot_relationship_max_id,
        label="Relationship",
    )
    _assert_terminal_cursor(
        count=int(job.snapshot_pending_admission_count),
        progress=int(job.pending_admissions_scrubbed),
        cursor=job.pending_admission_cursor_id,
        maximum=job.snapshot_pending_admission_max_id,
        label="Pending-admission",
    )
    evidence_count = int(
        (
            await db.execute(
                select(func.count(SubjectErasureMemoryEvidence.memory_id)).where(
                    SubjectErasureMemoryEvidence.namespace == job.namespace,
                    SubjectErasureMemoryEvidence.job_id == job.id,
                )
            )
        ).scalar_one()
        or 0
    )
    if evidence_count != int(job.snapshot_memory_count):
        raise SubjectErasureInvariantError("Erasure evidence is not exact")
    remaining_memory = int(
        (
            await db.execute(
                select(func.count(Memory.id)).where(
                    Memory.namespace == job.namespace,
                    Memory.subject_id == job.subject_ref,
                    Memory.id <= job.snapshot_memory_max_id,
                    Memory.erased_at.is_(None),
                )
            )
        ).scalar_one()
        or 0
    ) if job.snapshot_memory_max_id is not None else 0
    remaining_live = int(
        (
            await db.execute(
                select(func.count(LiveFact.id)).where(
                    LiveFact.namespace == job.namespace,
                    LiveFact.subject_id == job.subject_ref,
                    LiveFact.id <= job.snapshot_live_fact_max_id,
                )
            )
        ).scalar_one()
        or 0
    ) if job.snapshot_live_fact_max_id is not None else 0
    if remaining_memory or remaining_live:
        raise SubjectErasureInvariantError("Erasure derivative rows remain in snapshot")
    key_row = await db.get(SubjectKey, (job.namespace, job.subject_ref))
    # Legacy keys are zero-filled; an empty canonical tombstone is also valid.
    if (
        key_row is None
        or key_row.destroyed_at is None
        or any(bytes(key_row.enc_key or b""))
    ):
        raise SubjectErasureInvariantError("Subject-key tombstone is not destroyed")
    event = await chain_log(
        db,
        namespace=job.namespace,
        agent_id=job.queued_by_principal_ref,
        op="subject_erasure_completed",
        content_hash=job.manifest_sha256,
        payload={
            "job_id": str(job.id),
            "subject_ref": job.subject_ref,
            "request_ref": job.request_ref,
            "key_destroyed_at": _utc(job.key_destroyed_at).isoformat(),
            "memories_erased": int(job.memories_scrubbed),
            "live_facts_erased": int(job.live_facts_scrubbed),
            "relationships_erased": int(job.relationships_scrubbed),
            "pending_admissions_erased": int(job.pending_admissions_scrubbed),
            "manifest_sha256": job.manifest_sha256,
        },
    )
    from .webhook_service import MEMORY_ERASED, dispatch_event

    await dispatch_event(
        db,
        job.namespace,
        MEMORY_ERASED,
        {
            "job_id": str(job.id),
            "subject_ref": job.subject_ref,
            "request_ref": job.request_ref,
            "memories_erased": int(job.memories_scrubbed),
            "manifest_sha256": job.manifest_sha256,
        },
    )
    job.completion_event_id = event.id
    job.completion_event_hash = event.row_hash
    job.subject_locator_encrypted = None
    job.phase = "completed"
    job.status = "completed"
    job.completed_at = now
    job.lease_owner = None
    job.lease_expires_at = None
    job.heartbeat_at = now
    job.updated_at = now
    job.consecutive_failures = 0
    job.last_error_code = None
    job.last_error_digest = None


async def _advance_one_page(
    db: AsyncSession,
    *,
    claim: ClaimedSubjectErasureJob,
    worker_id: str,
    page_size: int,
    lease_seconds: int,
) -> bool:
    job = (
        await db.execute(
            select(SubjectErasureJob)
            .where(
                SubjectErasureJob.id == claim.job_id,
                SubjectErasureJob.namespace == claim.namespace,
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
        raise SubjectErasureLeaseConflict("Subject-erasure lease was lost")
    now = await _database_now(db)
    if _utc(job.lease_expires_at) <= now:
        raise SubjectErasureLeaseConflict("Subject-erasure lease expired")
    await lock_subject_key_for_update(db, job.subject_ref, job.namespace)
    candidates = _subject_candidates(job)

    while True:
        if job.phase == "memories":
            if job.memories_scrubbed == job.snapshot_memory_count:
                job.phase = "live_facts"
                continue
            rows = list(
                (
                    await db.execute(
                        _bounded_page_statement(
                            Memory,
                            job,
                            candidates,
                            job.memory_cursor_id,
                            job.snapshot_memory_max_id,
                            page_size,
                        )
                    )
                ).scalars()
            )
            if not rows:
                raise SubjectErasureInvariantError("Memory snapshot ended early")
            job.manifest_sha256 = _advance_manifest(job.manifest_sha256, rows)
            for memory in rows:
                db.add(
                    SubjectErasureMemoryEvidence(
                        job_id=job.id,
                        memory_id=memory.id,
                        namespace=job.namespace,
                        content_hash=memory.content_hash.lower(),
                        erased_at=now,
                    )
                )
                memory.content_encrypted = None
                memory.embedding = None
                memory.metadata_ = {}
                memory.source = None
                memory.subject_id = job.subject_ref
                memory.erased_at = now
            job.memory_cursor_id = rows[-1].id
            job.memories_scrubbed += len(rows)
            if job.memories_scrubbed > job.snapshot_memory_count:
                raise SubjectErasureInvariantError("Memory snapshot count was exceeded")
            if job.memories_scrubbed == job.snapshot_memory_count:
                job.phase = "live_facts"
            break

        if job.phase == "live_facts":
            if job.live_facts_scrubbed == job.snapshot_live_fact_count:
                job.phase = "relationships"
                continue
            rows = list(
                (
                    await db.execute(
                        _bounded_page_statement(
                            LiveFact,
                            job,
                            candidates,
                            job.live_fact_cursor_id,
                            job.snapshot_live_fact_max_id,
                            page_size,
                        )
                    )
                ).scalars()
            )
            if not rows:
                remaining = int(
                    (
                        await db.execute(
                            select(func.count(LiveFact.id)).where(
                                LiveFact.namespace == job.namespace,
                                LiveFact.subject_id.in_(candidates),
                                LiveFact.id <= job.snapshot_live_fact_max_id,
                            )
                        )
                    ).scalar_one()
                    or 0
                )
                if remaining:
                    raise SubjectErasureInvariantError(
                        "Live-fact snapshot ended before residual rows"
                    )
                # A concurrent retention/supersession transaction may already
                # have removed part of this derivative read model. Exact zero
                # residual is authoritative; no evidence row is needed for a
                # non-authoritative cache/read-model copy.
                job.live_facts_scrubbed = job.snapshot_live_fact_count
                job.live_fact_cursor_id = job.snapshot_live_fact_max_id
                job.phase = "relationships"
                continue
            job.live_fact_cursor_id = rows[-1].id
            job.live_facts_scrubbed += len(rows)
            for row in rows:
                await db.delete(row)
            if job.live_facts_scrubbed > job.snapshot_live_fact_count:
                raise SubjectErasureInvariantError("Live-fact snapshot count was exceeded")
            if job.live_facts_scrubbed == job.snapshot_live_fact_count:
                job.phase = "relationships"
            break

        if job.phase == "relationships":
            if job.relationships_scrubbed == job.snapshot_relationship_count:
                job.phase = "pending_admissions"
                continue
            rows = list(
                (
                    await db.execute(
                        _bounded_page_statement(
                            Relationship,
                            job,
                            candidates,
                            job.relationship_cursor_id,
                            job.snapshot_relationship_max_id,
                            page_size,
                        )
                    )
                ).scalars()
            )
            if not rows:
                raise SubjectErasureInvariantError("Relationship snapshot ended early")
            suffix = job.subject_ref.rsplit(":", 1)[-1]
            for edge in rows:
                edge.src_entity = f"erased:{suffix}:src"
                edge.rel_type = "erased"
                edge.dst_entity = f"erased:{suffix}:dst"
                edge.metadata_ = {}
                edge.source = None
                edge.subject_id = job.subject_ref
                edge.valid_to = edge.valid_to or now
            job.relationship_cursor_id = rows[-1].id
            job.relationships_scrubbed += len(rows)
            if job.relationships_scrubbed > job.snapshot_relationship_count:
                raise SubjectErasureInvariantError("Relationship snapshot count was exceeded")
            if job.relationships_scrubbed == job.snapshot_relationship_count:
                job.phase = "pending_admissions"
            break

        if job.phase == "pending_admissions":
            if job.pending_admissions_scrubbed == job.snapshot_pending_admission_count:
                job.phase = "finalizing"
                continue
            rows = list(
                (
                    await db.execute(
                        _bounded_page_statement(
                            PendingAdmission,
                            job,
                            candidates,
                            job.pending_admission_cursor_id,
                            job.snapshot_pending_admission_max_id,
                            page_size,
                        )
                    )
                ).scalars()
            )
            if not rows:
                raise SubjectErasureInvariantError("Pending-admission snapshot ended early")
            for pending in rows:
                pending.content = seal_text(
                    "[ERASED]",
                    purpose=PENDING_CONTENT_PURPOSE,
                    context=job.namespace,
                )
                pending.metadata_ = {}
                pending.source = None
                pending.subject_id = job.subject_ref
                if pending.status == "pending":
                    pending.status = "rejected"
                pending.resolved_at = pending.resolved_at or now
                pending.resolver_note = None
            job.pending_admission_cursor_id = rows[-1].id
            job.pending_admissions_scrubbed += len(rows)
            if job.pending_admissions_scrubbed > job.snapshot_pending_admission_count:
                raise SubjectErasureInvariantError(
                    "Pending-admission snapshot count was exceeded"
                )
            if job.pending_admissions_scrubbed == job.snapshot_pending_admission_count:
                job.phase = "finalizing"
            break

        if job.phase == "finalizing":
            await _complete_job(db, job, now)
            await db.commit()
            from .metrics import record_erase

            record_erase(job.namespace, int(job.memories_scrubbed))
            return True

        if job.phase in _TERMINAL_PHASES:
            return True
        raise SubjectErasureInvariantError("Subject-erasure phase is invalid")

    job.pages_completed += 1
    job.heartbeat_at = now
    job.updated_at = now
    job.lease_expires_at = now + timedelta(seconds=lease_seconds)
    job.consecutive_failures = 0
    job.last_error_code = None
    job.last_error_digest = None
    await db.commit()
    return False


async def _record_processing_failure(
    session_factory: async_sessionmaker[AsyncSession],
    *,
    claim: ClaimedSubjectErasureJob,
    worker_id: str,
    exc: BaseException,
) -> None:
    code, inherently_terminal = _classify_error(exc)
    if code == "lease_lost":
        return
    set_current_namespace(claim.namespace)
    set_current_barrier_group(None)
    async with session_factory() as db:
        job = (
            await db.execute(
                select(SubjectErasureJob)
                .where(
                    SubjectErasureJob.id == claim.job_id,
                    SubjectErasureJob.namespace == claim.namespace,
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
            job.failure_code = code if inherently_terminal else "worker_attempt_limit_exhausted"
            job.failed_at = now
        else:
            job.status = "pending"
            job.next_attempt_at = now + timedelta(
                seconds=_backoff_seconds(job.id, job.consecutive_failures)
            )
        await db.commit()


async def process_subject_erasure_job(
    session_factory: async_sessionmaker[AsyncSession],
    *,
    claim: ClaimedSubjectErasureJob,
    worker_id: str,
    page_size: int,
    max_pages: int,
    lease_seconds: int,
) -> None:
    set_current_namespace(claim.namespace)
    set_current_barrier_group(None)
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
                    select(SubjectErasureJob)
                    .where(
                        SubjectErasureJob.id == claim.job_id,
                        SubjectErasureJob.namespace == claim.namespace,
                    )
                    .with_for_update()
                )
            ).scalar_one_or_none()
            if job is not None and job.status == "running" and job.lease_owner == worker_id:
                job.status = "pending"
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


def validate_subject_erasure_worker_configuration(
    settings: Settings,
    *,
    production: bool,
) -> list[str]:
    errors: list[str] = []
    if production and not settings.subject_erasure_worker_enabled:
        errors.append("SUBJECT_ERASURE_WORKER_ENABLED must be true in production")
    if not 1 <= settings.subject_erasure_worker_batch_size <= 100:
        errors.append("SUBJECT_ERASURE_WORKER_BATCH_SIZE must be between 1 and 100")
    if not 1 <= settings.subject_erasure_worker_concurrency <= 32:
        errors.append("SUBJECT_ERASURE_WORKER_CONCURRENCY must be between 1 and 32")
    if not 30 <= settings.subject_erasure_worker_lease_seconds <= 3_600:
        errors.append("SUBJECT_ERASURE_WORKER_LEASE_SECONDS must be between 30 and 3600")
    if not 1 <= settings.subject_erasure_worker_page_size <= 500:
        errors.append("SUBJECT_ERASURE_WORKER_PAGE_SIZE must be between 1 and 500")
    if not 1 <= settings.subject_erasure_worker_max_pages_per_claim <= 20:
        errors.append(
            "SUBJECT_ERASURE_WORKER_MAX_PAGES_PER_CLAIM must be between 1 and 20"
        )
    if not (
        0 < settings.subject_erasure_worker_retry_base_seconds
        <= settings.subject_erasure_worker_retry_max_seconds
        <= 3_600
    ):
        errors.append("Subject-erasure retry delays must be positive, ordered, and <= 3600")
    if not 1 <= settings.subject_erasure_worker_max_attempts <= 100:
        errors.append("SUBJECT_ERASURE_WORKER_MAX_ATTEMPTS must be between 1 and 100")
    return errors


def subject_erasure_worker_status() -> tuple[bool, datetime | None]:
    settings = get_settings()
    heartbeat = _worker_last_heartbeat_at
    threshold = max(30.0, settings.subject_erasure_worker_poll_seconds * 5)
    healthy = bool(
        settings.subject_erasure_worker_enabled
        and _worker_last_iteration_healthy
        and heartbeat is not None
        and (datetime.now(UTC) - heartbeat).total_seconds() <= threshold
    )
    return healthy, heartbeat


def refresh_subject_erasure_worker_process_metrics() -> None:
    from .metrics import set_subject_erasure_worker_state

    healthy, heartbeat = subject_erasure_worker_status()
    set_subject_erasure_worker_state(
        enabled=get_settings().subject_erasure_worker_enabled,
        healthy=healthy,
        heartbeat_at=heartbeat,
    )


async def subject_erasure_inventory(db: AsyncSession) -> dict[str, object]:
    counts = {
        str(status): int(count)
        for status, count in (
            await db.execute(
                select(SubjectErasureJob.status, func.count(SubjectErasureJob.id)).group_by(
                    SubjectErasureJob.status
                )
            )
        ).all()
    }
    active = (
        await db.execute(
            select(
                func.coalesce(
                    func.sum(
                        SubjectErasureJob.memories_scrubbed
                        + SubjectErasureJob.live_facts_scrubbed
                        + SubjectErasureJob.relationships_scrubbed
                        + SubjectErasureJob.pending_admissions_scrubbed
                    ),
                    0,
                ),
                func.coalesce(
                    func.sum(
                        SubjectErasureJob.snapshot_memory_count
                        + SubjectErasureJob.snapshot_live_fact_count
                        + SubjectErasureJob.snapshot_relationship_count
                        + SubjectErasureJob.snapshot_pending_admission_count
                    ),
                    0,
                ),
                func.min(SubjectErasureJob.created_at),
            ).where(SubjectErasureJob.status.in_(("pending", "running")))
        )
    ).one()
    healthy, heartbeat = subject_erasure_worker_status()
    return {
        "counts": counts,
        "rows_scrubbed": int(active[0] or 0),
        "snapshot_rows": int(active[1] or 0),
        "oldest_active_at": active[2],
        "worker_enabled": get_settings().subject_erasure_worker_enabled,
        "worker_healthy": healthy,
        "worker_heartbeat_at": heartbeat,
    }


async def run_subject_erasure_worker(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    global _worker_last_heartbeat_at, _worker_last_iteration_healthy
    settings = get_settings()
    if not settings.subject_erasure_worker_enabled:
        return
    worker_id = f"subject-erasure:{uuid.uuid4()}"
    logger.info("Durable subject-erasure worker started")
    try:
        while True:
            _worker_last_heartbeat_at = datetime.now(UTC)
            try:
                set_current_namespace("__admin__")
                set_current_barrier_group(None)
                async with session_factory() as db:
                    claims = await claim_due_subject_erasure_jobs(
                        db,
                        worker_id=worker_id,
                        batch_size=min(
                            settings.subject_erasure_worker_batch_size,
                            settings.subject_erasure_worker_concurrency,
                        ),
                        lease_seconds=settings.subject_erasure_worker_lease_seconds,
                    )
                set_current_namespace(None)
                set_current_barrier_group(None)
                await asyncio.gather(
                    *(
                        process_subject_erasure_job(
                            session_factory,
                            claim=claim,
                            worker_id=worker_id,
                            page_size=settings.subject_erasure_worker_page_size,
                            max_pages=settings.subject_erasure_worker_max_pages_per_claim,
                            lease_seconds=settings.subject_erasure_worker_lease_seconds,
                        )
                        for claim in claims
                    )
                )
                _worker_last_iteration_healthy = True
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                _worker_last_iteration_healthy = False
                logger.warning(
                    "Subject-erasure worker poll failed",
                    extra={"error_digest": _error_digest(exc)[:16]},
                )
            finally:
                set_current_namespace(None)
                set_current_barrier_group(None)
                _worker_last_heartbeat_at = datetime.now(UTC)
            await asyncio.sleep(settings.subject_erasure_worker_poll_seconds)
    finally:
        set_current_namespace("__admin__")
        set_current_barrier_group(None)
        try:
            async with session_factory() as db:
                rows = list(
                    (
                        await db.execute(
                            select(SubjectErasureJob)
                            .where(SubjectErasureJob.lease_owner == worker_id)
                            .with_for_update(
                                skip_locked=db.get_bind().dialect.name == "postgresql"
                            )
                        )
                    ).scalars()
                )
                now = await _database_now(db)
                for job in rows:
                    job.status = "pending"
                    job.lease_owner = None
                    job.lease_expires_at = None
                    job.next_attempt_at = now
                    job.updated_at = now
                await db.commit()
        finally:
            set_current_namespace(None)
            set_current_barrier_group(None)
