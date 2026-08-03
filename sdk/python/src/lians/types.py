"""
Lians Python SDK — Pydantic v2 type definitions.

Mirrors the REST API schemas.  All datetime fields are UTC-aware.
"""
from __future__ import annotations

from datetime import datetime
from typing import Any, Generic, Literal, Optional, TypedDict, TypeVar
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

_PageItem = TypeVar("_PageItem")

# ── Write ─────────────────────────────────────────────────────────────────────

class MemoryAdd(BaseModel):
    agent_id: str
    content: str
    event_time: datetime
    source: Optional[str] = None
    subject_id: Optional[str] = None
    metadata: dict[str, Any] = Field(default_factory=dict)
    importance: float = Field(default=0.5, ge=0.0, le=1.0)


# ── Core memory object ────────────────────────────────────────────────────────

class MemoryOut(BaseModel):
    id: UUID
    namespace: str
    agent_id: str
    content: Optional[str]              # None if erased
    subject_id: Optional[str]
    event_time: datetime
    ingestion_time: datetime
    valid_from: datetime
    valid_to: Optional[datetime]        # None = still currently valid
    superseded_by: Optional[UUID]
    supersession_confidence: Optional[float]
    barrier_group: Optional[str] = None
    importance: float
    source: Optional[str]
    content_hash: str
    erased_at: Optional[datetime]
    metadata: dict[str, Any]


# ── Recall ────────────────────────────────────────────────────────────────────

class RecallRequest(BaseModel):
    agent_id: str
    query: str
    k: int = Field(default=5, ge=1, le=100)
    as_of: Optional[datetime] = None
    filters: dict[str, Any] = Field(default_factory=dict)


class RecallResult(BaseModel):
    memories: list[MemoryOut]
    as_of: Optional[datetime]
    total_candidates: int
    retrieval_degraded: bool = False
    graph_search_complete: bool = True
    candidate_window_complete: bool = True
    candidates_considered: int = 0
    candidate_limit: int = 0
    candidate_mode: str = "exact"


# ── Batch ─────────────────────────────────────────────────────────────────────

class MemoryBatchResult(BaseModel):
    added: int
    memories: list[MemoryOut]


# ── Erasure ───────────────────────────────────────────────────────────────────

class EraseRequest(BaseModel):
    subject_id: str
    request_ref: str


class SubjectErasureSnapshot(BaseModel):
    memories: int
    live_facts: int
    relationships: int
    pending_admissions: int
    total_rows: int


class SubjectErasureProgress(BaseModel):
    memories: int
    live_facts: int
    relationships: int
    pending_admissions: int
    rows_scrubbed: int
    pages_completed: int
    ratio: float


class EraseResult(BaseModel):
    job_id: UUID
    namespace: str
    subject_ref: str
    request_ref: str
    status: Literal["pending", "running", "completed", "failed"]
    phase: Literal[
        "memories",
        "live_facts",
        "relationships",
        "pending_admissions",
        "finalizing",
        "completed",
    ]
    key_destroyed_at: datetime
    cache_fenced_at: datetime
    snapshot: SubjectErasureSnapshot
    progress: SubjectErasureProgress
    processing_attempts: int
    next_attempt_at: datetime
    last_error_code: Optional[str]
    last_error_digest: Optional[str]
    failure_code: Optional[str]
    created_at: datetime
    started_at: Optional[datetime]
    updated_at: datetime
    completed_at: Optional[datetime]
    replayed: bool


class ErasureMemoryHash(BaseModel):
    memory_id: UUID
    content_hash: str


class ErasureCertificate(BaseModel):
    certificate_id: UUID
    job_id: UUID
    namespace: str
    subject_ref: str
    request_ref: str
    key_destroyed_at: datetime
    completed_at: datetime
    memories_erased: int
    live_facts_erased: int
    relationships_erased: int
    pending_admissions_erased: int
    manifest_sha256: str
    manifest_algorithm: Literal["lians-subject-erasure-memory-manifest-v1"]
    evidence: list[ErasureMemoryHash]
    content_hashes: list[str]
    hashes_returned: int
    hashes_total: int
    hashes_complete: bool
    has_more: bool
    next_memory_id: Optional[UUID]
    audit_event_id: UUID
    audit_row_hash: str
    chain_status: Literal["unchecked"]
    generated_at: datetime


# ── Lineage ───────────────────────────────────────────────────────────────────

class LineageNode(BaseModel):
    id: UUID
    content: Optional[str]
    content_hash: str
    event_time: datetime
    ingestion_time: datetime
    valid_from: datetime
    valid_to: Optional[datetime]
    source: Optional[str]
    importance: float
    supersession_confidence: Optional[float]
    erased_at: Optional[datetime]
    metadata: dict[str, Any]
    is_current: bool


class LineageEdge(BaseModel):
    from_id: UUID
    to_id: UUID
    relation: str
    confidence: float
    rationale: Optional[str]
    adjudication_stage: int
    superseded_at: datetime


class MemoryLineageResult(BaseModel):
    agent_id: str
    namespace: str
    queried_id: UUID
    root_id: UUID
    tip_id: UUID
    depth: int
    truncated: bool = False
    root_complete: bool = True
    tip_complete: bool = True
    max_nodes: int = 1000
    nodes: list[LineageNode]
    edges: list[LineageEdge]


# ── Fact history ──────────────────────────────────────────────────────────────

