"""Immutable persistence records for the Lians agent-improvement plane.

The improvement plane is deliberately append-only.  Agent manifests, evaluation
inputs, trial observations, comparisons, attestations, and advisory optimization
records are evidence: changing one in place would sever the claim from the exact
configuration that produced it.
"""

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
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import UUID

from .db import Base


def _now() -> datetime:
    return datetime.now(UTC)


class AgentDefinition(Base):
    """Stable tenant-local identity for an agent workload."""

    __tablename__ = "agent_definitions"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    namespace = Column(String(255), nullable=False)
    barrier_group = Column(String(255), nullable=True)
    barrier_scope = Column(String(64), nullable=False)
    key = Column(String(255), nullable=False)
    name = Column(String(255), nullable=False)
    description = Column(Text, nullable=True)
    metadata_ = Column("metadata", JSON, nullable=False, default=dict, server_default="{}")
    definition_hash = Column(String(64), nullable=False)
    created_by_principal_ref = Column(String(512), nullable=False)
    created_at = Column(DateTime(timezone=True), nullable=False, default=_now)

    __table_args__ = (
        UniqueConstraint(
            "namespace", "barrier_scope", "key", name="uq_agent_definition_namespace_key"
        ),
        UniqueConstraint("id", "namespace", name="uq_agent_definition_id_namespace"),
        Index("ix_agent_definition_scope_page", "namespace", "barrier_group", "created_at", "id"),
        CheckConstraint(
            "length(definition_hash) = 64 AND definition_hash = lower(definition_hash)",
            name="ck_agent_definition_hash",
        ),
    )


class ComponentArtifact(Base):
    """Content-addressed prompt, model, policy, tool, code, or context artifact."""

    __tablename__ = "component_artifacts"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    namespace = Column(String(255), nullable=False)
    barrier_group = Column(String(255), nullable=True)
    barrier_scope = Column(String(64), nullable=False)
    kind = Column(String(32), nullable=False)
    name = Column(String(255), nullable=False)
    version = Column(String(255), nullable=True)
    uri = Column(String(2048), nullable=True)
    digest_algorithm = Column(String(16), nullable=False, default="sha256", server_default="sha256")
    digest = Column(String(64), nullable=False)
    metadata_ = Column("metadata", JSON, nullable=False, default=dict, server_default="{}")
    artifact_hash = Column(String(64), nullable=False)
    created_by_principal_ref = Column(String(512), nullable=False)
    created_at = Column(DateTime(timezone=True), nullable=False, default=_now)

    __table_args__ = (
        UniqueConstraint(
            "namespace",
            "barrier_scope",
            "kind",
            "digest",
            name="uq_component_artifact_namespace_kind_digest",
        ),
        UniqueConstraint("id", "namespace", name="uq_component_artifact_id_namespace"),
        Index("ix_component_artifact_scope_page", "namespace", "barrier_group", "created_at", "id"),
        CheckConstraint(
            "kind IN ('model','prompt','policy','tool','context','code','runtime','permission','other')",
            name="ck_component_artifact_kind",
        ),
        CheckConstraint("digest_algorithm = 'sha256'", name="ck_component_artifact_algorithm"),
        CheckConstraint(
            "length(digest) = 64 AND digest = lower(digest) "
            "AND length(artifact_hash) = 64 AND artifact_hash = lower(artifact_hash)",
            name="ck_component_artifact_hashes",
        ),
    )


