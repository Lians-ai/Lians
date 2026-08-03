"""Durable, bounded state for data-subject erasure workflows.

The request transaction destroys the subject DEK and freezes exact row counts.
Workers then scrub derivative stores in keyset pages.  The evidence table keeps
only immutable memory IDs and pre-existing content hashes, making certificates
pageable without scanning the audit log or returning an unbounded array.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    Column,
    DateTime,
    ForeignKeyConstraint,
    Index,
    Integer,
    String,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import UUID

from .db import Base


def _now() -> datetime:
    return datetime.now(timezone.utc)


class SubjectErasureJob(Base):
    """One idempotent, fixed-snapshot erasure workflow per tenant subject."""

    __tablename__ = "subject_erasure_jobs"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    namespace = Column(String, nullable=False)
    subject_ref = Column(String(192), nullable=False)
    request_ref = Column(String(192), nullable=False)
    # The raw lookup value is needed only to drain legacy rows that predate
    # subject-reference canonicalization. It is envelope-encrypted and is
    # permanently removed when the fixed snapshot completes.
    subject_locator_encrypted = Column(String(4096), nullable=True)
    queued_by_principal_ref = Column(String(512), nullable=False)
    queued_by_auth_method = Column(String(64), nullable=False)

    status = Column(String(16), nullable=False, default="pending", server_default="pending")
    phase = Column(String(32), nullable=False, default="memories", server_default="memories")
    key_destroyed_at = Column(DateTime(timezone=True), nullable=False)
    cache_fenced_at = Column(DateTime(timezone=True), nullable=False)

    snapshot_memory_count = Column(BigInteger, nullable=False, default=0, server_default="0")
    snapshot_memory_max_id = Column(UUID(as_uuid=True), nullable=True)
    memory_cursor_id = Column(UUID(as_uuid=True), nullable=True)
    memories_scrubbed = Column(BigInteger, nullable=False, default=0, server_default="0")

    snapshot_live_fact_count = Column(BigInteger, nullable=False, default=0, server_default="0")
    snapshot_live_fact_max_id = Column(UUID(as_uuid=True), nullable=True)
    live_fact_cursor_id = Column(UUID(as_uuid=True), nullable=True)
    live_facts_scrubbed = Column(BigInteger, nullable=False, default=0, server_default="0")

    snapshot_relationship_count = Column(BigInteger, nullable=False, default=0, server_default="0")
    snapshot_relationship_max_id = Column(UUID(as_uuid=True), nullable=True)
    relationship_cursor_id = Column(UUID(as_uuid=True), nullable=True)
    relationships_scrubbed = Column(BigInteger, nullable=False, default=0, server_default="0")

    snapshot_pending_admission_count = Column(
        BigInteger, nullable=False, default=0, server_default="0"
    )
    snapshot_pending_admission_max_id = Column(UUID(as_uuid=True), nullable=True)
    pending_admission_cursor_id = Column(UUID(as_uuid=True), nullable=True)
    pending_admissions_scrubbed = Column(
        BigInteger, nullable=False, default=0, server_default="0"
    )

    pages_completed = Column(Integer, nullable=False, default=0, server_default="0")
    manifest_sha256 = Column(String(64), nullable=False)
    completion_event_id = Column(UUID(as_uuid=True), nullable=True)
    completion_event_hash = Column(String(64), nullable=True)

    processing_attempts = Column(Integer, nullable=False, default=0, server_default="0")
    consecutive_failures = Column(Integer, nullable=False, default=0, server_default="0")
    attempt_limit = Column(Integer, nullable=False, default=8, server_default="8")
    next_attempt_at = Column(
        DateTime(timezone=True), nullable=False, default=_now, server_default=func.now()
    )
    lease_owner = Column(String(255), nullable=True)
    lease_expires_at = Column(DateTime(timezone=True), nullable=True)
    heartbeat_at = Column(DateTime(timezone=True), nullable=True)
    last_attempt_at = Column(DateTime(timezone=True), nullable=True)
    last_error_code = Column(String(64), nullable=True)
    last_error_digest = Column(String(64), nullable=True)
    failure_code = Column(String(64), nullable=True)

    created_at = Column(
        DateTime(timezone=True), nullable=False, default=_now, server_default=func.now()
    )
    started_at = Column(DateTime(timezone=True), nullable=True)
    updated_at = Column(
        DateTime(timezone=True),
        nullable=False,
        default=_now,
        onupdate=_now,
        server_default=func.now(),
    )
    completed_at = Column(DateTime(timezone=True), nullable=True)
    failed_at = Column(DateTime(timezone=True), nullable=True)

    __table_args__ = (
        Index("ix_subject_erasure_job_namespace", "namespace"),
        UniqueConstraint("id", "namespace", name="uq_subject_erasure_job_id_namespace"),
        UniqueConstraint("namespace", "subject_ref", name="uq_subject_erasure_job_subject"),
        CheckConstraint(
            "status IN ('pending','running','completed','failed')",
            name="ck_subject_erasure_job_status",
        ),
        CheckConstraint(
            "phase IN ('memories','live_facts','relationships',"
            "'pending_admissions','finalizing','completed')",
            name="ck_subject_erasure_job_phase",
        ),
        CheckConstraint(
            "snapshot_memory_count >= 0 AND memories_scrubbed >= 0 "
            "AND memories_scrubbed <= snapshot_memory_count "
            "AND snapshot_live_fact_count >= 0 AND live_facts_scrubbed >= 0 "
            "AND live_facts_scrubbed <= snapshot_live_fact_count "
            "AND snapshot_relationship_count >= 0 AND relationships_scrubbed >= 0 "
            "AND relationships_scrubbed <= snapshot_relationship_count "
            "AND snapshot_pending_admission_count >= 0 "
            "AND pending_admissions_scrubbed >= 0 "
            "AND pending_admissions_scrubbed <= snapshot_pending_admission_count "
            "AND pages_completed >= 0",
            name="ck_subject_erasure_job_progress",
        ),
        CheckConstraint(
            "((snapshot_memory_count = 0 AND snapshot_memory_max_id IS NULL) OR "
            "(snapshot_memory_count > 0 AND snapshot_memory_max_id IS NOT NULL)) AND "
            "((snapshot_live_fact_count = 0 AND snapshot_live_fact_max_id IS NULL) OR "
            "(snapshot_live_fact_count > 0 AND snapshot_live_fact_max_id IS NOT NULL)) AND "
            "((snapshot_relationship_count = 0 AND snapshot_relationship_max_id IS NULL) OR "
            "(snapshot_relationship_count > 0 AND snapshot_relationship_max_id IS NOT NULL)) "
            "AND ((snapshot_pending_admission_count = 0 "
            "AND snapshot_pending_admission_max_id IS NULL) OR "
            "(snapshot_pending_admission_count > 0 "
            "AND snapshot_pending_admission_max_id IS NOT NULL))",
            name="ck_subject_erasure_job_snapshot_bounds",
        ),
        CheckConstraint(
            "processing_attempts >= 0 AND consecutive_failures >= 0 "
            "AND consecutive_failures <= processing_attempts "
            "AND attempt_limit BETWEEN 1 AND 100",
            name="ck_subject_erasure_job_attempts",
        ),
        CheckConstraint(
            "(lease_owner IS NULL AND lease_expires_at IS NULL) OR "
            "(lease_owner IS NOT NULL AND lease_expires_at IS NOT NULL)",
            name="ck_subject_erasure_job_lease_pair",
        ),
        CheckConstraint(
            "(last_error_code IS NULL AND last_error_digest IS NULL) OR "
            "(last_error_code IS NOT NULL AND last_error_digest IS NOT NULL "
            "AND length(last_error_digest) = 64)",
            name="ck_subject_erasure_job_error_pair",
        ),
        CheckConstraint(
            "length(manifest_sha256) = 64",
            name="ck_subject_erasure_job_manifest_hash",
        ),
        CheckConstraint(
            "cache_fenced_at <= key_destroyed_at",
            name="ck_subject_erasure_job_privacy_boundary_order",
        ),
        CheckConstraint(
            "(completion_event_id IS NULL AND completion_event_hash IS NULL) OR "
            "(completion_event_id IS NOT NULL AND completion_event_hash IS NOT NULL "
            "AND length(completion_event_hash) = 64)",
            name="ck_subject_erasure_job_completion_event_pair",
        ),
        CheckConstraint(
            "(status = 'completed' AND completed_at IS NOT NULL "
            "AND phase = 'completed' AND subject_locator_encrypted IS NULL "
            "AND completion_event_id IS NOT NULL) OR "
            "(status <> 'completed' AND completed_at IS NULL "
            "AND phase <> 'completed' AND subject_locator_encrypted IS NOT NULL)",
            name="ck_subject_erasure_job_completion",
        ),
        CheckConstraint(
            "(status = 'failed' AND failed_at IS NOT NULL AND failure_code IS NOT NULL) "
            "OR (status <> 'failed' AND failed_at IS NULL AND failure_code IS NULL)",
            name="ck_subject_erasure_job_failure",
        ),
        CheckConstraint(
            "status NOT IN ('completed','failed') OR "
            "(lease_owner IS NULL AND lease_expires_at IS NULL)",
            name="ck_subject_erasure_job_terminal_lease",
        ),
        Index(
            "ix_subject_erasure_job_claim",
            "status",
            "next_attempt_at",
            "lease_expires_at",
            "created_at",
            "id",
        ),
        Index(
            "ix_subject_erasure_job_namespace_status",
            "namespace",
            "status",
            "created_at",
            "id",
        ),
    )


class SubjectErasureMemoryEvidence(Base):
    """Immutable, keyset-pageable hash evidence for one erased memory."""

    __tablename__ = "subject_erasure_memory_evidence"

    job_id = Column(UUID(as_uuid=True), primary_key=True)
    memory_id = Column(UUID(as_uuid=True), primary_key=True)
    namespace = Column(String, nullable=False)
    content_hash = Column(String(64), nullable=False)
    erased_at = Column(DateTime(timezone=True), nullable=False, default=_now)

    __table_args__ = (
        ForeignKeyConstraint(
            ["job_id", "namespace"],
            ["subject_erasure_jobs.id", "subject_erasure_jobs.namespace"],
            ondelete="RESTRICT",
            name="fk_subject_erasure_evidence_job_namespace",
        ),
        CheckConstraint(
            "length(content_hash) = 64 AND content_hash = lower(content_hash)",
            name="ck_subject_erasure_evidence_content_hash",
        ),
        Index(
            "ix_subject_erasure_evidence_page",
            "namespace",
            "job_id",
            "memory_id",
        ),
    )