class FactHistoryResult(BaseModel):
    ticker: str
    metric: str
    agent_id: str
    namespace: str
    total: int
    total_is_lower_bound: bool = False
    has_more: bool = False
    scan_complete: bool = True
    rows_scanned: int = 0
    scan_limit: int = 0
    items: list[MemoryOut]


# ── Knowledge snapshot ────────────────────────────────────────────────────────

class KnowledgeSnapshot(BaseModel):
    agent_id: str
    namespace: str
    as_of: datetime
    recorded_as_of: datetime
    total: int
    returned: int
    complete: bool
    has_more: bool
    next_event_time: Optional[datetime] = None
    next_id: Optional[UUID] = None
    items: list[MemoryOut]


# ── Backtest contamination ────────────────────────────────────────────────────

class ContaminationFlag(BaseModel):
    memory_id: UUID
    event_time: datetime
    ingestion_time: datetime
    contamination_type: str          # "future_event" | "late_revision"
    delta_days: float
    content_preview: Optional[str]
    source: Optional[str]
    metadata: dict[str, Any]


class ContaminationReport(BaseModel):
    agent_id: str
    namespace: str
    simulation_as_of: datetime
    memories_checked: int
    flags_total: int
    flags_returned: int
    flags_complete: bool
    has_more: bool
    next_event_time: Optional[datetime] = None
    next_id: Optional[UUID] = None
    flags: list[ContaminationFlag]
    contamination_rate: float
    is_clean: bool


# ── Conflicts ─────────────────────────────────────────────────────────────────

class ConflictFlagOut(BaseModel):
    id: UUID
    namespace: str
    agent_id: str
    memory_a_id: UUID
    memory_b_id: UUID
    memory_a_content: Optional[str]
    memory_b_content: Optional[str]
    memory_a_source: Optional[str]
    memory_b_source: Optional[str]
    memory_a_event_time: datetime
    memory_b_event_time: datetime
    confidence: float
    detected_at: datetime
    status: str
    resolved_at: Optional[datetime]
    resolver_note: Optional[str]


class ConflictListResult(BaseModel):
    conflicts: list[ConflictFlagOut]
    total: int
    returned: int
    complete: bool
    has_more: bool
    next_detected_at: Optional[datetime] = None
    next_id: Optional[UUID] = None
    status_filter: Optional[str]


class ConflictResolveRequest(BaseModel):
    resolution: Literal["accept_a", "accept_b", "dismiss"]
    note: Optional[str] = None


class ConflictResolveResult(BaseModel):
    conflict_id: UUID
    resolution: str
    resolved_at: datetime
    memory_invalidated: Optional[UUID]


# ── Supersession review ───────────────────────────────────────────────────────

class SupersessionReviewItem(BaseModel):
    event_id: UUID
    memory_id: UUID
    superseded_by: Optional[UUID]
    confidence: float
    relation: str
    rationale: Optional[str]
    adjudication_stage: int
    created_at: datetime
    content_hash: Optional[str]


class SupersessionReviewResult(BaseModel):
    items: list[SupersessionReviewItem]
    total: int
    returned: int
    complete: bool
    has_more: bool
    next_chain_position: Optional[int] = None
    confidence_threshold: float


class SupersessionActionResult(BaseModel):
    memory_id: UUID
    action: Literal["confirm", "reject"]
    applied_at: datetime


# ── Audit / chain ─────────────────────────────────────────────────────────────

class AuditEvent(BaseModel):
    id: UUID
    namespace: str
    agent_id: str
    op: str
    memory_id: Optional[UUID]
    content_hash: Optional[str]
    payload: dict[str, Any]
    created_at: datetime
    prev_hash: Optional[str]
    row_hash: Optional[str]
    hash_version: int
    chain_position: int


class AuditChainViolation(BaseModel):
    row_id: str
    kind: str
    detail: str


class AuditChainVerifyResult(BaseModel):
    namespace: str
    rows_checked: int
    status: Literal["ok", "partial", "tampered"]
    truncated: bool
    chain_tip: Optional[str]
    violations: list[AuditChainViolation]


class AuditExportResult(BaseModel):
    namespace: str
    from_: Optional[datetime] = None
    to: Optional[datetime] = None
    total_rows: int
    returned_rows: int
    has_more: bool
    complete: bool
    next_chain_position: Optional[int] = None
    snapshot_max_chain_position: int
    chain_status: Optional[str]
    chain_violations: Optional[list[AuditChainViolation]]
    chain_rows_checked: Optional[int]
    chain_truncated: Optional[bool]
    chain_tip: Optional[str]
    events: list[AuditEvent]


# ── Compliance report ─────────────────────────────────────────────────────────

class ComplianceMemorySummary(BaseModel):
    total_memories: int
    active_memories: int
    superseded_memories: int
    erased_memories: int
    new_in_window: int
    superseded_in_window: int


class ComplianceAuditChain(BaseModel):
    status: str
    rows_checked: int
    violations: list[dict[str, Any]]


class ComplianceErasures(BaseModel):
    total_requests: int
    total_records_erased: int
    subject_ids: list[str]
    subject_ids_total: int
    subject_ids_complete: bool
    subject_ids_limit: int


class ComplianceConflicts(BaseModel):
    open: int
    resolved_accept_a: int
    resolved_accept_b: int
    dismissed: int
    detected_in_window: int


class ComplianceSupersessions(BaseModel):
    total_supersessions: int
    confirmed_by_human: int
    rejected_by_human: int
    high_confidence: int
    low_confidence: int


class ComplianceRetention(BaseModel):
    content_ttl_days: Optional[int]
    audit_retention_days: int
    legal_hold: bool
    stripe_customer_id: Optional[str]