class AgentVersion(Base):
    """Immutable, content-addressed configuration of one agent definition."""

    __tablename__ = "agent_versions"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    namespace = Column(String(255), nullable=False)
    barrier_group = Column(String(255), nullable=True)
    barrier_scope = Column(String(64), nullable=False)
    agent_definition_id = Column(UUID(as_uuid=True), nullable=False)
    version = Column(String(255), nullable=False)
    manifest = Column(JSON, nullable=False, default=dict, server_default="{}")
    manifest_hash = Column(String(64), nullable=False)
    created_by_principal_ref = Column(String(512), nullable=False)
    created_at = Column(DateTime(timezone=True), nullable=False, default=_now)

    __table_args__ = (
        ForeignKeyConstraint(
            ["agent_definition_id", "namespace"],
            ["agent_definitions.id", "agent_definitions.namespace"],
            ondelete="RESTRICT",
            name="fk_agent_version_definition_namespace",
        ),
        UniqueConstraint(
            "namespace",
            "barrier_scope",
            "agent_definition_id",
            "version",
            name="uq_agent_version_label",
        ),
        UniqueConstraint(
            "namespace",
            "barrier_scope",
            "agent_definition_id",
            "manifest_hash",
            name="uq_agent_version_manifest",
        ),
        UniqueConstraint("id", "namespace", name="uq_agent_version_id_namespace"),
        Index("ix_agent_version_scope_page", "namespace", "barrier_group", "created_at", "id"),
        CheckConstraint(
            "length(manifest_hash) = 64 AND manifest_hash = lower(manifest_hash)",
            name="ck_agent_version_manifest_hash",
        ),
    )


class AgentVersionComponent(Base):
    """Immutable membership edge from an agent version to an artifact."""

    __tablename__ = "agent_version_components"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    namespace = Column(String(255), nullable=False)
    barrier_group = Column(String(255), nullable=True)
    barrier_scope = Column(String(64), nullable=False)
    agent_version_id = Column(UUID(as_uuid=True), nullable=False)
    component_artifact_id = Column(UUID(as_uuid=True), nullable=False)
    role = Column(String(64), nullable=False)
    position = Column(Integer, nullable=False, default=0, server_default="0")
    binding_hash = Column(String(64), nullable=False)

    __table_args__ = (
        ForeignKeyConstraint(
            ["agent_version_id", "namespace"],
            ["agent_versions.id", "agent_versions.namespace"],
            ondelete="RESTRICT",
            name="fk_agent_version_component_version_namespace",
        ),
        ForeignKeyConstraint(
            ["component_artifact_id", "namespace"],
            ["component_artifacts.id", "component_artifacts.namespace"],
            ondelete="RESTRICT",
            name="fk_agent_version_component_artifact_namespace",
        ),
        UniqueConstraint(
            "agent_version_id", "role", "position", name="uq_agent_version_component_slot"
        ),
        UniqueConstraint(
            "agent_version_id",
            "component_artifact_id",
            "role",
            name="uq_agent_version_component_binding",
        ),
        CheckConstraint("position >= 0", name="ck_agent_version_component_position"),
        CheckConstraint("length(binding_hash) = 64", name="ck_agent_version_component_hash"),
    )


class EvalCase(Base):
    """Versioned evaluation case derived from a captured production decision."""

    __tablename__ = "eval_cases"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    namespace = Column(String(255), nullable=False)
    barrier_group = Column(String(255), nullable=True)
    barrier_scope = Column(String(64), nullable=False)
    decision_id = Column(UUID(as_uuid=True), nullable=False)
    decision_record_hash = Column(String(64), nullable=False)
    decision_receipt_hash = Column(String(64), nullable=False)
    name = Column(String(255), nullable=False)
    input = Column(JSON, nullable=False, default=dict, server_default="{}")
    expected = Column(JSON, nullable=False, default=dict, server_default="{}")
    scorer_context = Column(JSON, nullable=False, default=dict, server_default="{}")
    tags = Column(JSON, nullable=False, default=list, server_default="[]")
    capture_limitations = Column(JSON, nullable=False, default=list, server_default="[]")
    case_hash = Column(String(64), nullable=False)
    created_by_principal_ref = Column(String(512), nullable=False)
    created_at = Column(DateTime(timezone=True), nullable=False, default=_now)

    __table_args__ = (
        ForeignKeyConstraint(
            ["decision_id", "namespace"],
            ["decision_records.id", "decision_records.namespace"],
            ondelete="RESTRICT",
            name="fk_eval_case_decision_namespace",
        ),
        UniqueConstraint("id", "namespace", name="uq_eval_case_id_namespace"),
        UniqueConstraint(
            "namespace", "barrier_scope", "case_hash", name="uq_eval_case_namespace_hash"
        ),
        Index("ix_eval_case_scope_page", "namespace", "barrier_group", "created_at", "id"),
        Index("ix_eval_case_decision", "namespace", "decision_id"),
        CheckConstraint(
            "length(decision_record_hash) = 64 AND length(decision_receipt_hash) = 64 "
            "AND length(case_hash) = 64",
            name="ck_eval_case_hashes",
        ),
    )


