"""Normalized evidence graph persistence models.

The evidence graph is deliberately separate from the legacy JSON fields on
``DecisionRecord``.  Artifacts are immutable, version-addressable nodes; links
record whether a decision used an artifact directly or can reach it through a
declared dependency.  Namespace and information-barrier columns are repeated
on both tables so every query can enforce tenant isolation without trusting an
ORM relationship traversal.
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
    ForeignKeyConstraint,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import UUID

from .db import Base

EVIDENCE_ARTIFACT_KINDS = (
    "source",
    "policy",
    "model",
    "tool",
    "permission",
    "instruction",
    "input",
    "output",
)
EVIDENCE_RELATIONS = ("direct", "reachable")
EVIDENCE_COVERAGE_STATUSES = ("unknown", "partial", "complete")
IMPACT_ASSESSMENT_STATUSES = ("pending", "running", "completed", "failed")


def _now() -> datetime:
    return datetime.now(timezone.utc)


class EvidenceArtifact(Base):
    """An immutable dependency or payload addressable by identity/version/hash."""

    __tablename__ = "evidence_artifacts"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    namespace = Column(String, nullable=False, index=True)
    barrier_group = Column(String, nullable=True, index=True)
    kind = Column(String(32), nullable=False, index=True)
    identifier = Column(String(1024), nullable=False)
    identifier_normalized = Column(Text, nullable=False)
    identifier_lookup_hash = Column(String(64), nullable=False)
    version = Column(String(512), nullable=True)
    version_normalized = Column(Text, nullable=True)
    version_lookup_hash = Column(String(64), nullable=True)
    coordinate = Column(Text, nullable=False)
    coordinate_lookup_hash = Column(String(64), nullable=False)
    hash_algorithm = Column(
        String(32),
        nullable=False,
        default="sha256",
        server_default="sha256",
    )
    artifact_hash = Column(String(256), nullable=True)
    identity_hash = Column(String(64), nullable=False)
    metadata_ = Column("metadata", JSON, nullable=False, server_default="{}")
    risk_metadata = Column(JSON, nullable=False, server_default="{}")
    created_by_agent_id = Column(String(255), nullable=True)
    recorded_at = Column(
        DateTime(timezone=True),
        nullable=False,
        default=_now,
        server_default=func.now(),
    )

    __table_args__ = (
        CheckConstraint(
            "kind IN ('source','policy','model','tool','permission',"
            "'instruction','input','output')",
            name="ck_evidence_artifact_kind",
        ),
        UniqueConstraint(
            "namespace",
            "identity_hash",
            name="uq_evidence_artifact_namespace_identity",
        ),
        UniqueConstraint(
            "id",
            "namespace",
            name="uq_evidence_artifact_id_namespace",
        ),
        Index(
            "ix_evidence_artifact_identifier",
            "namespace",
            "kind",
            "identifier_lookup_hash",
        ),
        Index(
            "ix_evidence_artifact_version",
            "namespace",
            "kind",
            "version_lookup_hash",
        ),
        Index(
            "ix_evidence_artifact_coordinate",
            "namespace",
            "kind",
            "coordinate_lookup_hash",
        ),
        Index(
            "ix_evidence_artifact_hash",
            "namespace",
            "kind",
            "artifact_hash",
        ),
        Index(
            "ix_evidence_artifact_recent",
            "namespace",
            "recorded_at",
        ),
        Index(
            "ix_evidence_artifact_scope_page",
            "namespace",
            "barrier_group",
            "recorded_at",
            "id",
        ),
    )


class DecisionEvidenceLink(Base):
    """A namespace-safe edge from a decision to an evidence artifact."""

    __tablename__ = "decision_evidence_links"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    namespace = Column(String, nullable=False, index=True)
    barrier_group = Column(String, nullable=True, index=True)
    decision_id = Column(UUID(as_uuid=True), nullable=False)
    artifact_id = Column(UUID(as_uuid=True), nullable=False)
    relation = Column(String(16), nullable=False)
    match_basis = Column(JSON, nullable=False, server_default="[]")
    risk_metadata = Column(JSON, nullable=False, server_default="{}")
    risk_score = Column(Integer, nullable=True)
    risk_level = Column(String(16), nullable=True)
    recorded_at = Column(
        DateTime(timezone=True),
        nullable=False,
        default=_now,
        server_default=func.now(),
    )

    __table_args__ = (
        ForeignKeyConstraint(
            ["decision_id", "namespace"],
            ["decision_records.id", "decision_records.namespace"],
            ondelete="CASCADE",
            name="fk_decision_evidence_link_decision_namespace",
        ),
        ForeignKeyConstraint(
            ["artifact_id", "namespace"],
            ["evidence_artifacts.id", "evidence_artifacts.namespace"],
            ondelete="RESTRICT",
            name="fk_decision_evidence_link_artifact_namespace",
        ),
        UniqueConstraint(
            "id",
            "namespace",
            name="uq_decision_evidence_link_id_namespace",
        ),
        CheckConstraint(
            "relation IN ('direct','reachable')",
            name="ck_decision_evidence_relation",
        ),
        CheckConstraint(
            "risk_score IS NULL OR (risk_score >= 0 AND risk_score <= 100)",
            name="ck_decision_evidence_risk_score",
        ),
        CheckConstraint(
            "risk_level IS NULL OR risk_level IN ('critical','high','medium','low')",
            name="ck_decision_evidence_risk_level",
        ),
        UniqueConstraint(
            "namespace",
            "decision_id",
            "artifact_id",
            "relation",
            name="uq_decision_evidence_edge",
        ),
        Index(
            "ix_decision_evidence_impact",
            "namespace",
            "artifact_id",
            "relation",
            "risk_score",
            "decision_id",
        ),
        Index(
            "ix_decision_evidence_graph",
            "namespace",
            "decision_id",
            "relation",
        ),
    )


class DecisionEvidenceLinkRegistration(Base):
    """Monotonic append watermark for one immutable decision-evidence link."""

    __tablename__ = "decision_evidence_link_registrations"

    sequence = Column(
        BigInteger().with_variant(Integer, "sqlite"),
        primary_key=True,
        autoincrement=True,
    )
    namespace = Column(String, nullable=False, index=True)
    barrier_group = Column(String, nullable=True, index=True)
    link_id = Column(UUID(as_uuid=True), nullable=False, unique=True)
    registered_at = Column(
        DateTime(timezone=True),
        nullable=False,
        default=_now,
        server_default=func.now(),
    )

    __table_args__ = (
        ForeignKeyConstraint(
            ["link_id", "namespace"],
            ["decision_evidence_links.id", "decision_evidence_links.namespace"],
            ondelete="CASCADE",
            name="fk_evidence_link_registration_link_namespace",
        ),
        Index(
            "ix_decision_evidence_link_registration_scan",
            "namespace",
            "sequence",
            "link_id",
        ),
    )


class DecisionEvidenceCoverageSet(Base):
    """Monotonic registration boundary for one DecisionRecord's coverage."""

    __tablename__ = "decision_evidence_coverage_sets"

    sequence = Column(
        BigInteger().with_variant(Integer, "sqlite"),
        primary_key=True,
        autoincrement=True,
    )
    namespace = Column(String, nullable=False, index=True)
    barrier_group = Column(String, nullable=True, index=True)
    decision_id = Column(UUID(as_uuid=True), nullable=False)
    registered_at = Column(
        DateTime(timezone=True),
        nullable=False,
        default=_now,
        server_default=func.now(),
    )

    __table_args__ = (
        ForeignKeyConstraint(
            ["decision_id", "namespace"],
            ["decision_records.id", "decision_records.namespace"],
            ondelete="CASCADE",
            name="fk_evidence_coverage_set_decision_namespace",
        ),
        UniqueConstraint(
            "sequence",
            "namespace",
            name="uq_evidence_coverage_set_sequence_namespace",
        ),
        UniqueConstraint(
            "namespace",
            "decision_id",
            name="uq_decision_evidence_coverage_set",
        ),
        Index(
            "ix_decision_evidence_coverage_scan",
            "namespace",
            "sequence",
            "decision_id",
        ),
    )