class ComplianceReport(BaseModel):
    namespace: str
    generated_at: datetime
    window_from: Optional[datetime]
    window_to: Optional[datetime]
    summary: ComplianceMemorySummary
    audit_chain: ComplianceAuditChain
    erasures: ComplianceErasures
    conflicts: ComplianceConflicts
    supersessions: ComplianceSupersessions
    retention: Optional[ComplianceRetention]


# ── Webhooks ──────────────────────────────────────────────────────────────────

class WebhookEndpoint(BaseModel):
    id: UUID
    namespace: str
    url: str
    events: list[str]
    enabled: bool
    description: Optional[str]
    created_at: datetime
    updated_at: datetime


class WebhookRegisterRequest(BaseModel):
    url: str
    events: list[str]
    secret: Optional[str] = None
    description: Optional[str] = None


class WebhookRegisterResult(BaseModel):
    endpoint: WebhookEndpoint
    secret: str = Field(
        repr=False,
        json_schema_extra={"readOnly": True, "x-sensitive": True},
    )  # shown once at registration


class WebhookUpdateRequest(BaseModel):
    expected_updated_at: datetime
    enabled: Optional[bool] = None
    events: Optional[list[str]] = None
    description: Optional[str] = None


class WebhookDelivery(BaseModel):
    id: UUID
    event_type: str
    attempt: int
    status_code: Optional[int]
    error: Optional[str]
    delivered_at: Optional[datetime]
    created_at: datetime


class WebhookDeliveryListResult(BaseModel):
    deliveries: list[WebhookDelivery]
    total: int
    returned: int
    complete: bool
    has_more: bool = False
    next_after_created_at: Optional[datetime] = None
    next_after_id: Optional[UUID] = None


# ── Universal Recorder ──────────────────────────────────────────────────────

# Decision impact analysis

DecisionDependencyKind = Literal[
    "source",
    "policy",
    "model",
    "tool",
    "permission",
    "instruction",
    "input",
    "output",
]
DecisionDependencyChangeType = Literal[
    "changed",
    "corrected",
    "retired",
    "revoked",
    "recalled",
    "corrupted",
    "erased",
]
DecisionImpactAnalysisMode = Literal[
    "indexed",
    "hybrid_legacy_fallback",
    "legacy_fallback",
]
ExhaustiveImpactAssessmentState = Literal[
    "pending",
    "running",
    "completed",
    "failed",
]


class DecisionOut(TypedDict):
    id: str
    namespace: str
    agent_id: str
    recorded_by_principal_ref: str
    recorded_by_auth_method: str
    recorded_by_credential_ref: str | None
    recorded_by_principal_type: str | None
    recorded_by_role: Literal["owner", "analyst", "compliance", "readonly"] | None
    recorded_by_scopes: list[str]
    decision_type: str
    outcome: str
    reason_codes: list[str]
    regime: str | None
    subject_id: str | None
    session_id: str | None
    model_id: str | None
    model_version: str | None
    policy_version: str | None
    decided_at: str
    recorded_at: str
    knowledge_as_of: str
    knowledge_recorded_as_of: str
    evidence_memory_ids: list[str]
    input_hash: str | None
    output_hash: str | None
    human_review_status: str
    human_reviewer: str | None
    human_reviewed_at: str | None
    supersedes_id: str | None
    metadata: dict[str, Any]
    record_hash_version: int
    record_integrity_status: Literal["verified", "legacy_unverified"]
    record_hash: str


class CompatibilityListPage(TypedDict, Generic[_PageItem]):
    items: list[_PageItem]
    total: int
    limit: int
    returned: int
    has_more: bool
    page_complete: bool
    collection_complete: bool
    next_cursor: dict[str, str] | None


class LedgerEventOut(TypedDict):
    id: str
    namespace: str
    event_type: str
    agent_id: str
    occurred_at: str
    recorded_at: str
    subject_id: str | None
    session_id: str | None
    decision_id: str | None
    model_id: str | None
    model_version: str | None
    payload: dict[str, Any]
    artifact_hash: str | None
    event_hash: str


class EvidenceArtifactOut(TypedDict):
    id: str
    namespace: str
    barrier_group: str | None
    kind: DecisionDependencyKind
    identifier: str
    version: str | None
    coordinate: str
    hash_algorithm: str
    artifact_hash: str | None
    identity_hash: str
    metadata: dict[str, Any]
    risk_metadata: dict[str, Any]
    created_by_agent_id: str | None
    recorded_at: str


class DecisionEvidenceGraphResult(TypedDict):
    decision_id: str
    namespace: str
    links_total: int
    links_returned: int
    links_complete: bool
    has_more: bool
    next_relation: Literal["direct", "reachable"] | None
    next_link_id: str | None
    artifacts_total: int
    artifacts_returned: int
    direct_count: int
    reachable_count: int
    artifacts: list[dict[str, Any]]
    links: list[dict[str, Any]]
    coverage: dict[str, Any]


class DecisionReviewEvent(TypedDict):
    id: str
    namespace: str
    barrier_group: str | None
    decision_id: str
    sequence: int
    status: str
    reviewer_principal_id: str
    reviewer_principal_type: str | None
    reviewer_role: str | None
    auth_method: str
    credential_id: str | None
    note: str | None
    note_hash: str | None
    prior_event_hash: str | None
    event_hash: str
    reviewed_at: str


