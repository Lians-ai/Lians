"""API contracts for the normalized decision-evidence graph."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal, TypeAlias
from uuid import UUID

from pydantic import BaseModel, Field, field_validator, model_validator

from .schemas import DecisionImpactItem, DecisionOut

EvidenceArtifactKind: TypeAlias = Literal[
    "source",
    "policy",
    "model",
    "tool",
    "permission",
    "instruction",
    "input",
    "output",
]
EvidenceRelation: TypeAlias = Literal["direct", "reachable"]


class EvidenceArtifactCreate(BaseModel):
    kind: EvidenceArtifactKind
    identifier: str = Field(min_length=1, max_length=1024)
    version: str | None = Field(default=None, min_length=1, max_length=512)
    hash_algorithm: str = Field(default="sha256", min_length=1, max_length=32)
    artifact_hash: str | None = Field(default=None, min_length=1, max_length=256)
    metadata: dict[str, Any] = Field(default_factory=dict)
    risk_metadata: dict[str, Any] = Field(default_factory=dict)
    barrier_group: str | None = Field(default=None, min_length=1, max_length=255)
    created_by_agent_id: str | None = Field(default=None, min_length=1, max_length=255)

    @field_validator("identifier", "version")
    @classmethod
    def strip_identity_fields(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = value.strip()
        if not normalized:
            raise ValueError("identity fields cannot be blank")
        return normalized

    @field_validator("barrier_group", "created_by_agent_id")
    @classmethod
    def strip_optional_labels(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = value.strip()
        if not normalized:
            raise ValueError("optional labels cannot be blank")
        return normalized

    @field_validator("hash_algorithm")
    @classmethod
    def normalize_hash_algorithm(cls, value: str) -> str:
        normalized = value.strip().casefold()
        compacted = normalized.replace("-", "")
        if compacted.startswith("sha") or compacted.startswith("blake"):
            normalized = compacted
        if not normalized:
            raise ValueError("hash_algorithm cannot be blank")
        return normalized

    @field_validator("artifact_hash")
    @classmethod
    def normalize_artifact_hash(cls, value: str | None) -> str | None:
        if value is None:
            return None
        stripped = value.strip()
        if not stripped:
            raise ValueError("artifact_hash cannot be blank")
        return stripped

    @model_validator(mode="after")
    def validate_hash(self):
        if self.artifact_hash is None:
            return self
        if self.hash_algorithm == "sha256":
            normalized_hash = self.artifact_hash.casefold()
            if len(normalized_hash) != 64 or any(
                char not in "0123456789abcdef" for char in normalized_hash
            ):
                raise ValueError("sha256 artifact_hash must be exactly 64 hexadecimal chars")
            self.artifact_hash = normalized_hash
        return self


class EvidenceArtifactOut(BaseModel):
    id: UUID
    namespace: str
    barrier_group: str | None
    kind: EvidenceArtifactKind
    identifier: str
    version: str | None
    coordinate: str
    hash_algorithm: str
    artifact_hash: str | None
    identity_hash: str
    metadata: dict[str, Any]
    risk_metadata: dict[str, Any]
    created_by_agent_id: str | None
    recorded_at: datetime


class DecisionEvidenceLinkCreate(BaseModel):
    artifact_id: UUID
    relation: EvidenceRelation = "direct"
    match_basis: list[str] = Field(default_factory=list, max_length=100)
    risk_metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator("match_basis")
    @classmethod
    def normalize_match_basis(cls, values: list[str]) -> list[str]:
        normalized = sorted({value.strip() for value in values if value.strip()})
        if any(len(value) > 512 for value in normalized):
            raise ValueError("match_basis entries cannot exceed 512 characters")
        return normalized


class DecisionEvidenceLinkOut(BaseModel):
    id: UUID
    namespace: str
    barrier_group: str | None
    decision_id: UUID
    artifact_id: UUID
    relation: EvidenceRelation
    match_basis: list[str]
    risk_metadata: dict[str, Any]
    risk_score: int | None = Field(default=None, ge=0, le=100)
    risk_level: Literal["critical", "high", "medium", "low"] | None = None
    recorded_at: datetime
    artifact: EvidenceArtifactOut | None = None


class EvidenceKindCoverageOut(BaseModel):
    kind: EvidenceArtifactKind
    status: Literal["unknown", "partial", "complete"]
    indexer_version: str
    normalization_scope: str
    source_watermark: str | None
    gap_codes: list[str]
    indexed_artifact_count: int = Field(ge=0)
    assessed_at: datetime | None


class DecisionEvidenceCoverageOut(BaseModel):
    decision_id: UUID
    namespace: str
    coverage_sequence: int = Field(ge=0)
    overall_status: Literal["unknown", "partial", "complete"]
    normalized_complete: bool
    kinds: list[EvidenceKindCoverageOut]
    disclosure: str


class EvidenceGraphCoverage(BaseModel):
    indexed_links: int
    indexed_artifacts: int
    legacy_memory_references: int
    unindexed_legacy_memory_references: int
    unindexed_legacy_memory_ids: list[UUID]
    unindexed_legacy_memory_ids_truncated: bool
    coverage_sequence: int
    overall_status: Literal["unknown", "partial", "complete"]
    kinds: list[EvidenceKindCoverageOut]
    normalized_complete: bool
    normalization_scope: Literal["persisted_per_kind_watermarks"] = (
        "persisted_per_kind_watermarks"
    )


class DecisionEvidenceGraphOut(BaseModel):
    decision_id: UUID
    namespace: str
    links_total: int = Field(ge=0)
    links_returned: int = Field(ge=0)
    links_complete: bool
    has_more: bool
    next_relation: EvidenceRelation | None = None
    next_link_id: UUID | None = None
    artifacts_total: int = Field(ge=0)
    artifacts_returned: int = Field(ge=0)
    direct_count: int
    reachable_count: int
    artifacts: list[EvidenceArtifactOut]
    links: list[DecisionEvidenceLinkOut]
    coverage: EvidenceGraphCoverage


class EvidenceDependencyChange(BaseModel):
    dependency_kind: EvidenceArtifactKind
    dependency_value: str = Field(min_length=1, max_length=1537)
    change_type: Literal[
        "changed",
        "corrected",
        "retired",
        "revoked",
        "recalled",
        "corrupted",
        "erased",
    ] = "changed"
    occurred_at: datetime | None = None
    note: str | None = Field(default=None, max_length=2000)
    agent_id: str = Field(default="lians-impact-monitor", min_length=1, max_length=255)
    limit: int = Field(default=100, ge=1, le=1000)
    record_event: bool = True

    @field_validator("dependency_value")
    @classmethod
    def normalize_dependency_value(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("dependency_value cannot be blank")
        return normalized


class IndexedDecisionImpactResult(BaseModel):
    dependency: dict[str, str]
    change_type: str
    assessed_at: datetime
    total: int
    direct_count: int
    reachable_count: int
    search_truncated: bool
    change_event_id: UUID | None
    items: list[DecisionImpactItem]
    analysis_mode: Literal["indexed", "hybrid_legacy_fallback", "legacy_fallback"]
    indexed_decisions_matched: int
    legacy_decisions_matched: int
    legacy_candidates_scanned: int
    legacy_fallback_truncated: bool
    total_is_lower_bound: bool
    legacy_fallback_scope: Literal["incomplete_kind_coverage"] = (
        "incomplete_kind_coverage"
    )


class ExhaustiveImpactAssessmentCreate(BaseModel):
    idempotency_key: str = Field(min_length=1, max_length=512)
    dependency_kind: EvidenceArtifactKind
    dependency_value: str = Field(min_length=1, max_length=1537)
    change_type: Literal[
        "changed",
        "corrected",
        "retired",
        "revoked",
        "recalled",
        "corrupted",
        "erased",
    ] = "changed"
    occurred_at: datetime | None = None
    note: str | None = Field(default=None, max_length=2000)
    record_event: bool = True

    @field_validator("idempotency_key", "dependency_value")
    @classmethod
    def normalize_required_text(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("value cannot be blank")
        return normalized


class ExhaustiveImpactAssessmentAdvance(BaseModel):
    page_size: int = Field(default=250, ge=1, le=500)
    max_pages: int = Field(default=1, ge=1, le=20)


class ExhaustiveImpactAssessmentStatus(BaseModel):
    id: UUID
    namespace: str
    barrier_group: str | None
    dependency: dict[str, str]
    change_type: str
    status: Literal["pending", "running", "completed", "failed"]
    snapshot_max_coverage_sequence: int = Field(ge=0)
    snapshot_max_link_sequence: int = Field(ge=0)
    snapshot_decision_count: int = Field(ge=0)
    cursor_coverage_sequence: int = Field(ge=0)
    decisions_scanned: int = Field(ge=0)
    fallback_candidates_scanned: int = Field(ge=0)
    indexed_decisions_matched: int = Field(ge=0)
    legacy_decisions_matched: int = Field(ge=0)
    matches_found: int = Field(ge=0)
    direct_count: int = Field(ge=0)
    reachable_count: int = Field(ge=0)
    pages_completed: int = Field(ge=0)
    record_event: bool
    completion_event_id: UUID | None
    failure_code: str | None
    processing_attempts: int = Field(ge=0)
    consecutive_failures: int = Field(ge=0)
    attempt_limit: int = Field(ge=1, le=100)
    next_attempt_at: datetime
    lease_expires_at: datetime | None
    heartbeat_at: datetime | None
    last_attempt_at: datetime | None
    last_error_code: str | None
    last_error_digest: str | None
    created_at: datetime
    started_at: datetime | None
    updated_at: datetime
    completed_at: datetime | None
    failed_at: datetime | None
    snapshot_complete: bool
    completion_scope: Literal["explicit_registration_sequence_snapshot"] = (
        "explicit_registration_sequence_snapshot"
    )
    disclosure: str


class ExhaustiveImpactAssessmentMatchOut(BaseModel):
    sequence: int = Field(ge=1)
    decision: DecisionOut
    match_basis: list[str]
    impact_status: Literal["direct_reference", "reachable"]
    risk_score: int = Field(ge=0, le=100)
    priority: Literal["critical", "high", "medium", "low"]
    match_sources: list[Literal["indexed", "legacy_fallback"]]


class ExhaustiveImpactAssessmentResults(BaseModel):
    assessment_id: UUID
    status: Literal["pending", "running", "completed", "failed"]
    snapshot_complete: bool
    total_matches: int = Field(ge=0)
    items: list[ExhaustiveImpactAssessmentMatchOut]
    next_cursor: int | None
