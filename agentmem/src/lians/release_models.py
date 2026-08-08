"""Immutable release assurance, deployment, canary, and rollback evidence."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from sqlalchemy import (
    JSON,
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


class ReleaseCandidate(Base):
    __tablename__ = "release_candidates"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    namespace = Column(String(255), nullable=False)
    barrier_group = Column(String(255), nullable=True)
    barrier_scope = Column(String(64), nullable=False)
    name = Column(String(255), nullable=False)
    version = Column(String(255), nullable=False)
    agent_version_id = Column(UUID(as_uuid=True), nullable=False)
    evaluation_attestation_id = Column(UUID(as_uuid=True), nullable=False)
    optimization_study_id = Column(UUID(as_uuid=True), nullable=True)
    environment_manifest = Column(JSON, nullable=False)
    rollout_plan = Column(JSON, nullable=False)
    release_hash = Column(String(64), nullable=False)
    created_by_principal_ref = Column(String(512), nullable=False)
    created_at = Column(DateTime(timezone=True), nullable=False, default=_now)

    __table_args__ = (
        ForeignKeyConstraint(
            ["agent_version_id", "namespace"],
            ["agent_versions.id", "agent_versions.namespace"],
            ondelete="RESTRICT",
            name="fk_release_candidate_agent_version_namespace",
        ),
        ForeignKeyConstraint(
            ["evaluation_attestation_id", "namespace"],
            ["evaluation_attestations.id", "evaluation_attestations.namespace"],
            ondelete="RESTRICT",
            name="fk_release_candidate_eval_attestation_namespace",
        ),
        ForeignKeyConstraint(
            ["optimization_study_id", "namespace"],
            ["optimization_studies.id", "optimization_studies.namespace"],
            ondelete="RESTRICT",
            name="fk_release_candidate_study_namespace",
        ),
        UniqueConstraint("id", "namespace", name="uq_release_candidate_id_namespace"),
        UniqueConstraint(
            "namespace",
            "barrier_scope",
            "name",
            "version",
            name="uq_release_candidate_scope_name_version",
        ),
        UniqueConstraint(
            "namespace", "barrier_scope", "release_hash", name="uq_release_candidate_scope_hash"
        ),
        Index("ix_release_candidate_scope_page", "namespace", "barrier_group", "created_at", "id"),
        CheckConstraint("length(release_hash) = 64", name="ck_release_candidate_hash"),
    )


class ReleaseAttestation(Base):
    __tablename__ = "release_attestations"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    namespace = Column(String(255), nullable=False)
    barrier_group = Column(String(255), nullable=True)
    barrier_scope = Column(String(64), nullable=False)
    schema_version = Column(String(16), nullable=False, default="0.1", server_default="0.1")
    release_candidate_id = Column(UUID(as_uuid=True), nullable=False)
    evaluation_attestation_id = Column(UUID(as_uuid=True), nullable=False)
    approval_attestation_ids = Column(JSON, nullable=False)
    payload = Column(JSON, nullable=False)
    payload_hash = Column(String(64), nullable=False)
    signature_algorithm = Column(String(32), nullable=False)
    signing_key_id = Column(String(255), nullable=False)
    signing_public_key = Column(Text, nullable=False)
    signature = Column(Text, nullable=False)
    attested_by_principal_ref = Column(String(512), nullable=False)
    attested_at = Column(DateTime(timezone=True), nullable=False, default=_now)

    __table_args__ = (
        ForeignKeyConstraint(
            ["release_candidate_id", "namespace"],
            ["release_candidates.id", "release_candidates.namespace"],
            ondelete="RESTRICT",
            name="fk_release_attestation_candidate_namespace",
        ),
        ForeignKeyConstraint(
            ["evaluation_attestation_id", "namespace"],
            ["evaluation_attestations.id", "evaluation_attestations.namespace"],
            ondelete="RESTRICT",
            name="fk_release_attestation_eval_namespace",
        ),
        UniqueConstraint("id", "namespace", name="uq_release_attestation_id_namespace"),
        UniqueConstraint("release_candidate_id", name="uq_release_attestation_candidate"),
        UniqueConstraint(
            "namespace", "barrier_scope", "payload_hash", name="uq_release_attestation_scope_hash"
        ),
        Index(
            "ix_release_attestation_scope_page", "namespace", "barrier_group", "attested_at", "id"
        ),
        CheckConstraint("schema_version = '0.1'", name="ck_release_attestation_version"),
        CheckConstraint("signature_algorithm = 'ed25519'", name="ck_release_attestation_alg"),
        CheckConstraint("length(payload_hash) = 64", name="ck_release_attestation_hash"),
    )


class Deployment(Base):
    __tablename__ = "improvement_deployments"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    namespace = Column(String(255), nullable=False)
    barrier_group = Column(String(255), nullable=True)
    barrier_scope = Column(String(64), nullable=False)
    release_attestation_id = Column(UUID(as_uuid=True), nullable=False)
    stage = Column(String(16), nullable=False)
    traffic_percentage = Column(Float, nullable=False)
    environment = Column(String(255), nullable=False)
    external_deployment_ref_hash = Column(String(64), nullable=False)
    prior_deployment_id = Column(UUID(as_uuid=True), nullable=True)
    evidence = Column(JSON, nullable=False)
    status = Column(String(32), nullable=False)
    deployment_hash = Column(String(64), nullable=False)
    recorded_by_principal_ref = Column(String(512), nullable=False)
    deployed_at = Column(DateTime(timezone=True), nullable=False)
    recorded_at = Column(DateTime(timezone=True), nullable=False, default=_now)

    __table_args__ = (
        ForeignKeyConstraint(
            ["release_attestation_id", "namespace"],
            ["release_attestations.id", "release_attestations.namespace"],
            ondelete="RESTRICT",
            name="fk_deployment_release_attestation_namespace",
        ),
        ForeignKeyConstraint(
            ["prior_deployment_id", "namespace"],
            ["improvement_deployments.id", "improvement_deployments.namespace"],
            ondelete="RESTRICT",
            name="fk_deployment_prior_namespace",
        ),
        UniqueConstraint("id", "namespace", name="uq_improvement_deployment_id_namespace"),
        UniqueConstraint(
            "namespace", "barrier_scope", "deployment_hash", name="uq_deployment_scope_hash"
        ),
        Index("ix_deployment_scope_time", "namespace", "barrier_group", "deployed_at", "id"),
        CheckConstraint("stage IN ('shadow','canary','production')", name="ck_deployment_stage"),
        CheckConstraint(
            "traffic_percentage >= 0 AND traffic_percentage <= 100",
            name="ck_deployment_traffic_percentage",
        ),
        CheckConstraint("status IN ('observed','healthy','failed')", name="ck_deployment_status"),
        CheckConstraint(
            "length(external_deployment_ref_hash) = 64 AND length(deployment_hash) = 64",
            name="ck_deployment_hashes",
        ),
    )


class Rollback(Base):
    __tablename__ = "improvement_rollbacks"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    namespace = Column(String(255), nullable=False)
    barrier_group = Column(String(255), nullable=True)
    barrier_scope = Column(String(64), nullable=False)
    deployment_id = Column(UUID(as_uuid=True), nullable=False)
    target_deployment_id = Column(UUID(as_uuid=True), nullable=False)
    reason_code = Column(String(128), nullable=False)
    evidence = Column(JSON, nullable=False)
    external_rollback_ref_hash = Column(String(64), nullable=False)
    rollback_hash = Column(String(64), nullable=False)
    recorded_by_principal_ref = Column(String(512), nullable=False)
    rolled_back_at = Column(DateTime(timezone=True), nullable=False)
    recorded_at = Column(DateTime(timezone=True), nullable=False, default=_now)

    __table_args__ = (
        ForeignKeyConstraint(
            ["deployment_id", "namespace"],
            ["improvement_deployments.id", "improvement_deployments.namespace"],
            ondelete="RESTRICT",
            name="fk_rollback_deployment_namespace",
        ),
        ForeignKeyConstraint(
            ["target_deployment_id", "namespace"],
            ["improvement_deployments.id", "improvement_deployments.namespace"],
            ondelete="RESTRICT",
            name="fk_rollback_target_namespace",
        ),
        UniqueConstraint("id", "namespace", name="uq_improvement_rollback_id_namespace"),
        UniqueConstraint("deployment_id", name="uq_rollback_deployment_once"),
        UniqueConstraint(
            "namespace", "barrier_scope", "rollback_hash", name="uq_rollback_scope_hash"
        ),
        Index("ix_rollback_scope_time", "namespace", "barrier_group", "rolled_back_at", "id"),
        CheckConstraint(
            "deployment_id <> target_deployment_id", name="ck_rollback_distinct_target"
        ),
        CheckConstraint(
            "length(external_rollback_ref_hash) = 64 AND length(rollback_hash) = 64",
            name="ck_rollback_hashes",
        ),
    )


__all__ = ["Deployment", "ReleaseAttestation", "ReleaseCandidate", "Rollback"]