class DecisionReviewHistoryResult(TypedDict):
    decision_id: str
    total: int
    returned: int
    complete: bool
    has_more: bool
    next_sequence: int | None
    page_chain_verified: bool
    chain_scope_complete: bool
    events: list[DecisionReviewEvent]


class DecisionDependency(TypedDict):
    kind: DecisionDependencyKind
    value: str


class DecisionDependencyChangeRequired(TypedDict):
    dependency_kind: DecisionDependencyKind
    dependency_value: str


class DecisionDependencyChange(DecisionDependencyChangeRequired, total=False):
    change_type: DecisionDependencyChangeType
    occurred_at: str | None
    note: str | None
    agent_id: str
    limit: int
    record_event: bool


class DecisionImpactItem(TypedDict):
    decision: DecisionOut
    match_basis: list[str]
    impact_status: Literal["direct_reference", "reachable"]
    risk_score: int
    priority: Literal["critical", "high", "medium", "low"]


class DecisionImpactResult(TypedDict):
    dependency: DecisionDependency
    change_type: DecisionDependencyChangeType
    assessed_at: str
    total: int
    direct_count: int
    reachable_count: int
    search_truncated: bool
    change_event_id: str | None
    items: list[DecisionImpactItem]
    analysis_mode: DecisionImpactAnalysisMode
    indexed_decisions_matched: int
    legacy_decisions_matched: int
    legacy_candidates_scanned: int
    legacy_fallback_truncated: bool
    total_is_lower_bound: bool
    legacy_fallback_scope: Literal["incomplete_kind_coverage"]


class ExhaustiveImpactAssessmentCreateRequired(TypedDict):
    idempotency_key: str
    dependency_kind: DecisionDependencyKind
    dependency_value: str


class ExhaustiveImpactAssessmentCreate(
    ExhaustiveImpactAssessmentCreateRequired,
    total=False,
):
    change_type: DecisionDependencyChangeType
    occurred_at: str | None
    note: str | None
    record_event: bool


class ExhaustiveImpactAssessmentAdvance(TypedDict, total=False):
    page_size: int
    max_pages: int


class ExhaustiveImpactAssessmentStatus(TypedDict):
    id: str
    namespace: str
    barrier_group: str | None
    dependency: DecisionDependency
    change_type: DecisionDependencyChangeType
    status: ExhaustiveImpactAssessmentState
    snapshot_max_coverage_sequence: int
    snapshot_max_link_sequence: int
    snapshot_decision_count: int
    cursor_coverage_sequence: int
    decisions_scanned: int
    fallback_candidates_scanned: int
    indexed_decisions_matched: int
    legacy_decisions_matched: int
    matches_found: int
    direct_count: int
    reachable_count: int
    pages_completed: int
    record_event: bool
    completion_event_id: str | None
    failure_code: str | None
    created_at: str
    started_at: str | None
    updated_at: str
    completed_at: str | None
    snapshot_complete: bool
    completion_scope: Literal["explicit_registration_sequence_snapshot"]
    disclosure: str


class ExhaustiveImpactAssessmentMatch(TypedDict):
    sequence: int
    decision: DecisionOut
    match_basis: list[str]
    impact_status: Literal["direct_reference", "reachable"]
    risk_score: int
    priority: Literal["critical", "high", "medium", "low"]
    match_sources: list[Literal["indexed", "legacy_fallback"]]


class ExhaustiveImpactAssessmentResults(TypedDict):
    assessment_id: str
    status: ExhaustiveImpactAssessmentState
    snapshot_complete: bool
    total_matches: int
    items: list[ExhaustiveImpactAssessmentMatch]
    next_cursor: int | None


RecorderProtocol = Literal["lians", "otlp.genai", "mcp", "a2a"]
CaptureMode = Literal["metadata_only", "hash_only", "full"]


class RecorderActor(BaseModel):
    agent_id: Optional[str] = None
    principal_id: Optional[str] = None
    roles: list[str] = Field(default_factory=list)
    authentication_context: dict[str, Any] = Field(default_factory=dict)
    extensions: dict[str, Any] = Field(default_factory=dict)


class RecorderCorrelation(BaseModel):
    run_id: Optional[str] = None
    trace_id: Optional[str] = None
    span_id: Optional[str] = None
    parent_span_id: Optional[str] = None
    session_id: Optional[str] = None
    task_id: Optional[str] = None
    context_id: Optional[str] = None
    message_id: Optional[str] = None
    tool_call_id: Optional[str] = None
    decision_id: Optional[UUID] = None
    extensions: dict[str, Any] = Field(default_factory=dict)


class RecorderCapturePolicy(BaseModel):
    mode: CaptureMode = "hash_only"
    sensitive_fields: list[str] = Field(default_factory=list)


class RecorderEnvelope(BaseModel):
    schema_version: Literal["0.1"] = "0.1"
    protocol: RecorderProtocol
    event_type: Optional[str] = None
    event_id: Optional[str] = None
    idempotency_key: Optional[str] = None
    occurred_at: Optional[datetime] = None
    subject_id: Optional[str] = None
    actor: RecorderActor = Field(default_factory=RecorderActor)
    correlation: RecorderCorrelation = Field(default_factory=RecorderCorrelation)
    capture: RecorderCapturePolicy = Field(default_factory=RecorderCapturePolicy)
    payload: dict[str, Any] = Field(default_factory=dict)
    extensions: dict[str, Any] = Field(default_factory=dict)