class EvalSuite(Base):
    """Immutable dataset plus a versioned multi-objective improvement contract."""

    __tablename__ = "eval_suites"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    namespace = Column(String(255), nullable=False)
    barrier_group = Column(String(255), nullable=True)
    barrier_scope = Column(String(64), nullable=False)
    name = Column(String(255), nullable=False)
    version = Column(String(255), nullable=False)
    description = Column(Text, nullable=True)
    improvement_contract = Column(JSON, nullable=False)
    repetitions = Column(Integer, nullable=False, default=2, server_default="2")
    suite_hash = Column(String(64), nullable=False)
    created_by_principal_ref = Column(String(512), nullable=False)
    created_at = Column(DateTime(timezone=True), nullable=False, default=_now)

    __table_args__ = (
        UniqueConstraint(
            "namespace", "barrier_scope", "name", "version", name="uq_eval_suite_name_version"
        ),
        UniqueConstraint("id", "namespace", name="uq_eval_suite_id_namespace"),
        UniqueConstraint(
            "namespace", "barrier_scope", "suite_hash", name="uq_eval_suite_namespace_hash"
        ),
        Index("ix_eval_suite_scope_page", "namespace", "barrier_group", "created_at", "id"),
        CheckConstraint("repetitions BETWEEN 2 AND 100", name="ck_eval_suite_repetitions"),
        CheckConstraint("length(suite_hash) = 64", name="ck_eval_suite_hash"),
    )


class EvalSuiteCase(Base):
    """Immutable, ordered membership of a case in a suite."""

    __tablename__ = "eval_suite_cases"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    namespace = Column(String(255), nullable=False)
    barrier_group = Column(String(255), nullable=True)
    barrier_scope = Column(String(64), nullable=False)
    suite_id = Column(UUID(as_uuid=True), nullable=False)
    case_id = Column(UUID(as_uuid=True), nullable=False)
    position = Column(Integer, nullable=False)

    __table_args__ = (
        ForeignKeyConstraint(
            ["suite_id", "namespace"],
            ["eval_suites.id", "eval_suites.namespace"],
            ondelete="RESTRICT",
            name="fk_eval_suite_case_suite_namespace",
        ),
        ForeignKeyConstraint(
            ["case_id", "namespace"],
            ["eval_cases.id", "eval_cases.namespace"],
            ondelete="RESTRICT",
            name="fk_eval_suite_case_case_namespace",
        ),
        UniqueConstraint("suite_id", "case_id", name="uq_eval_suite_case_member"),
        UniqueConstraint("suite_id", "position", name="uq_eval_suite_case_position"),
        CheckConstraint("position >= 0", name="ck_eval_suite_case_position"),
    )


