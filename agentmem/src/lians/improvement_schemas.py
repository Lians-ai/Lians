"""Typed public contracts for agent versions, evaluation, and optimization."""

from __future__ import annotations

import math
from datetime import datetime
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

SHA256_PATTERN = r"^[0-9a-f]{64}$"

ComponentKind = Literal[
    "model",
    "prompt",
    "policy",
    "tool",
    "context",
    "code",
    "runtime",
    "permission",
    "other",
]
MetricType = Literal[
    "quality",
    "evidence",
    "safety",
    "latency",
    "token",
    "cost",
    "outcome",
    "reliability",
    "autonomy",
    "robustness",
]
MetricProvenance = Literal[
    "provider-reported",
    "workload-reported",
    "client-measured",
    "deterministic",
    "human-authored",
    "model-judged",
    "external",
    "estimated",
]
Direction = Literal["maximize", "minimize"]


def _normalize_text(value: str) -> str:
    normalized = value.strip()
    if not normalized:
        raise ValueError("value cannot be blank")
    return normalized


class AgentDefinitionCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    key: str = Field(min_length=1, max_length=255, pattern=r"^[A-Za-z0-9][A-Za-z0-9._:-]*$")
    name: str = Field(min_length=1, max_length=255)
    description: str | None = Field(default=None, max_length=4000)
    metadata: dict[str, Any] = Field(default_factory=dict, max_length=256)

    _strip_name = field_validator("key", "name")(_normalize_text)


class AgentDefinitionOut(BaseModel):
    id: UUID
    namespace: str
    barrier_group: str | None
    key: str
    name: str
    description: str | None
    metadata: dict[str, Any]
    definition_hash: str
    created_by_principal_ref: str
    created_at: datetime


class ComponentArtifactCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: ComponentKind
    name: str = Field(min_length=1, max_length=255)
    version: str | None = Field(default=None, min_length=1, max_length=255)
    uri: str | None = Field(default=None, min_length=1, max_length=2048)
    digest_algorithm: Literal["sha256"] = "sha256"
    digest: str = Field(pattern=SHA256_PATTERN)
    metadata: dict[str, Any] = Field(default_factory=dict, max_length=256)

    _strip_name = field_validator("name", "version", "uri")(
        lambda value: _normalize_text(value) if value is not None else None
    )


class AgentVersionComponentCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    role: str = Field(min_length=1, max_length=64, pattern=r"^[A-Za-z0-9][A-Za-z0-9._:-]*$")
    artifact_id: UUID | None = None
    artifact: ComponentArtifactCreate | None = None

    @model_validator(mode="after")
    def exactly_one_artifact_source(self):
        if (self.artifact_id is None) == (self.artifact is None):
            raise ValueError("supply exactly one of artifact_id or artifact")
        return self


class ComponentArtifactOut(BaseModel):
    id: UUID
    namespace: str
    barrier_group: str | None
    kind: ComponentKind
    name: str
    version: str | None
    uri: str | None
    digest_algorithm: Literal["sha256"]
    digest: str
    metadata: dict[str, Any]
    artifact_hash: str
    created_by_principal_ref: str
    created_at: datetime


class AgentVersionCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    version: str = Field(min_length=1, max_length=255)
    manifest: dict[str, Any] = Field(default_factory=dict, max_length=512)
    components: list[AgentVersionComponentCreate] = Field(min_length=1, max_length=128)

    _strip_version = field_validator("version")(_normalize_text)

    @field_validator("components")
    @classmethod
    def unique_component_slots(cls, values: list[AgentVersionComponentCreate]):
        roles = [item.role for item in values]
        if len(roles) != len(set(roles)):
            raise ValueError("component roles must be unique within an agent version")
        return values


class AgentVersionComponentOut(BaseModel):
    role: str
    position: int
    binding_hash: str
    artifact: ComponentArtifactOut


class AgentVersionOut(BaseModel):
    id: UUID
    namespace: str
    barrier_group: str | None
    agent_definition_id: UUID
    version: str
    manifest: dict[str, Any]
    manifest_hash: str
    components: list[AgentVersionComponentOut]
    created_by_principal_ref: str
    created_at: datetime