class DecisionEvidenceKindCoverage(Base):
    """Persisted normalization completeness for one decision and artifact kind."""

    __tablename__ = "decision_evidence_kind_coverage"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    coverage_set_sequence = Column(
        BigInteger().with_variant(Integer, "sqlite"), nullable=False
    )
    namespace = Column(String, nullable=False, index=True)
    barrier_group = Column(String, nullable=True, index=True)
    decision_id = Column(UUID(as_uuid=True), nullable=False)
    kind = Column(String(32), nullable=False)
    status = Column(String(16), nullable=False)
    indexer_version = Column(String(64), nullable=False)
    normalization_scope = Column(String(64), nullable=False)
    source_watermark = Column(String(64), nullable=True)
    gap_codes = Column(JSON, nullable=False, default=list, server_default="[]")
    indexed_artifact_count = Column(Integer, nullable=False, default=0, server_default="0")
    assessed_at = Column(DateTime(timezone=True), nullable=True)
    created_at = Column(
        DateTime(timezone=True),
        nullable=False,
        default=_now,
        server_default=func.now(),
    )
    updated_at = Column(
        DateTime(timezone=True),
        nullable=False,
        default=_now,
        onupdate=_now,
        server_default=func.now(),
    )

    __table_args__ = (
        ForeignKeyConstraint(
            ["coverage_set_sequence", "namespace"],
            [
                "decision_evidence_coverage_sets.sequence",
                "decision_evidence_coverage_sets.namespace",
            ],
            ondelete="CASCADE",
            name="fk_evidence_kind_coverage_set_namespace",
        ),
        ForeignKeyConstraint(
            ["decision_id", "namespace"],
            ["decision_records.id", "decision_records.namespace"],
            ondelete="CASCADE",
            name="fk_evidence_kind_coverage_decision_namespace",
        ),
        CheckConstraint(
            "kind IN ('source','policy','model','tool','permission',"
            "'instruction','input','output')",
            name="ck_decision_evidence_coverage_kind",
        ),
        CheckConstraint(
            "status IN ('unknown','partial','complete')",
            name="ck_decision_evidence_coverage_status",
        ),
        CheckConstraint(
            "source_watermark IS NULL OR length(source_watermark) = 64",
            name="ck_decision_evidence_coverage_watermark",
        ),
        CheckConstraint(
            "(status = 'unknown' AND source_watermark IS NULL "
            "AND assessed_at IS NULL) OR "
            "(status IN ('partial','complete') "
            "AND source_watermark IS NOT NULL AND assessed_at IS NOT NULL)",
            name="ck_decision_evidence_coverage_assessment_state",
        ),
        CheckConstraint(
            "indexed_artifact_count >= 0",
            name="ck_decision_evidence_coverage_artifact_count",
        ),
        CheckConstraint(
            "json_array_length(gap_codes) <= 32",
            name="ck_decision_evidence_coverage_gap_bound",
        ),
        UniqueConstraint(
            "namespace",
            "decision_id",
            "kind",
            name="uq_decision_evidence_kind_coverage",
        ),
        Index(
            "ix_decision_evidence_coverage_kind_status",
            "namespace",
            "kind",
            "status",
            "decision_id",
        ),
        Index(
            "ix_decision_evidence_coverage_global_status",
            "status",
            "namespace",
            "decision_id",
            "kind",
        ),
        Index(
            "ix_decision_evidence_coverage_set_kind",
            "coverage_set_sequence",
            "kind",
        ),
    )