class EvalRun(Base):
    """One completed, immutable execution of a suite against an agent version."""

    __tablename__ = "eval_runs"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    namespace = Column(String(255), nullable=False)
    barrier_group = Column(String(255), nullable=True)
    barrier_scope = Column(String(64), nullable=False)
    suite_id = Column(UUID(as_uuid=True), nullable=False)
    agent_version_id = Column(UUID(as_uuid=True), nullable=False)
    environment = Column(JSON, nullable=False, default=dict, server_default="{}")
    capture_limitations = Column(JSON, nullable=False, default=list, server_default="[]")
    trial_count = Column(Integer, nullable=False)
    run_hash = Column(String(64), nullable=False)
    created_by_principal_ref = Column(String(512), nullable=False)
    completed_at = Column(DateTime(timezone=True), nullable=False, default=_now)

    __table_args__ = (
        ForeignKeyConstraint(
            ["suite_id", "namespace"],
            ["eval_suites.id", "eval_suites.namespace"],
            ondelete="RESTRICT",
            name="fk_eval_run_suite_namespace",
        ),
        ForeignKeyConstraint(
            ["agent_version_id", "namespace"],
            ["agent_versions.id", "agent_versions.namespace"],
            ondelete="RESTRICT",
            name="fk_eval_run_agent_version_namespace",
        ),
        UniqueConstraint("id", "namespace", name="uq_eval_run_id_namespace"),
        UniqueConstraint(
            "namespace", "barrier_scope", "run_hash", name="uq_eval_run_namespace_hash"
        ),
        Index("ix_eval_run_suite_page", "namespace", "suite_id", "completed_at", "id"),
        CheckConstraint("trial_count > 0", name="ck_eval_run_trial_count"),
        CheckConstraint("length(run_hash) = 64", name="ck_eval_run_hash"),
    )


class Trial(Base):
    """A pinned repeat for one case; results cannot be amended after insertion."""

    __tablename__ = "eval_trials"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    namespace = Column(String(255), nullable=False)
    barrier_group = Column(String(255), nullable=True)
    barrier_scope = Column(String(64), nullable=False)
    run_id = Column(UUID(as_uuid=True), nullable=False)
    case_id = Column(UUID(as_uuid=True), nullable=False)
    repetition = Column(Integer, nullable=False)
    seed = Column(Integer, nullable=False)
    input_hash = Column(String(64), nullable=False)
    output_hash = Column(String(64), nullable=False)
    configuration_hash = Column(String(64), nullable=False)
    latency_ms = Column(Float, nullable=True)
    input_tokens = Column(Integer, nullable=True)
    output_tokens = Column(Integer, nullable=True)
    cost = Column(Float, nullable=True)
    cost_currency = Column(String(3), nullable=True)
    started_at = Column(DateTime(timezone=True), nullable=False)
    completed_at = Column(DateTime(timezone=True), nullable=False)
    trial_hash = Column(String(64), nullable=False)

    __table_args__ = (
        ForeignKeyConstraint(
            ["run_id", "namespace"],
            ["eval_runs.id", "eval_runs.namespace"],
            ondelete="RESTRICT",
            name="fk_eval_trial_run_namespace",
        ),
        ForeignKeyConstraint(
            ["case_id", "namespace"],
            ["eval_cases.id", "eval_cases.namespace"],
            ondelete="RESTRICT",
            name="fk_eval_trial_case_namespace",
        ),
        UniqueConstraint("run_id", "case_id", "repetition", name="uq_eval_trial_repeat"),
        UniqueConstraint("id", "namespace", name="uq_eval_trial_id_namespace"),
        CheckConstraint("repetition >= 0", name="ck_eval_trial_repetition"),
        CheckConstraint("latency_ms IS NULL OR latency_ms >= 0", name="ck_eval_trial_latency"),
        CheckConstraint(
            "input_tokens IS NULL OR input_tokens >= 0", name="ck_eval_trial_input_tokens"
        ),
        CheckConstraint(
            "output_tokens IS NULL OR output_tokens >= 0", name="ck_eval_trial_output_tokens"
        ),
        CheckConstraint("cost IS NULL OR cost >= 0", name="ck_eval_trial_cost"),
        CheckConstraint("completed_at >= started_at", name="ck_eval_trial_time_order"),
        CheckConstraint(
            "length(input_hash) = 64 AND length(output_hash) = 64 "
            "AND length(configuration_hash) = 64 AND length(trial_hash) = 64",
            name="ck_eval_trial_hashes",
        ),
    )


