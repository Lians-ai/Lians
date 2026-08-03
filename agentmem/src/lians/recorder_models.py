"""Persistence models for the provider-neutral Universal Recorder.

The recorder lives in a separate module so its storage contract can evolve
without coupling protocol ingestion to the core memory and decision models.
Import this module anywhere ``Base.metadata`` is assembled (the API router does
so at runtime; Alembic should import it for future autogeneration).
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy import (
    JSON,
    BigInteger,
    Boolean,
    CheckConstraint,
    Column,
    DateTime,
    ForeignKey,
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


class RecorderRun(Base):
    """A protocol-neutral execution boundary assembled from recorder events.

    ``barrier_scope`` is an internal, non-null companion to ``barrier_group``.
    PostgreSQL treats NULL values as distinct in unique constraints, so using a
    reserved unbarriered value or a one-way hash of the barrier prevents
    duplicate runs and user-chosen-name collisions while preserving the public
    information-barrier semantics on ``barrier_group``.
    """

    __tablename__ = "recorder_runs"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    namespace = Column(String, nullable=False, index=True)
    barrier_group = Column(String, nullable=True, index=True)
    barrier_scope = Column(String, nullable=False)

    correlation_type = Column(String(32), nullable=False)
    correlation_value = Column(String(512), nullable=False)
    correlation_hash = Column(String(64), nullable=False)
    boundary_kind = Column(String(32), nullable=False, default="run")

    agent_id = Column(String(255), nullable=True, index=True)
    subject_id = Column(String(512), nullable=True, index=True)
    session_id = Column(String(512), nullable=True, index=True)
    trace_id = Column(String(64), nullable=True, index=True)
    task_id = Column(String(512), nullable=True, index=True)
    # Correlation reference rather than a foreign key: instrumentation can
    # arrive before a DecisionRecord is promoted from the completed boundary.
    decision_id = Column(UUID(as_uuid=True), nullable=True, index=True)

    status = Column(String(32), nullable=False, default="open", index=True)
    first_occurred_at = Column(DateTime(timezone=True), nullable=False)
    last_occurred_at = Column(DateTime(timezone=True), nullable=False)
    first_recorded_at = Column(DateTime(timezone=True), nullable=False, default=_now)
    last_recorded_at = Column(DateTime(timezone=True), nullable=False, default=_now)
    ready_at = Column(DateTime(timezone=True), nullable=True, index=True)

    event_count = Column(Integer, nullable=False, default=0)
    protocols = Column(JSON, nullable=False, default=list)
    capture_state = Column(JSON, nullable=False, default=dict)
    readiness_score = Column(Integer, nullable=False, default=0)
    receipt_ready = Column(Boolean, nullable=False, default=False, index=True)
    completeness_gaps = Column(JSON, nullable=False, default=list)
    diagnostics = Column(JSON, nullable=False, default=list)
    extension_attributes = Column(JSON, nullable=False, default=dict)
    # Mutable aggregate of immutable event-level authenticated producers.
    ingested_by_principal_refs = Column(
        JSON,
        nullable=False,
        default=list,
        server_default="[]",
    )
    ingested_by_auth_methods = Column(
        JSON,
        nullable=False,
        default=list,
        server_default="[]",
    )

    created_at = Column(DateTime(timezone=True), nullable=False, default=_now)
    updated_at = Column(DateTime(timezone=True), nullable=False, default=_now, onupdate=_now)

    __table_args__ = (
        UniqueConstraint(
            "namespace",
            "barrier_scope",
            "correlation_hash",
            name="uq_recorder_run_scope_correlation",
        ),
        Index("ix_recorder_run_ns_updated", "namespace", "updated_at"),
        Index("ix_recorder_run_ns_trace", "namespace", "trace_id"),
        Index("ix_recorder_run_ns_task", "namespace", "task_id"),
    )


class RecorderEvent(Base):
    """An immutable, normalized event accepted by the Universal Recorder."""

    __tablename__ = "recorder_events"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    namespace = Column(String, nullable=False, index=True)
    run_id = Column(
        UUID(as_uuid=True),
        ForeignKey("recorder_runs.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    barrier_group = Column(String, nullable=True, index=True)
    barrier_scope = Column(String, nullable=False)

    schema_version = Column(String(32), nullable=False)
    protocol = Column(String(32), nullable=False, index=True)
    event_kind = Column(String(128), nullable=False, index=True)
    event_name = Column(String(512), nullable=True)
    phase = Column(String(32), nullable=False)
    status = Column(String(64), nullable=True)

    source_event_id = Column(String(512), nullable=True)
    dedup_key = Column(String(64), nullable=False)
    idempotency_key_hash = Column(String(64), nullable=True)
    source_payload_hash = Column(String(64), nullable=False)
    event_hash = Column(String(64), nullable=False, index=True)
    # The application always supplies v2. The v1 server default classifies
    # rolling 0.4.2 INSERTs, which cannot send authenticated provenance.
    event_hash_version = Column(Integer, nullable=False, default=2, server_default="1")

    occurred_at = Column(DateTime(timezone=True), nullable=False, index=True)
    recorded_at = Column(DateTime(timezone=True), nullable=False, default=_now, index=True)
    # Server-derived ingestion identity. Envelope actor fields remain claims.
    ingested_by_principal_ref = Column(
        String(512),
        nullable=False,
        index=True,
        server_default="lians:principal:v1:legacy-unverified",
    )
    ingested_by_auth_method = Column(
        String(64), nullable=False, server_default="legacy_unverified"
    )
    ingested_by_credential_id = Column(String(128), nullable=True)
    actor_attribution = Column(
        String(32),
        nullable=False,
        default="claimed_unverified",
        server_default="claimed_unverified",
    )
    agent_id = Column(String(255), nullable=True, index=True)
    subject_id = Column(String(512), nullable=True, index=True)
    session_id = Column(String(512), nullable=True, index=True)
    trace_id = Column(String(64), nullable=True, index=True)
    span_id = Column(String(64), nullable=True)
    parent_span_id = Column(String(64), nullable=True)
    task_id = Column(String(512), nullable=True, index=True)
    context_id = Column(String(512), nullable=True)
    message_id = Column(String(512), nullable=True)
    tool_call_id = Column(String(512), nullable=True)
    decision_id = Column(UUID(as_uuid=True), nullable=True, index=True)

    model_id = Column(String(512), nullable=True, index=True)
    model_version = Column(String(512), nullable=True)
    policy_version = Column(String(512), nullable=True)
    input_hash = Column(String(64), nullable=True)
    output_hash = Column(String(64), nullable=True)

    capture_mode = Column(String(32), nullable=False)
    normalized_payload = Column(JSON, nullable=False, default=dict)
    extension_attributes = Column(JSON, nullable=False, default=dict)
    capture_gaps = Column(JSON, nullable=False, default=list)
    diagnostics = Column(JSON, nullable=False, default=list)

    __table_args__ = (
        UniqueConstraint(
            "namespace",
            "barrier_scope",
            "dedup_key",
            name="uq_recorder_event_scope_dedup",
        ),
        CheckConstraint(
            "event_hash_version IN (1, 2)",
            name="ck_recorder_event_hash_version",
        ),
        CheckConstraint(
            "length(event_hash) = 64 AND length(source_payload_hash) = 64",
            name="ck_recorder_event_hash_lengths",
        ),
        CheckConstraint(
            "actor_attribution IN ('claimed_unverified', 'not_supplied')",
            name="ck_recorder_event_actor_attribution",
        ),
        Index("ix_recorder_event_run_time", "run_id", "occurred_at"),
        Index(
            "ix_recorder_event_run_page",
            "namespace",
            "run_id",
            "recorded_at",
            "id",
        ),
        Index("ix_recorder_event_ns_protocol_time", "namespace", "protocol", "occurred_at"),
        Index("ix_recorder_event_ns_trace_span", "namespace", "trace_id", "span_id"),
        Index("ix_recorder_event_ns_task", "namespace", "task_id"),
        Index(
            "ix_recorder_event_decision_snapshot",
            "namespace",
            "decision_id",
            "recorded_at",
            "id",
        ),
        Index("ix_recorder_events_capture_mode", "capture_mode"),
    )


class RecorderEvidenceIndexJob(Base):
    """Durable fixed-snapshot back-linking for a decision's prior Recorder events."""

    __tablename__ = "recorder_evidence_index_jobs"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    namespace = Column(String, nullable=False)
    barrier_group = Column(String, nullable=True)
    barrier_scope = Column(String(64), nullable=False)
    decision_id = Column(UUID(as_uuid=True), nullable=False)
    queued_by_principal_ref = Column(String(512), nullable=False)
    queued_by_auth_method = Column(String(64), nullable=False)

    status = Column(String(16), nullable=False, default="pending", server_default="pending")
    snapshot_max_recorded_at = Column(DateTime(timezone=True), nullable=False)
    snapshot_max_event_id = Column(UUID(as_uuid=True), nullable=False)
    snapshot_event_count = Column(BigInteger, nullable=False)
    cursor_recorded_at = Column(DateTime(timezone=True), nullable=True)
    cursor_event_id = Column(UUID(as_uuid=True), nullable=True)
    events_indexed = Column(BigInteger, nullable=False, default=0, server_default="0")
    artifacts_created = Column(BigInteger, nullable=False, default=0, server_default="0")
    links_created = Column(BigInteger, nullable=False, default=0, server_default="0")
    pages_completed = Column(Integer, nullable=False, default=0, server_default="0")

    processing_attempts = Column(Integer, nullable=False, default=0, server_default="0")
    consecutive_failures = Column(Integer, nullable=False, default=0, server_default="0")
    attempt_limit = Column(Integer, nullable=False, default=8, server_default="8")
    next_attempt_at = Column(DateTime(timezone=True), nullable=False, default=_now, server_default=func.now())
    lease_owner = Column(String(255), nullable=True)
    lease_expires_at = Column(DateTime(timezone=True), nullable=True)
    heartbeat_at = Column(DateTime(timezone=True), nullable=True)
    last_attempt_at = Column(DateTime(timezone=True), nullable=True)
    last_error_code = Column(String(64), nullable=True)
    last_error_digest = Column(String(64), nullable=True)
    failure_code = Column(String(64), nullable=True)
    created_at = Column(DateTime(timezone=True), nullable=False, default=_now, server_default=func.now())
    started_at = Column(DateTime(timezone=True), nullable=True)
    updated_at = Column(DateTime(timezone=True), nullable=False, default=_now, onupdate=_now, server_default=func.now())
    completed_at = Column(DateTime(timezone=True), nullable=True)
    failed_at = Column(DateTime(timezone=True), nullable=True)

    __table_args__ = (
        Index("ix_recorder_index_jobs_namespace", "namespace"),
        Index("ix_recorder_index_jobs_barrier_group", "barrier_group"),
        ForeignKeyConstraint(
            ["decision_id", "namespace"],
            ["decision_records.id", "decision_records.namespace"],
            ondelete="CASCADE",
            name="fk_recorder_index_job_decision_namespace",
        ),
        UniqueConstraint("id", "namespace", name="uq_recorder_index_job_id_namespace"),
        UniqueConstraint(
            "namespace",
            "decision_id",
            name="uq_recorder_index_job_decision",
        ),
        CheckConstraint(
            "status IN ('pending','running','completed','failed')",
            name="ck_recorder_index_job_status",
        ),
        CheckConstraint(
            "snapshot_event_count > 500 AND events_indexed >= 0 "
            "AND events_indexed <= snapshot_event_count "
            "AND artifacts_created >= 0 AND links_created >= 0 "
            "AND pages_completed >= 0",
            name="ck_recorder_index_job_progress",
        ),
        CheckConstraint(
            "(cursor_recorded_at IS NULL AND cursor_event_id IS NULL) OR "
            "(cursor_recorded_at IS NOT NULL AND cursor_event_id IS NOT NULL)",
            name="ck_recorder_index_job_cursor_pair",
        ),
        CheckConstraint(
            "processing_attempts >= 0 AND consecutive_failures >= 0 "
            "AND consecutive_failures <= processing_attempts "
            "AND attempt_limit >= 1 AND attempt_limit <= 100",
            name="ck_recorder_index_job_attempts",
        ),
        CheckConstraint(
            "(lease_owner IS NULL AND lease_expires_at IS NULL) OR "
            "(lease_owner IS NOT NULL AND lease_expires_at IS NOT NULL)",
            name="ck_recorder_index_job_lease_pair",
        ),
        CheckConstraint(
            "(last_error_code IS NULL AND last_error_digest IS NULL) OR "
            "(last_error_code IS NOT NULL AND last_error_digest IS NOT NULL "
            "AND length(last_error_digest) = 64)",
            name="ck_recorder_index_job_error_pair",
        ),
        CheckConstraint(
            "(status = 'completed' AND completed_at IS NOT NULL "
            "AND events_indexed = snapshot_event_count) OR "
            "(status <> 'completed' AND completed_at IS NULL)",
            name="ck_recorder_index_job_completion",
        ),
        CheckConstraint(
            "(status = 'failed' AND failed_at IS NOT NULL AND failure_code IS NOT NULL) "
            "OR (status <> 'failed' AND failed_at IS NULL AND failure_code IS NULL)",
            name="ck_recorder_index_job_failure",
        ),
        CheckConstraint(
            "status NOT IN ('completed','failed') OR "
            "(lease_owner IS NULL AND lease_expires_at IS NULL)",
            name="ck_recorder_index_job_terminal_lease",
        ),
        Index(
            "ix_recorder_index_job_claim",
            "status",
            "next_attempt_at",
            "lease_expires_at",
            "created_at",
            "id",
        ),
        Index(
            "ix_recorder_index_job_scope_status",
            "namespace",
            "barrier_scope",
            "status",
            "created_at",
            "id",
        ),
    )