class RecorderEvent(BaseModel):
    id: UUID
    run_id: UUID
    protocol: RecorderProtocol
    event_kind: str
    event_name: Optional[str]
    phase: str
    status: Optional[str]
    occurred_at: datetime
    recorded_at: datetime
    agent_id: Optional[str]
    actor_attribution: Literal["claimed_unverified", "not_supplied"]
    ingested_by_principal_ref: str
    ingested_by_auth_method: str
    ingested_by_credential_id: Optional[str]
    trace_id: Optional[str]
    span_id: Optional[str]
    task_id: Optional[str]
    decision_id: Optional[UUID]
    model_id: Optional[str]
    input_hash: Optional[str]
    output_hash: Optional[str]
    capture_mode: CaptureMode
    capture_gaps: list[str]
    diagnostics: list[dict[str, Any]]
    event_hash: str
    event_hash_version: Literal[1, 2]


class RecorderRunReadiness(BaseModel):
    run_id: UUID
    correlation_type: str
    boundary_kind: Literal["run", "decision"]
    status: str
    event_count: int
    protocols: list[RecorderProtocol]
    score: int
    receipt_ready: bool
    ready_at: Optional[datetime]
    missing_fields: list[str]
    diagnostics: list[dict[str, Any]]
    first_event_at: datetime
    last_event_at: datetime
    time_to_readiness_ms: Optional[int]


class RecorderIngestResult(BaseModel):
    accepted: bool
    duplicate: bool
    event: RecorderEvent
    readiness: RecorderRunReadiness


class RecorderEvidenceIndexJob(BaseModel):
    id: UUID
    decision_id: UUID
    status: Literal["pending", "running", "completed", "failed"]
    snapshot_max_recorded_at: datetime
    snapshot_max_event_id: UUID
    snapshot_event_count: int
    cursor_recorded_at: datetime | None
    cursor_event_id: UUID | None
    events_indexed: int
    events_remaining: int
    artifacts_created: int
    links_created: int
    pages_completed: int
    processing_attempts: int
    progress_ratio: float
    complete: bool
    next_attempt_at: datetime
    last_error_code: str | None
    last_error_digest: str | None
    failure_code: str | None
    created_at: datetime
    started_at: datetime | None
    updated_at: datetime
    completed_at: datetime | None
    failed_at: datetime | None


class RecorderBatchRejection(BaseModel):
    index: int
    code: str
    detail: str


class RecorderBatchResult(BaseModel):
    received: int
    accepted: int
    duplicates: int
    rejected: int
    results: list[RecorderIngestResult]
    rejections: list[RecorderBatchRejection]
    ready_run_ids: list[UUID]


class FirstReceiptReadiness(BaseModel):
    namespace: str
    evaluated_at: datetime
    total_runs: int
    ready_runs: int
    waiting_runs: int
    readiness_rate: float
    first_ready_run_id: Optional[UUID]
    first_ready_at: Optional[datetime]
    next_actions: list[str]
    runs: list[RecorderRunReadiness]


# ── Runtime Gate and investigations ─────────────────────────────────────────

RiskLevel = Literal["low", "medium", "high", "critical"]
GateDisposition = Literal["allow", "deny", "review"]


class IssuerCreate(BaseModel):
    actor_id: Optional[str] = None
    name: str
    issuer_uri: Optional[str] = None
    description: Optional[str] = None
    barrier_group: Optional[str] = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class ReceiptIssuer(BaseModel):
    model_config = ConfigDict(extra="allow")

    id: UUID
    namespace: str
    barrier_group: Optional[str]
    name: str
    issuer_uri: Optional[str]
    description: Optional[str]
    status: Literal["active", "revoked"]
    metadata: dict[str, Any]
    created_by: str
    created_at: datetime
    revoked_by: Optional[str]
    revoked_at: Optional[datetime]
    revocation_reason: Optional[str]


class TrustedKeyCreate(BaseModel):
    actor_id: Optional[str] = None
    key_id: str
    algorithm: Literal["ed25519"] = "ed25519"
    public_key: str
    valid_from: Optional[datetime] = None
    valid_until: Optional[datetime] = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class TrustedKeyRotate(TrustedKeyCreate):
    reason: str


class TrustedReceiptKey(BaseModel):
    model_config = ConfigDict(extra="allow")

    id: UUID
    namespace: str
    barrier_group: Optional[str]
    issuer_id: UUID
    key_id: str
    algorithm: Literal["ed25519"]
    public_key: str
    public_key_format: Literal["raw-base64"]
    fingerprint_sha256: str
    status: Literal["active", "revoked"]
    valid_from: datetime
    valid_until: Optional[datetime]
    created_by: str
    created_at: datetime
    revoked_by: Optional[str]
    revoked_at: Optional[datetime]
    revocation_reason: Optional[str]
    rotated_at: Optional[datetime]
    rotated_from_key_id: Optional[str]
    replaced_by_key_id: Optional[str]
    rotation_reason: Optional[str]
    metadata: dict[str, Any]


class GatePolicyRuleCreate(BaseModel):
    name: str
    description: Optional[str] = None
    priority: int = 100
    enabled: bool = True
    action_on_failure: Literal["deny", "review"] = "deny"
    applies_to_decision_types: list[str] = Field(default_factory=list)
    applies_to_risk_levels: list[RiskLevel] = Field(default_factory=list)
    required_receipt_grade: Optional[Literal["A", "B", "C", "D", "F"]] = None
    require_trusted_issuer: bool = False
    require_sources_current: bool = False
    require_policy_attached: bool = False
    required_principal_scopes: list[str] = Field(default_factory=list)
    minimum_approval_count: int = 0
    required_approval_roles: list[str] = Field(default_factory=list)
    allowed_approval_principal_types: list[Literal["human", "workload", "api_key"]] = Field(
        default_factory=list
    )
    maximum_approval_age_seconds: Optional[int] = None
    require_information_barrier_match: bool = False
    block_untrusted_content: bool = False
    max_untrusted_content_score: Optional[int] = None