class MetricResult(Base):
    """One scored observation with explicit scorer and provenance identity."""

    __tablename__ = "metric_results"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    namespace = Column(String(255), nullable=False)
    barrier_group = Column(String(255), nullable=True)
    barrier_scope = Column(String(64), nullable=False)
    trial_id = Column(UUID(as_uuid=True), nullable=False)
    name = Column(String(255), nullable=False)
    metric_type = Column(String(32), nullable=False)
    value = Column(Float, nullable=False)
    unit = Column(String(64), nullable=False)
    provenance = Column(String(32), nullable=False)
    scorer_id = Column(String(255), nullable=False)
    scorer_version = Column(String(255), nullable=False)
    scorer_config_hash = Column(String(64), nullable=False)
    evidence_refs = Column(JSON, nullable=False, default=list, server_default="[]")
    limitations = Column(JSON, nullable=False, default=list, server_default="[]")
    result_hash = Column(String(64), nullable=False)

    __table_args__ = (
        ForeignKeyConstraint(
            ["trial_id", "namespace"],
            ["eval_trials.id", "eval_trials.namespace"],
            ondelete="RESTRICT",
            name="fk_metric_result_trial_namespace",
        ),
        UniqueConstraint("trial_id", "name", name="uq_metric_result_trial_name"),
        CheckConstraint(
            "metric_type IN ('quality','evidence','safety','latency','token','cost','outcome','reliability','autonomy','robustness')",
            name="ck_metric_result_type",
        ),
        CheckConstraint(
            "provenance IN ('provider-reported','workload-reported','client-measured',"
            "'deterministic','human-authored','model-judged','external','estimated')",
            name="ck_metric_result_provenance",
        ),
        CheckConstraint(
            "length(scorer_config_hash) = 64 AND length(result_hash) = 64",
            name="ck_metric_result_hashes",
        ),
    )


class Comparison(Base):
    """Deterministic aggregate comparison between baseline and candidate runs."""

    __tablename__ = "eval_comparisons"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    namespace = Column(String(255), nullable=False)
    barrier_group = Column(String(255), nullable=True)
    barrier_scope = Column(String(64), nullable=False)
    suite_id = Column(UUID(as_uuid=True), nullable=False)
    baseline_run_id = Column(UUID(as_uuid=True), nullable=False)
    candidate_run_id = Column(UUID(as_uuid=True), nullable=False)
    primary_metric = Column(String(255), nullable=False)
    primary_improvement = Column(Float, nullable=False)
    aggregates = Column(JSON, nullable=False)
    protected_results = Column(JSON, nullable=False)
    critical_invariants_passed = Column(Boolean, nullable=False)
    verdict = Column(String(32), nullable=False)
    comparison_hash = Column(String(64), nullable=False)
    created_by_principal_ref = Column(String(512), nullable=False)
    created_at = Column(DateTime(timezone=True), nullable=False, default=_now)

    __table_args__ = (
        ForeignKeyConstraint(
            ["suite_id", "namespace"],
            ["eval_suites.id", "eval_suites.namespace"],
            ondelete="RESTRICT",
            name="fk_eval_comparison_suite_namespace",
        ),
        ForeignKeyConstraint(
            ["baseline_run_id", "namespace"],
            ["eval_runs.id", "eval_runs.namespace"],
            ondelete="RESTRICT",
            name="fk_eval_comparison_baseline_namespace",
        ),
        ForeignKeyConstraint(
            ["candidate_run_id", "namespace"],
            ["eval_runs.id", "eval_runs.namespace"],
            ondelete="RESTRICT",
            name="fk_eval_comparison_candidate_namespace",
        ),
        UniqueConstraint(
            "namespace",
            "barrier_scope",
            "baseline_run_id",
            "candidate_run_id",
            name="uq_eval_comparison_run_pair",
        ),
        UniqueConstraint("id", "namespace", name="uq_eval_comparison_id_namespace"),
        UniqueConstraint(
            "namespace", "barrier_scope", "comparison_hash", name="uq_eval_comparison_hash"
        ),
        CheckConstraint(
            "verdict IN ('eligible_for_review','no_verified_improvement','protected_regression')",
            name="ck_eval_comparison_verdict",
        ),
        CheckConstraint("length(comparison_hash) = 64", name="ck_eval_comparison_hash_length"),
    )