class EvalCaseFromDecision(BaseModel):
    model_config = ConfigDict(extra="forbid")

    decision_id: UUID
    decision_receipt_hash: str = Field(pattern=SHA256_PATTERN)
    name: str = Field(min_length=1, max_length=255)
    input: dict[str, Any] = Field(default_factory=dict, max_length=512)
    expected: dict[str, Any] = Field(default_factory=dict, max_length=512)
    scorer_context: dict[str, Any] = Field(default_factory=dict, max_length=256)
    tags: list[str] = Field(default_factory=list, max_length=100)
    capture_limitations: list[str] = Field(default_factory=list, max_length=100)

    _strip_name = field_validator("name")(_normalize_text)

    @field_validator("tags", "capture_limitations")
    @classmethod
    def normalized_lists(cls, values: list[str]) -> list[str]:
        normalized = sorted({_normalize_text(value) for value in values})
        if any(len(value) > 512 for value in normalized):
            raise ValueError("list entries cannot exceed 512 characters")
        return normalized


class EvalCaseOut(BaseModel):
    id: UUID
    namespace: str
    barrier_group: str | None
    decision_id: UUID
    decision_record_hash: str
    decision_receipt_hash: str
    name: str
    input: dict[str, Any]
    expected: dict[str, Any]
    scorer_context: dict[str, Any]
    tags: list[str]
    capture_limitations: list[str]
    case_hash: str
    created_by_principal_ref: str
    created_at: datetime


class MetricObjective(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=255)
    direction: Direction
    minimum_improvement: float = 0.0

    _strip_name = field_validator("name")(_normalize_text)

    @field_validator("minimum_improvement")
    @classmethod
    def finite_nonnegative_improvement(cls, value: float) -> float:
        if not math.isfinite(value) or value < 0:
            raise ValueError("minimum_improvement must be finite and non-negative")
        return value


class ProtectedMetric(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=255)
    direction: Direction
    maximum_degradation: float = 0.0
    minimum: float | None = None
    maximum: float | None = None
    critical: bool = False

    _strip_name = field_validator("name")(_normalize_text)

    @field_validator("maximum_degradation", "minimum", "maximum")
    @classmethod
    def finite_bounds(cls, value: float | None, info):
        if value is None:
            return None
        if not math.isfinite(value):
            raise ValueError("metric bounds must be finite")
        if info.field_name == "maximum_degradation" and value < 0:
            raise ValueError("maximum_degradation must be non-negative")
        return value

    @model_validator(mode="after")
    def ordered_bounds(self):
        if self.minimum is not None and self.maximum is not None and self.minimum > self.maximum:
            raise ValueError("minimum cannot exceed maximum")
        return self


class ImprovementContract(BaseModel):
    model_config = ConfigDict(extra="forbid")

    primary_metric: MetricObjective
    protected_metrics: list[ProtectedMetric] = Field(default_factory=list, max_length=100)

    @field_validator("protected_metrics")
    @classmethod
    def unique_protected_metrics(cls, values: list[ProtectedMetric]):
        names = [value.name for value in values]
        if len(names) != len(set(names)):
            raise ValueError("protected metric names must be unique")
        return values


class EvalSuiteCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=255)
    version: str = Field(min_length=1, max_length=255)
    description: str | None = Field(default=None, max_length=4000)
    case_ids: list[UUID] = Field(min_length=1, max_length=1000)
    improvement_contract: ImprovementContract
    repetitions: int = Field(default=2, ge=2, le=100)

    _strip_labels = field_validator("name", "version")(_normalize_text)

    @field_validator("case_ids")
    @classmethod
    def unique_cases(cls, values: list[UUID]):
        if len(values) != len(set(values)):
            raise ValueError("case_ids must be unique")
        return values


class EvalSuiteOut(BaseModel):
    id: UUID
    namespace: str
    barrier_group: str | None
    name: str
    version: str
    description: str | None
    case_ids: list[UUID]
    improvement_contract: ImprovementContract
    repetitions: int
    suite_hash: str
    created_by_principal_ref: str
    created_at: datetime


class MetricResultCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=255)
    metric_type: MetricType
    value: float
    unit: str = Field(min_length=1, max_length=64)
    provenance: MetricProvenance
    scorer_id: str = Field(min_length=1, max_length=255)
    scorer_version: str = Field(min_length=1, max_length=255)
    scorer_config_hash: str = Field(pattern=SHA256_PATTERN)
    evidence_refs: list[str] = Field(default_factory=list, max_length=100)
    limitations: list[str] = Field(default_factory=list, max_length=100)

    _strip_labels = field_validator("name", "unit", "scorer_id", "scorer_version")(_normalize_text)

    @field_validator("value")
    @classmethod
    def finite_value(cls, value: float):
        if not math.isfinite(value):
            raise ValueError("metric value must be finite")
        return value

    @field_validator("evidence_refs", "limitations")
    @classmethod
    def normalized_evidence(cls, values: list[str]) -> list[str]:
        normalized = sorted({_normalize_text(value) for value in values})
        if any(len(value) > 2048 for value in normalized):
            raise ValueError("evidence and limitation entries cannot exceed 2048 characters")
        return normalized


class TrialCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    case_id: UUID
    repetition: int = Field(ge=0, le=99)
    seed: int = Field(ge=0, le=2_147_483_647)
    input_hash: str = Field(pattern=SHA256_PATTERN)
    output_hash: str = Field(pattern=SHA256_PATTERN)
    configuration_hash: str = Field(pattern=SHA256_PATTERN)
    latency_ms: float | None = Field(default=None, ge=0)
    input_tokens: int | None = Field(default=None, ge=0)
    output_tokens: int | None = Field(default=None, ge=0)
    cost: float | None = Field(default=None, ge=0)
    cost_currency: str | None = Field(default=None, pattern=r"^[A-Z]{3}$")
    started_at: datetime
    completed_at: datetime
    metrics: list[MetricResultCreate] = Field(min_length=1, max_length=256)

    @model_validator(mode="after")
    def validate_trial(self):
        if self.completed_at < self.started_at:
            raise ValueError("completed_at cannot precede started_at")
        if (self.cost is None) != (self.cost_currency is None):
            raise ValueError("cost and cost_currency must be supplied together")
        names = [metric.name for metric in self.metrics]
        if len(names) != len(set(names)):
            raise ValueError("metric names must be unique within a trial")
        return self


class EvalRunCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    suite_id: UUID
    agent_version_id: UUID
    environment: dict[str, Any] = Field(default_factory=dict, max_length=256)
    capture_limitations: list[str] = Field(default_factory=list, max_length=100)
    trials: list[TrialCreate] = Field(min_length=2, max_length=100_000)

    @field_validator("capture_limitations")
    @classmethod
    def normalized_limitations(cls, values: list[str]) -> list[str]:
        return sorted({_normalize_text(value) for value in values})

    @field_validator("trials")
    @classmethod
    def unique_trial_keys(cls, values: list[TrialCreate]):
        keys = [(trial.case_id, trial.repetition) for trial in values]
        if len(keys) != len(set(keys)):
            raise ValueError("case_id/repetition trial pairs must be unique")
        return values


class MetricResultOut(MetricResultCreate):
    id: UUID
    result_hash: str


class TrialOut(BaseModel):
    id: UUID
    case_id: UUID
    repetition: int
    seed: int
    input_hash: str
    output_hash: str
    configuration_hash: str
    latency_ms: float | None
    input_tokens: int | None
    output_tokens: int | None
    cost: float | None
    cost_currency: str | None
    started_at: datetime
    completed_at: datetime
    trial_hash: str
    metrics: list[MetricResultOut]


class EvalRunOut(BaseModel):
    id: UUID
    namespace: str
    barrier_group: str | None
    suite_id: UUID
    agent_version_id: UUID
    environment: dict[str, Any]
    capture_limitations: list[str]
    trial_count: int
    run_hash: str
    created_by_principal_ref: str
    completed_at: datetime
    trials: list[TrialOut]


class ComparisonCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    baseline_run_id: UUID
    candidate_run_id: UUID

    @model_validator(mode="after")
    def distinct_runs(self):
        if self.baseline_run_id == self.candidate_run_id:
            raise ValueError("baseline and candidate runs must differ")
        return self


class MetricAggregate(BaseModel):
    name: str
    direction: Direction
    baseline_mean: float
    candidate_mean: float
    baseline_variance: float
    candidate_variance: float
    baseline_ci95: list[float]
    candidate_ci95: list[float]
    raw_delta: float
    improvement: float
    sample_size_baseline: int
    sample_size_candidate: int


class ProtectedMetricResult(BaseModel):
    name: str
    passed: bool
    critical: bool
    degradation: float
    maximum_degradation: float
    minimum_passed: bool
    maximum_passed: bool


class ComparisonOut(BaseModel):
    id: UUID
    namespace: str
    barrier_group: str | None
    suite_id: UUID
    baseline_run_id: UUID
    candidate_run_id: UUID
    primary_metric: str
    primary_improvement: float
    aggregates: list[MetricAggregate]
    protected_results: list[ProtectedMetricResult]
    critical_invariants_passed: bool
    verdict: Literal["eligible_for_review", "no_verified_improvement", "protected_regression"]
    comparison_hash: str
    created_by_principal_ref: str
    created_at: datetime


class EvaluationAttestationCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    comparison_id: UUID
    claims: list[str] = Field(default_factory=list, max_length=100)
    limitations: list[str] = Field(default_factory=list, max_length=100)

    @field_validator("claims", "limitations")
    @classmethod
    def normalized_claims(cls, values: list[str]) -> list[str]:
        normalized = sorted({_normalize_text(value) for value in values})
        if any(len(value) > 2000 for value in normalized):
            raise ValueError("claims and limitations cannot exceed 2000 characters")
        return normalized


class EvaluationAttestationOut(BaseModel):
    id: UUID
    namespace: str
    barrier_group: str | None
    schema_version: Literal["0.1"]
    comparison_id: UUID
    payload: dict[str, Any]
    payload_hash: str
    signature_algorithm: Literal["ed25519"]
    signing_key_id: str
    signing_public_key: str
    signature: str
    created_by_principal_ref: str
    attested_at: datetime


class EvaluationAttestationVerifyRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    attestation: EvaluationAttestationOut
    trusted_public_key: str | None = None


class EvaluationAttestationVerification(BaseModel):
    valid: bool
    payload_hash_valid: bool
    signature_valid: bool
    errors: list[str]


class OptimizationStudyCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=255)
    suite_id: UUID
    baseline_agent_version_id: UUID
    comparison_ids: list[UUID] = Field(min_length=1, max_length=100)
    objective: dict[str, Any] = Field(default_factory=dict, max_length=256)

    _strip_name = field_validator("name")(_normalize_text)

    @field_validator("comparison_ids")
    @classmethod
    def unique_comparisons(cls, values: list[UUID]):
        if len(values) != len(set(values)):
            raise ValueError("comparison_ids must be unique")
        return values


class OptimizationCandidateOut(BaseModel):
    id: UUID
    agent_version_id: UUID
    comparison_id: UUID
    rank: int
    eligible: bool
    score: float
    candidate_hash: str


class RecommendationOut(BaseModel):
    id: UUID
    candidate_id: UUID
    disposition: Literal["recommend_for_human_review", "do_not_recommend"]
    rationale: dict[str, Any]
    requires_human_approval: Literal[True]
    recommendation_hash: str
    created_at: datetime


class OptimizationStudyOut(BaseModel):
    id: UUID
    namespace: str
    barrier_group: str | None
    name: str
    suite_id: UUID
    baseline_agent_version_id: UUID
    objective: dict[str, Any]
    status: Literal["advisory"]
    study_hash: str
    created_by_principal_ref: str
    created_at: datetime
    candidates: list[OptimizationCandidateOut]
    recommendations: list[RecommendationOut]


__all__ = [name for name in globals() if not name.startswith("_")]