class GatePolicySetCreate(BaseModel):
    actor_id: Optional[str] = None
    name: str
    version: str
    description: Optional[str] = None
    barrier_group: Optional[str] = None
    default_disposition: GateDisposition = "deny"
    protected_actions: list[str]
    target_ref_prefixes: list[str]
    enforcement_principal_ids: list[str]
    maximum_permit_ttl_seconds: int = Field(default=60, ge=1, le=300)
    rules: list[GatePolicyRuleCreate]
    metadata: dict[str, Any] = Field(default_factory=dict)


class GateReceiptContext(BaseModel):
    grade: Optional[Literal["A", "B", "C", "D", "F"]] = None
    receipt_hash: Optional[str] = None
    issuer_id: Optional[UUID] = None
    key_id: Optional[str] = None
    # Used for verification only. The server persists only a digest reference.
    document: Optional[dict[str, Any]] = None


class GateApproval(BaseModel):
    principal_id: str
    role: str
    status: Literal["approved", "rejected", "pending"] = "approved"
    attestation_ref: Optional[str] = None
    principal_type: Optional[str] = None
    auth_method: Optional[str] = None
    attested_at: Optional[datetime] = None


class GateApprovalAttestationCreate(BaseModel):
    action: str
    decision_id: UUID
    change_event_id: Optional[UUID] = None
    policy_set_id: UUID
    target_ref: str
    target_barrier_group: Optional[str] = None
    receipt_hash: Optional[str] = None
    status: Literal["approved", "rejected"] = "approved"
    statement: Optional[str] = Field(default=None, repr=False)
    evidence_refs: list[str] = Field(default_factory=list)
    expires_at: Optional[datetime] = None


class GateApprovalAttestationSupersede(BaseModel):
    status: Literal["approved", "rejected", "revoked"]
    statement: Optional[str] = Field(default=None, repr=False)
    evidence_refs: list[str] = Field(default_factory=list)
    expires_at: Optional[datetime] = None


class GateApprovalAttestation(BaseModel):
    id: UUID
    namespace: str
    barrier_group: Optional[str]
    series_key: str
    sequence: int
    approval_principal_id: str
    attested_by: str
    principal_type: Optional[str]
    attester_role: str
    auth_method: str
    credential_id: Optional[str]
    status: Literal["approved", "rejected", "revoked"]
    action: str
    decision_id: Optional[UUID]
    change_event_id: Optional[UUID]
    policy_set_id: UUID
    policy_hash: str
    target_ref: Optional[str]
    target_barrier_group: Optional[str]
    receipt_hash: Optional[str]
    context_hash: str
    statement: Optional[str] = Field(repr=False)
    statement_hash: Optional[str]
    evidence_refs: list[str]
    expires_at: Optional[datetime]
    supersedes_id: Optional[UUID]
    prior_attestation_hash: Optional[str]
    attestation_hash: str
    attested_at: datetime


class UntrustedContentSignal(BaseModel):
    signal_type: str
    source: Optional[str] = None
    score: int
    trusted: bool = False
    details: dict[str, Any] = Field(default_factory=dict)


class GateEvaluationRequest(BaseModel):
    action: str
    target_ref: str
    decision_id: UUID
    enforcement_principal_id: str
    permit_ttl_seconds: int = Field(ge=1, le=300)
    execution_request_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    principal_id: Optional[str] = None
    # Advanced assertions; ordinary callers should let authenticated identity
    # supply scopes and barriers.
    principal_scopes: list[str] = Field(default_factory=list)
    principal_barrier_group: Optional[str] = None
    target_barrier_group: Optional[str] = None
    decision_type: Optional[str] = None
    risk_level: RiskLevel = "medium"
    change_event_id: Optional[UUID] = None
    policy_set_id: Optional[UUID] = None
    policy_name: Optional[str] = None
    policy_version: Optional[str] = None
    receipt: GateReceiptContext = Field(default_factory=GateReceiptContext)
    sources_current: Optional[bool] = None
    attached_policy_version: Optional[str] = None
    approval_ids: list[UUID] = Field(default_factory=list)
    untrusted_content_signals: list[UntrustedContentSignal] = Field(default_factory=list)
    context: dict[str, Any] = Field(default_factory=dict)


class FlexibleControlResult(BaseModel):
    """Typed identifiers plus forward-compatible server-managed fields."""

    model_config = ConfigDict(extra="allow")

    id: UUID
    namespace: str


class GatePolicySet(FlexibleControlResult):
    barrier_group: Optional[str]
    name: str
    version: str
    description: Optional[str]
    status: Literal["draft", "active", "retired"]
    default_disposition: GateDisposition
    protected_actions: list[str]
    target_ref_prefixes: list[str]
    enforcement_principal_ids: list[str]
    maximum_permit_ttl_seconds: int
    created_by: str
    created_at: datetime
    activated_by: Optional[str]
    activated_at: Optional[datetime]
    retired_at: Optional[datetime]
    policy_hash: str
    metadata: dict[str, Any]
    rules: list[dict[str, Any]] = Field(default_factory=list)