class EvaluationAttestation(Base):
    """Separate signed evaluation claim; Decision Receipt v0.1 stays closed."""

    __tablename__ = "evaluation_attestations"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    namespace = Column(String(255), nullable=False)
    barrier_group = Column(String(255), nullable=True)
    barrier_scope = Column(String(64), nullable=False)
    schema_version = Column(String(16), nullable=False, default="0.1", server_default="0.1")
    comparison_id = Column(UUID(as_uuid=True), nullable=False)
    payload = Column(JSON, nullable=False)
    payload_hash = Column(String(64), nullable=False)
    signature_algorithm = Column(String(32), nullable=False)
    signing_key_id = Column(String(255), nullable=False)
    signing_public_key = Column(Text, nullable=False)
    signature = Column(Text, nullable=False)
    created_by_principal_ref = Column(String(512), nullable=False)
    attested_at = Column(DateTime(timezone=True), nullable=False, default=_now)

    __table_args__ = (
        ForeignKeyConstraint(
            ["comparison_id", "namespace"],
            ["eval_comparisons.id", "eval_comparisons.namespace"],
            ondelete="RESTRICT",
            name="fk_evaluation_attestation_comparison_namespace",
        ),
        UniqueConstraint("id", "namespace", name="uq_evaluation_attestation_id_namespace"),
        UniqueConstraint(
            "namespace", "barrier_scope", "payload_hash", name="uq_evaluation_attestation_hash"
        ),
        CheckConstraint("schema_version = '0.1'", name="ck_evaluation_attestation_version"),
        CheckConstraint("signature_algorithm = 'ed25519'", name="ck_evaluation_attestation_alg"),
        CheckConstraint("length(payload_hash) = 64", name="ck_evaluation_attestation_hash_length"),
    )


class OptimizationStudy(Base):
    """Advisory, multi-objective study over already evaluated candidates."""

    __tablename__ = "optimization_studies"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    namespace = Column(String(255), nullable=False)
    barrier_group = Column(String(255), nullable=True)
    barrier_scope = Column(String(64), nullable=False)
    name = Column(String(255), nullable=False)
    suite_id = Column(UUID(as_uuid=True), nullable=False)
    baseline_agent_version_id = Column(UUID(as_uuid=True), nullable=False)
    objective = Column(JSON, nullable=False)
    status = Column(String(32), nullable=False, default="advisory", server_default="advisory")
    study_hash = Column(String(64), nullable=False)
    created_by_principal_ref = Column(String(512), nullable=False)
    created_at = Column(DateTime(timezone=True), nullable=False, default=_now)

    __table_args__ = (
        ForeignKeyConstraint(
            ["suite_id", "namespace"],
            ["eval_suites.id", "eval_suites.namespace"],
            ondelete="RESTRICT",
            name="fk_optimization_study_suite_namespace",
        ),
        ForeignKeyConstraint(
            ["baseline_agent_version_id", "namespace"],
            ["agent_versions.id", "agent_versions.namespace"],
            ondelete="RESTRICT",
            name="fk_optimization_study_baseline_namespace",
        ),
        UniqueConstraint("id", "namespace", name="uq_optimization_study_id_namespace"),
        UniqueConstraint(
            "namespace", "barrier_scope", "study_hash", name="uq_optimization_study_hash"
        ),
        CheckConstraint("status = 'advisory'", name="ck_optimization_study_status"),
        CheckConstraint("length(study_hash) = 64", name="ck_optimization_study_hash_length"),
    )


