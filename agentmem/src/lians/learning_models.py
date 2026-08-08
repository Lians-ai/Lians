"""Append-only business outcomes, feedback, drift, and learning proposals."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from sqlalchemy import (
    JSON,
    Boolean,
    CheckConstraint,
    Column,
    DateTime,
    Float,
    ForeignKeyConstraint,
    Index,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import UUID

from .db import Base


def _now() -> datetime:
    return datetime.now(UTC)


class Outcome(Base):
    __tablename__ = "improvement_outcomes"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    namespace = Column(String(255), nullable=False)
    barrier_group = Column(String(255), nullable=True)
    barrier_scope = Column(String(64), nullable=False)
    agent_version_id = Column(UUID(as_uuid=True), nullable=False)
    decision_id = Column(UUID(as_uuid=True), nullable=True)
    deployment_id = Column(UUID(as_uuid=True), nullable=True)
    correlation_hash = Column(String(64), nullable=False)
    kind = Column(String(32), nullable=False)
    metrics = Column(JSON, nullable=False)
    payload_encrypted = Column(Text, nullable=True)
    payload_hash = Column(String(64), nullable=True)
    outcome_hash = Column(String(64), nullable=False)
    occurred_at = Column(DateTime(timezone=True), nullable=False)
    recorded_at = Column(DateTime(timezone=True), nullable=False, default=_now)
    recorded_by_principal_ref = Column(String(512), nullable=False)

    __table_args__ = (
        ForeignKeyConstraint(
            ["agent_version_id", "namespace"],
            ["agent_versions.id", "agent_versions.namespace"],
            ondelete="RESTRICT",
            name="fk_outcome_agent_version_namespace",
        ),
        ForeignKeyConstraint(
            ["decision_id", "namespace"],
            ["decision_records.id", "decision_records.namespace"],
            ondelete="RESTRICT",
            name="fk_outcome_decision_namespace",
        ),
        UniqueConstraint("id", "namespace", name="uq_improvement_outcome_id_namespace"),
        UniqueConstraint(
            "namespace", "barrier_scope", "outcome_hash", name="uq_improvement_outcome_scope_hash"
        ),
        Index(
            "ix_improvement_outcome_scope_time", "namespace", "barrier_group", "occurred_at", "id"
        ),
        Index(
            "ix_improvement_outcome_version_time", "namespace", "agent_version_id", "occurred_at"
        ),
        CheckConstraint(
            "kind IN ('success','failure','correction','dispute','override','incident','business')",
            name="ck_improvement_outcome_kind",
        ),
        CheckConstraint(
            "length(correlation_hash) = 64 AND length(outcome_hash) = 64",
            name="ck_improvement_outcome_hashes",
        ),
        CheckConstraint(
            "(payload_encrypted IS NULL AND payload_hash IS NULL) OR "
            "(payload_encrypted IS NOT NULL AND length(payload_hash) = 64)",
            name="ck_improvement_outcome_payload_pair",
        ),
    )


class Feedback(Base):
    __tablename__ = "improvement_feedback"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    namespace = Column(String(255), nullable=False)
    barrier_group = Column(String(255), nullable=True)
    barrier_scope = Column(String(64), nullable=False)
    agent_version_id = Column(UUID(as_uuid=True), nullable=False)
    outcome_id = Column(UUID(as_uuid=True), nullable=True)
    decision_id = Column(UUID(as_uuid=True), nullable=True)
    decision_receipt_hash = Column(String(64), nullable=True)
    kind = Column(String(32), nullable=False)
    payload_encrypted = Column(Text, nullable=False)
    payload_hash = Column(String(64), nullable=False)
    generated_eval_case_id = Column(UUID(as_uuid=True), nullable=True)
    feedback_hash = Column(String(64), nullable=False)
    authored_by_principal_ref = Column(String(512), nullable=False)
    authored_at = Column(DateTime(timezone=True), nullable=False, default=_now)

    __table_args__ = (
        ForeignKeyConstraint(
            ["agent_version_id", "namespace"],
            ["agent_versions.id", "agent_versions.namespace"],
            ondelete="RESTRICT",
            name="fk_feedback_agent_version_namespace",
        ),
        ForeignKeyConstraint(
            ["outcome_id", "namespace"],
            ["improvement_outcomes.id", "improvement_outcomes.namespace"],
            ondelete="RESTRICT",
            name="fk_feedback_outcome_namespace",
        ),
        ForeignKeyConstraint(
            ["decision_id", "namespace"],
            ["decision_records.id", "decision_records.namespace"],
            ondelete="RESTRICT",
            name="fk_feedback_decision_namespace",
        ),
        ForeignKeyConstraint(
            ["generated_eval_case_id", "namespace"],
            ["eval_cases.id", "eval_cases.namespace"],
            ondelete="RESTRICT",
            name="fk_feedback_eval_case_namespace",
        ),
        UniqueConstraint("id", "namespace", name="uq_improvement_feedback_id_namespace"),
        UniqueConstraint(
            "namespace", "barrier_scope", "feedback_hash", name="uq_improvement_feedback_scope_hash"
        ),
        Index(
            "ix_improvement_feedback_scope_time", "namespace", "barrier_group", "authored_at", "id"
        ),
        CheckConstraint(
            "kind IN ('correction','dispute','human_override','incident','rating','comment')",
            name="ck_improvement_feedback_kind",
        ),
        CheckConstraint(
            "length(payload_hash) = 64 AND length(feedback_hash) = 64",
            name="ck_improvement_feedback_hashes",
        ),
    )


class DriftSignal(Base):
    __tablename__ = "drift_signals"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    namespace = Column(String(255), nullable=False)
    barrier_group = Column(String(255), nullable=True)
    barrier_scope = Column(String(64), nullable=False)
    agent_version_id = Column(UUID(as_uuid=True), nullable=False)
    metric_name = Column(String(255), nullable=False)
    baseline = Column(JSON, nullable=False)
    current = Column(JSON, nullable=False)
    direction = Column(String(16), nullable=False)
    magnitude = Column(Float, nullable=False)
    threshold = Column(Float, nullable=False)
    drifted = Column(Boolean, nullable=False)
    method = Column(String(32), nullable=False)
    signal_hash = Column(String(64), nullable=False)
    detected_by_principal_ref = Column(String(512), nullable=False)
    detected_at = Column(DateTime(timezone=True), nullable=False, default=_now)

    __table_args__ = (
        ForeignKeyConstraint(
            ["agent_version_id", "namespace"],
            ["agent_versions.id", "agent_versions.namespace"],
            ondelete="RESTRICT",
            name="fk_drift_signal_agent_version_namespace",
        ),
        UniqueConstraint("id", "namespace", name="uq_drift_signal_id_namespace"),
        UniqueConstraint(
            "namespace", "barrier_scope", "signal_hash", name="uq_drift_signal_scope_hash"
        ),
        Index("ix_drift_signal_scope_time", "namespace", "barrier_group", "detected_at", "id"),
        CheckConstraint(
            "direction IN ('increase','decrease','absolute')", name="ck_drift_direction"
        ),
        CheckConstraint("magnitude >= 0 AND threshold >= 0", name="ck_drift_magnitude"),
        CheckConstraint("method = 'two-window-mean-v1'", name="ck_drift_method"),
        CheckConstraint("length(signal_hash) = 64", name="ck_drift_signal_hash"),
    )


class LearningProposal(Base):
    __tablename__ = "learning_proposals"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    namespace = Column(String(255), nullable=False)
    barrier_group = Column(String(255), nullable=True)
    barrier_scope = Column(String(64), nullable=False)
    agent_version_id = Column(UUID(as_uuid=True), nullable=False)
    source_feedback_id = Column(UUID(as_uuid=True), nullable=True)
    source_drift_signal_id = Column(UUID(as_uuid=True), nullable=True)
    eval_case_id = Column(UUID(as_uuid=True), nullable=True)
    proposal_type = Column(String(32), nullable=False)
    recommendation = Column(JSON, nullable=False)
    priority = Column(Float, nullable=False)
    status = Column(String(32), nullable=False)
    proposal_hash = Column(String(64), nullable=False)
    created_by_principal_ref = Column(String(512), nullable=False)
    created_at = Column(DateTime(timezone=True), nullable=False, default=_now)

    __table_args__ = (
        ForeignKeyConstraint(
            ["agent_version_id", "namespace"],
            ["agent_versions.id", "agent_versions.namespace"],
            ondelete="RESTRICT",
            name="fk_learning_proposal_agent_version_namespace",
        ),
        ForeignKeyConstraint(
            ["source_feedback_id", "namespace"],
            ["improvement_feedback.id", "improvement_feedback.namespace"],
            ondelete="RESTRICT",
            name="fk_learning_proposal_feedback_namespace",
        ),
        ForeignKeyConstraint(
            ["source_drift_signal_id", "namespace"],
            ["drift_signals.id", "drift_signals.namespace"],
            ondelete="RESTRICT",
            name="fk_learning_proposal_drift_namespace",
        ),
        ForeignKeyConstraint(
            ["eval_case_id", "namespace"],
            ["eval_cases.id", "eval_cases.namespace"],
            ondelete="RESTRICT",
            name="fk_learning_proposal_eval_case_namespace",
        ),
        UniqueConstraint("id", "namespace", name="uq_learning_proposal_id_namespace"),
        UniqueConstraint(
            "namespace", "barrier_scope", "proposal_hash", name="uq_learning_proposal_scope_hash"
        ),
        Index(
            "ix_learning_proposal_scope_priority",
            "namespace",
            "barrier_group",
            "status",
            "priority",
            "id",
        ),
        CheckConstraint(
            "proposal_type IN ('regression_case','context_change','tool_change','prompt_change',"
            "'model_change','policy_change','investigate')",
            name="ck_learning_proposal_type",
        ),
        CheckConstraint(
            "status = 'awaiting_customer_approval'", name="ck_learning_proposal_status"
        ),
        CheckConstraint("priority >= 0 AND priority <= 1", name="ck_learning_proposal_priority"),
        CheckConstraint(
            "(source_feedback_id IS NOT NULL AND source_drift_signal_id IS NULL) OR "
            "(source_feedback_id IS NULL AND source_drift_signal_id IS NOT NULL)",
            name="ck_learning_proposal_one_source",
        ),
        CheckConstraint("length(proposal_hash) = 64", name="ck_learning_proposal_hash"),
    )


__all__ = ["DriftSignal", "Feedback", "LearningProposal", "Outcome"]