class GateDecision(FlexibleControlResult):
    barrier_group: Optional[str]
    policy_set_id: UUID
    policy_name: str
    policy_version: str
    policy_hash: str
    principal_id: str
    action: str
    target_ref: str
    enforcement_principal_id: Optional[str]
    execution_request_hash: Optional[str]
    decision_id: Optional[UUID]
    change_event_id: Optional[UUID]
    receipt_hash: Optional[str]
    disposition: GateDisposition
    reasons: list[dict[str, Any]]
    applied_rules: list[dict[str, Any]]
    input_snapshot: dict[str, Any]
    request_hash: str
    evaluation_hash: str
    evaluated_at: datetime


class GateExecutionPermitIssued(BaseModel):
    permit_id: UUID
    evaluation_id: UUID
    enforcement_principal_id: str
    action: str
    target_ref: str
    decision_id: UUID
    execution_request_hash: str
    issued_at: datetime
    expires_at: datetime
    token: str = Field(
        min_length=59,
        max_length=59,
        pattern=r"^lians_permit_v1_[A-Za-z0-9_-]{43}$",
        repr=False,
        json_schema_extra={"readOnly": True, "x-sensitive": True},
    )


class GateEvaluationResult(GateDecision):
    execution_permit: Optional[GateExecutionPermitIssued] = None


class GateExecutionPermitConsume(BaseModel):
    permit_id: UUID
    token: str = Field(repr=False, json_schema_extra={"writeOnly": True})
    action: str
    target_ref: str
    decision_id: UUID
    execution_request_hash: str = Field(pattern=r"^[0-9a-f]{64}$")


class GateExecutionPermitConsumption(BaseModel):
    id: UUID
    namespace: str
    barrier_group: Optional[str]
    permit_id: UUID
    evaluation_id: UUID
    policy_set_id: UUID
    decision_id: UUID
    consuming_principal_id: str
    action: str
    target_ref: str
    execution_request_hash: str
    grant_hash: str
    consumed_at: datetime
    consumption_hash: str


class InvestigationCaseCreate(BaseModel):
    actor_id: Optional[str] = None
    title: str
    description: Optional[str] = None
    severity: RiskLevel = "medium"
    owner_principal: Optional[str] = None
    barrier_group: Optional[str] = None
    decision_id: Optional[UUID] = None
    change_event_id: Optional[UUID] = None
    gate_decision_id: Optional[UUID] = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class InvestigationCaseUpdate(BaseModel):
    expected_updated_at: datetime
    actor_id: Optional[str] = None
    owner_principal: Optional[str] = None
    status: Optional[Literal["open", "in_review", "remediating", "resolved"]] = None
    severity: Optional[RiskLevel] = None
    resolution_summary: Optional[str] = None


class InvestigationCase(FlexibleControlResult):
    barrier_group: Optional[str]
    title: str
    description: Optional[str]
    severity: RiskLevel
    status: str
    owner_principal: Optional[str]
    decision_id: Optional[UUID]
    change_event_id: Optional[UUID]
    gate_decision_id: Optional[UUID]
    opened_by: str
    opened_at: datetime
    updated_at: datetime
    closed_at: Optional[datetime]
    resolution_summary: Optional[str]
    metadata: dict[str, Any]


class RemediationTaskCreate(BaseModel):
    expected_case_updated_at: datetime
    actor_id: Optional[str] = None
    title: str
    description: Optional[str] = None
    owner_principal: Optional[str] = None
    due_at: Optional[datetime] = None
    decision_id: Optional[UUID] = None
    change_event_id: Optional[UUID] = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class RemediationTaskUpdate(BaseModel):
    expected_updated_at: datetime
    actor_id: Optional[str] = None
    owner_principal: Optional[str] = None
    status: Optional[Literal["pending", "in_progress", "blocked", "cancelled"]] = None
    due_at: Optional[datetime] = None


class RemediationTask(FlexibleControlResult):
    barrier_group: Optional[str]
    case_id: UUID
    title: str
    description: Optional[str]
    status: str
    owner_principal: Optional[str]
    due_at: Optional[datetime]
    decision_id: Optional[UUID]
    change_event_id: Optional[UUID]
    created_by: str
    created_at: datetime
    updated_at: datetime
    closed_at: Optional[datetime]
    metadata: dict[str, Any]


class ClosureAttestationCreate(BaseModel):
    expected_updated_at: datetime
    actor_id: Optional[str] = None
    statement: str = Field(repr=False)
    evidence_refs: list[str]
    resolution_summary: Optional[str] = None


class ClosureAttestation(FlexibleControlResult):
    barrier_group: Optional[str]
    resource_type: Literal["case", "task"]
    resource_id: UUID
    attested_by: str
    statement: Optional[str] = Field(repr=False)
    statement_hash: str
    hash_version: Literal[1, 2]
    evidence_refs: list[str]
    decision_id: Optional[UUID]
    change_event_id: Optional[UUID]
    attestation_hash: str
    attested_at: datetime


class AttestedClosure(BaseModel):
    resource_type: Literal["case", "task"]
    resource_id: UUID
    status: Literal["closed"]
    attestation: ClosureAttestation


class Principal(BaseModel):
    namespace: str
    scopes: list[str]
    barrier_group: Optional[str]
    principal_id: Optional[str]
    principal_type: Optional[str]
    auth_method: str
    credential_id: Optional[str]