class DecisionImpactAssessmentJob(Base):
    """Durable, resumable exhaustive impact scan over a fixed decision snapshot."""

    __tablename__ = "decision_impact_assessment_jobs"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    namespace = Column(String, nullable=False, index=True)
    # This is the requester's exact barrier context, not the looser decision
    # visibility rule where unbarriered decisions are shared downward.
    barrier_group = Column(String, nullable=True, index=True)
    barrier_scope = Column(String(64), nullable=False)
    idempotency_key_hash = Column(String(64), nullable=False)
    request_fingerprint = Column(String(64), nullable=False)
    dependency_kind = Column(String(32), nullable=False)
    dependency_value = Column(String(1537), nullable=False)
    dependency_lookup_hash = Column(String(64), nullable=False)
    change_type = Column(String(32), nullable=False)
    change_occurred_at = Column(DateTime(timezone=True), nullable=True)
    note = Column(String(2000), nullable=True)
    requested_by_principal_ref = Column(String(512), nullable=False)
    requested_by_auth_method = Column(String(64), nullable=False)
    status = Column(String(16), nullable=False, default="pending", server_default="pending")
    snapshot_max_coverage_sequence = Column(BigInteger, nullable=False)
    snapshot_decision_count = Column(
        BigInteger, nullable=False, default=0, server_default="0"
    )
    cursor_coverage_sequence = Column(BigInteger, nullable=False, default=0, server_default="0")
    decisions_scanned = Column(BigInteger, nullable=False, default=0, server_default="0")
    fallback_candidates_scanned = Column(
        BigInteger, nullable=False, default=0, server_default="0"
    )
    indexed_decisions_matched = Column(
        BigInteger, nullable=False, default=0, server_default="0"
    )
    legacy_decisions_matched = Column(
        BigInteger, nullable=False, default=0, server_default="0"
    )
    matches_found = Column(BigInteger, nullable=False, default=0, server_default="0")
    direct_count = Column(BigInteger, nullable=False, default=0, server_default="0")
    reachable_count = Column(BigInteger, nullable=False, default=0, server_default="0")
    pages_completed = Column(Integer, nullable=False, default=0, server_default="0")
    record_event = Column(Boolean, nullable=False, default=True, server_default="true")
    snapshot_max_link_sequence = Column(BigInteger, nullable=False)
    completion_event_id = Column(UUID(as_uuid=True), nullable=True)
    failure_code = Column(String(128), nullable=True)
    # Autonomous processing is leased from the durable queue.  The lease is
    # deliberately advisory at the application layer but enforced by a row
    # lock whenever work or results are committed, so two replicas can never
    # advance one cursor concurrently.
    processing_attempts = Column(Integer, nullable=False, default=0, server_default="0")
    consecutive_failures = Column(
        Integer, nullable=False, default=0, server_default="0"
    )
    attempt_limit = Column(Integer, nullable=False, default=8, server_default="8")
    next_attempt_at = Column(
        DateTime(timezone=True),
        nullable=False,
        default=_now,
        server_default=func.now(),
    )
    lease_owner = Column(String(255), nullable=True)
    lease_expires_at = Column(DateTime(timezone=True), nullable=True)
    heartbeat_at = Column(DateTime(timezone=True), nullable=True)
    last_attempt_at = Column(DateTime(timezone=True), nullable=True)
    last_error_code = Column(String(64), nullable=True)
    last_error_digest = Column(String(64), nullable=True)
    created_at = Column(
        DateTime(timezone=True),
        nullable=False,
        default=_now,
        server_default=func.now(),
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
        ForeignKeyConstraint(
            ["completion_event_id", "namespace"],
            ["ledger_events.id", "ledger_events.namespace"],
            ondelete="RESTRICT",
            name="fk_impact_job_completion_event_namespace",
        ),
        UniqueConstraint(
            "id",
            "namespace",
            name="uq_impact_job_id_namespace",
        ),
        CheckConstraint(
            "dependency_kind IN ('source','policy','model','tool','permission',"
            "'instruction','input','output')",
            name="ck_impact_assessment_dependency_kind",
        ),
        CheckConstraint(
            "status IN ('pending','running','completed','failed')",
            name="ck_impact_assessment_status",
        ),
        CheckConstraint(
            "snapshot_max_coverage_sequence >= 0 "
            "AND snapshot_max_link_sequence >= 0 "
            "AND cursor_coverage_sequence >= 0 "
            "AND cursor_coverage_sequence <= snapshot_max_coverage_sequence",
            name="ck_impact_assessment_cursors",
        ),
        CheckConstraint(
            "decisions_scanned >= 0 AND fallback_candidates_scanned >= 0 "
            "AND indexed_decisions_matched >= 0 "
            "AND legacy_decisions_matched >= 0 AND matches_found >= 0 "
            "AND direct_count >= 0 AND reachable_count >= 0 "
            "AND pages_completed >= 0",
            name="ck_impact_assessment_counts",
        ),
        CheckConstraint(
            "fallback_candidates_scanned <= decisions_scanned",
            name="ck_impact_assessment_fallback_count",
        ),
        CheckConstraint(
            "snapshot_decision_count >= 0",
            name="ck_impact_assessment_snapshot_decision_count",
        ),
        CheckConstraint(
            "decisions_scanned <= snapshot_decision_count",
            name="ck_impact_assessment_scan_within_snapshot",
        ),
        CheckConstraint(
            "status <> 'completed' OR decisions_scanned = snapshot_decision_count",
            name="ck_impact_assessment_completed_snapshot_count",
        ),
        CheckConstraint(
            "processing_attempts >= 0 AND consecutive_failures >= 0 "
            "AND consecutive_failures <= processing_attempts "
            "AND attempt_limit >= 1 AND attempt_limit <= 100",
            name="ck_impact_assessment_attempts",
        ),
        CheckConstraint(
            "(lease_owner IS NULL AND lease_expires_at IS NULL) OR "
            "(lease_owner IS NOT NULL AND lease_expires_at IS NOT NULL)",
            name="ck_impact_assessment_lease_pair",
        ),
        CheckConstraint(
            "(last_error_code IS NULL AND last_error_digest IS NULL) OR "
            "(last_error_code IS NOT NULL AND last_error_digest IS NOT NULL "
            "AND length(last_error_digest) = 64)",
            name="ck_impact_assessment_error_pair",
        ),
        CheckConstraint(
            "(status = 'failed' AND failed_at IS NOT NULL "
            "AND failure_code IS NOT NULL) OR "
            "(status <> 'failed' AND failed_at IS NULL)",
            name="ck_impact_assessment_failure_state",
        ),
        CheckConstraint(
            "status NOT IN ('completed','failed') OR "
            "(lease_owner IS NULL AND lease_expires_at IS NULL)",
            name="ck_impact_assessment_terminal_lease",
        ),
        UniqueConstraint(
            "namespace",
            "barrier_scope",
            "idempotency_key_hash",
            name="uq_impact_assessment_idempotency",
        ),
        Index(
            "ix_impact_assessment_queue",
            "namespace",
            "barrier_scope",
            "status",
            "created_at",
        ),
        Index(
            "ix_impact_assessment_status_created",
            "status",
            "created_at",
        ),
        Index(
            "ix_impact_assessment_worker_claim",
            "status",
            "next_attempt_at",
            "lease_expires_at",
            "created_at",
        ),
    )