class Candidate(Base):
    """A candidate configuration backed by an immutable comparison."""

    __tablename__ = "optimization_candidates"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    namespace = Column(String(255), nullable=False)
    barrier_group = Column(String(255), nullable=True)
    barrier_scope = Column(String(64), nullable=False)
    study_id = Column(UUID(as_uuid=True), nullable=False)
    agent_version_id = Column(UUID(as_uuid=True), nullable=False)
    comparison_id = Column(UUID(as_uuid=True), nullable=False)
    rank = Column(Integer, nullable=False)
    eligible = Column(Boolean, nullable=False)
    score = Column(Float, nullable=False)
    candidate_hash = Column(String(64), nullable=False)

    __table_args__ = (
        ForeignKeyConstraint(
            ["study_id", "namespace"],
            ["optimization_studies.id", "optimization_studies.namespace"],
            ondelete="RESTRICT",
            name="fk_optimization_candidate_study_namespace",
        ),
        ForeignKeyConstraint(
            ["agent_version_id", "namespace"],
            ["agent_versions.id", "agent_versions.namespace"],
            ondelete="RESTRICT",
            name="fk_optimization_candidate_version_namespace",
        ),
        ForeignKeyConstraint(
            ["comparison_id", "namespace"],
            ["eval_comparisons.id", "eval_comparisons.namespace"],
            ondelete="RESTRICT",
            name="fk_optimization_candidate_comparison_namespace",
        ),
        UniqueConstraint("id", "namespace", name="uq_optimization_candidate_id_namespace"),
        UniqueConstraint("study_id", "agent_version_id", name="uq_optimization_candidate_version"),
        UniqueConstraint("study_id", "rank", name="uq_optimization_candidate_rank"),
        CheckConstraint("rank > 0", name="ck_optimization_candidate_rank"),
        CheckConstraint("length(candidate_hash) = 64", name="ck_optimization_candidate_hash"),
    )


class Recommendation(Base):
    """Human-review-only recommendation; it cannot authorize deployment."""

    __tablename__ = "optimization_recommendations"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    namespace = Column(String(255), nullable=False)
    barrier_group = Column(String(255), nullable=True)
    barrier_scope = Column(String(64), nullable=False)
    study_id = Column(UUID(as_uuid=True), nullable=False)
    candidate_id = Column(UUID(as_uuid=True), nullable=False)
    disposition = Column(String(32), nullable=False)
    rationale = Column(JSON, nullable=False)
    requires_human_approval = Column(Boolean, nullable=False, default=True, server_default="true")
    recommendation_hash = Column(String(64), nullable=False)
    created_at = Column(DateTime(timezone=True), nullable=False, default=_now)

    __table_args__ = (
        ForeignKeyConstraint(
            ["study_id", "namespace"],
            ["optimization_studies.id", "optimization_studies.namespace"],
            ondelete="RESTRICT",
            name="fk_optimization_recommendation_study_namespace",
        ),
        ForeignKeyConstraint(
            ["candidate_id", "namespace"],
            ["optimization_candidates.id", "optimization_candidates.namespace"],
            ondelete="RESTRICT",
            name="fk_optimization_recommendation_candidate_namespace",
        ),
        UniqueConstraint(
            "study_id", "candidate_id", name="uq_optimization_recommendation_candidate"
        ),
        CheckConstraint(
            "disposition IN ('recommend_for_human_review','do_not_recommend')",
            name="ck_optimization_recommendation_disposition",
        ),
        CheckConstraint("requires_human_approval", name="ck_optimization_recommendation_human"),
        CheckConstraint(
            "length(recommendation_hash) = 64", name="ck_optimization_recommendation_hash"
        ),
    )


__all__ = [
    "AgentDefinition",
    "AgentVersion",
    "AgentVersionComponent",
    "Candidate",
    "Comparison",
    "ComponentArtifact",
    "EvalCase",
    "EvalRun",
    "EvalSuite",
    "EvalSuiteCase",
    "EvaluationAttestation",
    "MetricResult",
    "OptimizationStudy",
    "Recommendation",
    "Trial",
]