class WorkloadCredentialCreate(BaseModel):
    label: Optional[str] = None
    role: Optional[Literal["owner", "analyst", "compliance", "readonly"]] = None
    scopes: list[str] = Field(default_factory=list)
    barrier_group: Optional[str] = None
    ttl_seconds: int = Field(ge=60, le=31_536_000)


class WorkloadCredentialRotate(BaseModel):
    expected_version: int = Field(ge=1)
    ttl_seconds: int = Field(ge=60, le=31_536_000)


class WorkloadCredential(BaseModel):
    id: UUID
    namespace: str
    label: Optional[str]
    scopes: list[str]
    effective_scopes: list[str]
    role: Optional[Literal["owner", "analyst", "compliance", "readonly"]]
    barrier_group: Optional[str]
    provisioning_source: Literal["tenant_oidc"]
    created_by: str
    created_at: datetime
    expires_at: datetime
    last_used_at: Optional[datetime]
    rotated_from_id: Optional[UUID]
    rotated_at: Optional[datetime]
    revoked_at: Optional[datetime]
    version: int
    status: Literal["active", "expired", "revoked", "rotated"]


class WorkloadCredentialCreated(WorkloadCredential):
    secret: str = Field(
        repr=False,
        json_schema_extra={"readOnly": True, "x-sensitive": True},
    )


# ── Investigator flagship read model ────────────────────────────────────────


class InvestigatorQueueItem(BaseModel):
    decision: dict[str, Any]
    priority_score: int
    priority_level: Literal["low", "medium", "high", "critical"]
    posture: Literal["defensible", "needs_attention", "blocked"]
    signals: list[str]
    latest_gate_disposition: Optional[str]
    open_case_count: int
    maximum_evidence_risk_score: Optional[int]
    review_status: str
    normalized_evidence_complete: bool


class InvestigatorQueue(BaseModel):
    generated_at: datetime
    items: list[InvestigatorQueueItem]
    candidates_scanned: int
    scan_limit: int
    scan_truncated: bool
    total_is_lower_bound: bool


class InvestigatorRiskSummary(BaseModel):
    posture: Literal["defensible", "needs_attention", "blocked"]
    priority_score: int
    priority_level: Literal["low", "medium", "high", "critical"]
    receipt_grade: str
    receipt_score: int
    receipt_missing: list[str]
    maximum_evidence_risk_score: Optional[int]
    latest_gate_disposition: Optional[str]
    gate_disposition_counts: dict[str, int]
    open_case_count: int
    overdue_task_count: int
    blockers: list[str]
    attention_signals: list[str]
    recommended_actions: list[str]


class InvestigatorCollectionWindow(BaseModel):
    limit: int
    returned: int
    total: int
    total_is_lower_bound: bool
    truncated: bool
    complete: bool
    ordering: str
    scope: str


class InvestigatorReportCoverage(BaseModel):
    complete: bool
    audit_scope_complete: bool
    receipt_evidence_scope_complete: bool
    evidence_links: InvestigatorCollectionWindow
    evidence_artifacts: InvestigatorCollectionWindow
    timeline: InvestigatorCollectionWindow
    gate_evaluations: InvestigatorCollectionWindow
    approval_attestations: InvestigatorCollectionWindow
    review_history: InvestigatorCollectionWindow
    cases: InvestigatorCollectionWindow
    remediation_tasks: InvestigatorCollectionWindow
    closure_attestations: InvestigatorCollectionWindow


class InvestigatorIntegrity(BaseModel):
    audit_chain: dict[str, Any]
    review_chain_status: Literal["ok", "missing", "tampered", "partial"]
    review_chain_violations: list[dict[str, Any]] = Field(default_factory=list)
    approval_attestations_status: Literal["valid", "missing", "invalid", "partial"]
    approval_attestations_valid: Optional[bool]
    invalid_approval_attestation_ids: list[UUID] = Field(default_factory=list)


class DecisionInvestigationReport(BaseModel):
    report_version: Literal["1.1"]
    generated_at: datetime
    decision: dict[str, Any]
    risk: InvestigatorRiskSummary
    receipt_completeness: dict[str, Any]
    coverage: InvestigatorReportCoverage
    evidence_graph: dict[str, Any]
    timeline: list[dict[str, Any]]
    gate_evaluations: list[GateDecision]
    approval_attestations: list[GateApprovalAttestation]
    review_history: list[dict[str, Any]]
    cases: list[dict[str, Any]]
    integrity: InvestigatorIntegrity
    links: dict[str, str]
    disclosures: list[str]


class LiansDiscovery(BaseModel):
    name: Literal["Lians"]
    category: Literal["decision_evidence_infrastructure"]
    api_version: str
    decision_receipt_version: str
    universal_recorder_version: str
    protocols: list[str]
    authentication: list[str]
    links: dict[str, str]


class PlatformCapabilities(BaseModel):
    generated_at: datetime
    namespace: str
    principal_type: str
    authentication_method: str
    information_barrier_scoped: bool
    components: dict[str, dict[str, Any]]
    standards: dict[str, dict[str, Any]]
    privacy: dict[str, Any]
    links: dict[str, str]


class ReadinessCheck(BaseModel):
    id: str
    status: Literal["pass", "warning", "fail", "not_configured"]
    message: str
    required_for: list[str]


class PlatformReadiness(BaseModel):
    generated_at: datetime
    namespace: str
    status: Literal["ready", "degraded", "configuration_required"]
    production_baseline_ready: bool
    control_plane_ready: bool
    enterprise_identity_ready: bool
    checks: list[ReadinessCheck]
    inventory: dict[str, int]
    disclosures: list[str]