class DecisionImpactAssessmentMatch(Base):
    """One idempotent decision match produced by an exhaustive assessment."""

    __tablename__ = "decision_impact_assessment_matches"

    sequence = Column(
        BigInteger().with_variant(Integer, "sqlite"),
        primary_key=True,
        autoincrement=True,
    )
    namespace = Column(String, nullable=False, index=True)
    job_id = Column(UUID(as_uuid=True), nullable=False)
    # Exact job visibility boundary; see DecisionImpactAssessmentJob.
    job_barrier_group = Column(String, nullable=True, index=True)
    decision_id = Column(UUID(as_uuid=True), nullable=False)
    impact_status = Column(String(32), nullable=False)
    match_basis = Column(JSON, nullable=False, default=list, server_default="[]")
    match_sources = Column(JSON, nullable=False, default=list, server_default="[]")
    risk_score = Column(Integer, nullable=False)
    risk_level = Column(String(16), nullable=False)
    created_at = Column(
        DateTime(timezone=True),
        nullable=False,
        default=_now,
        server_default=func.now(),
    )
    updated_at = Column(
        DateTime(timezone=True),
        nullable=False,
        default=_now,
        onupdate=_now,
        server_default=func.now(),
    )

    __table_args__ = (
        ForeignKeyConstraint(
            ["job_id", "namespace"],
            ["decision_impact_assessment_jobs.id", "decision_impact_assessment_jobs.namespace"],
            ondelete="CASCADE",
            name="fk_impact_match_job_namespace",
        ),
        ForeignKeyConstraint(
            ["decision_id", "namespace"],
            ["decision_records.id", "decision_records.namespace"],
            ondelete="RESTRICT",
            name="fk_impact_match_decision_namespace",
        ),
        CheckConstraint(
            "impact_status IN ('direct_reference','reachable')",
            name="ck_impact_assessment_match_status",
        ),
        CheckConstraint(
            "risk_score >= 0 AND risk_score <= 100",
            name="ck_impact_assessment_match_risk_score",
        ),
        CheckConstraint(
            "risk_level IN ('critical','high','medium','low')",
            name="ck_impact_assessment_match_risk_level",
        ),
        CheckConstraint(
            "(risk_score >= 85 AND risk_level = 'critical') OR "
            "(risk_score >= 70 AND risk_score < 85 AND risk_level = 'high') OR "
            "(risk_score >= 45 AND risk_score < 70 AND risk_level = 'medium') OR "
            "(risk_score < 45 AND risk_level = 'low')",
            name="ck_impact_assessment_match_risk_consistency",
        ),
        CheckConstraint(
            "json_array_length(match_basis) <= 100",
            name="ck_impact_assessment_match_basis_bound",
        ),
        CheckConstraint(
            "json_array_length(match_sources) <= 2",
            name="ck_impact_assessment_match_sources_bound",
        ),
        UniqueConstraint(
            "namespace",
            "job_id",
            "decision_id",
            name="uq_impact_assessment_match",
        ),
        Index(
            "ix_impact_assessment_match_page",
            "namespace",
            "job_id",
            "sequence",
        ),
    )
