from __future__ import annotations

from datetime import datetime
from typing import Any, Literal, Optional
from uuid import UUID

from pydantic import BaseModel, Field, field_validator, model_validator

from .barrier_policy import is_reserved_barrier_group


class MemoryAdd(BaseModel):
    agent_id: str = Field(min_length=1, max_length=255)
    content: str = Field(min_length=1, max_length=100_000)
    event_time: datetime
    source: Optional[str] = Field(None, max_length=512)
    subject_id: Optional[str] = Field(None, max_length=512)
    metadata: dict[str, Any] = Field(default_factory=dict, max_length=100)
    importance: float = Field(default=0.5, ge=0.0, le=1.0)


class MemoryOut(BaseModel):
    id: UUID
    namespace: str
    agent_id: str
    content: Optional[str]        # None if erased
    subject_id: Optional[str]
    event_time: datetime
    ingestion_time: datetime
    valid_from: datetime
    valid_to: Optional[datetime]
    superseded_by: Optional[UUID]
    supersession_confidence: Optional[float]
    barrier_group: Optional[str] = None
    importance: float
    source: Optional[str]
    content_hash: str
    erased_at: Optional[datetime]
    metadata: dict[str, Any]
    # Relevance score (hybrid semantic+lexical fusion) — populated on recall
    # responses only; None on write/snapshot surfaces. Additive for API
    # consumers that rank or threshold on similarity (e.g. the Memory Governor).
    score: Optional[float] = None
    # Adjacent-memory context (recall with include_context=True only): the
    # nearest same-agent memories immediately before/after this one in event
    # time, within the context gap. Event streams split one fact across
    # neighboring entries (a question and its answer); the neighbor routinely
    # holds the half a consumer LLM needs to read the hit correctly.
    context_before: Optional[str] = None
    context_after: Optional[str] = None

    model_config = {"from_attributes": True}


class RecallRequest(BaseModel):
    agent_id: str = Field(min_length=1, max_length=255)
    query: str = Field(min_length=1, max_length=20_000)
    k: int = Field(default=5, ge=1, le=100)
    as_of: Optional[datetime] = None
    filters: dict[str, Any] = Field(default_factory=dict, max_length=100)
    # Attach each hit's temporally-adjacent neighbors (context_before/_after).
    # Measured on LongMemEval: answer-session retrieval coverage was ~100%
    # while judged QA sat at 89% — the failures were an answerer misreading
    # isolated fragments whose meaning lived in the adjacent turn.
    include_context: bool = False


class RecallResult(BaseModel):
    memories: list[MemoryOut]
    as_of: Optional[datetime]
    total_candidates: int
    # True when the embedding provider was unavailable and recall proceeded
    # lexical-only (BM25 + recency + importance). The same flag is written to
    # the audit chain, so a decision made under degraded recall is
    # reconstructable as such.
    retrieval_degraded: bool = False
    # False only when opt-in graph-proximity reranking exhausted a traversal
    # budget; omitted distances are then unknown, not "unreachable".
    graph_search_complete: bool = True
    # Candidate discovery is deliberately bounded. ``False`` means the
    # returned top-k came from a disclosed candidate window rather than an
    # exhaustive agent scan; callers must not interpret absence as proof that
    # no matching fact exists.
    candidate_window_complete: bool = True
    candidates_considered: int = 0
    candidate_limit: int = 0
    candidate_mode: str = "exact"
    # Rough size of the returned memory contents (~4 chars/token) so callers
    # can budget the prompt cost of injecting this recall into an LLM call.
    token_estimate: int = 0


class AuditReconstructRequest(BaseModel):
    agent_id: str
    as_of: datetime
    query: Optional[str] = None


class AuditReconstructResult(BaseModel):
    memories: list[MemoryOut]
    event_trail: list[dict[str, Any]]
    as_of: datetime
    memory_total: int
    memories_returned: int
    memories_complete: bool
    memories_mode: Literal["knowledge_snapshot", "ranked_query"]
    event_total: int
    events_returned: int
    events_complete: bool
    retrieval_degraded: bool = False
    candidate_window_complete: bool = True


class DecisionCreate(BaseModel):
    # Caller-supplied workload label. Authenticated recorder provenance is
    # always derived from the request credential and cannot be asserted here.
    agent_id: str = Field(min_length=1, max_length=255)
    decision_type: str = Field(min_length=1, max_length=100)
    outcome: str = Field(min_length=1, max_length=500)
    reason_codes: list[str] = Field(default_factory=list, max_length=100)
    regime: Optional[str] = Field(None, max_length=100)
    subject_id: Optional[str] = None
    session_id: Optional[str] = None
    model_id: Optional[str] = None
    model_version: Optional[str] = None
    policy_version: Optional[str] = None
    decided_at: datetime
    knowledge_as_of: Optional[datetime] = None
    # System/recording-time cutoff for the evidence boundary. This is distinct
    # from knowledge_as_of (business/event time) and prevents a later-ingested,
    # backdated correction from rewriting an earlier receipt.
    knowledge_recorded_as_of: Optional[datetime] = None
    evidence_memory_ids: list[UUID] = Field(default_factory=list, max_length=1000)
    input_hash: Optional[str] = Field(None, pattern=r"^[0-9a-fA-F]{64}$")
    output_hash: Optional[str] = Field(None, pattern=r"^[0-9a-fA-F]{64}$")
    supersedes_id: Optional[UUID] = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class DecisionOut(BaseModel):
    id: UUID
    namespace: str
    agent_id: str
    recorded_by_principal_ref: str
    recorded_by_auth_method: str
    recorded_by_credential_ref: Optional[str]
    recorded_by_principal_type: Optional[str]
    recorded_by_role: Optional[str]
    recorded_by_scopes: list[str]
    decision_type: str
    outcome: str
    reason_codes: list[str]
    regime: Optional[str]
    subject_id: Optional[str]
    session_id: Optional[str]
    model_id: Optional[str]
    model_version: Optional[str]
    policy_version: Optional[str]
    decided_at: datetime
    recorded_at: datetime
    knowledge_as_of: datetime
    knowledge_recorded_as_of: datetime
    evidence_memory_ids: list[UUID]
    input_hash: Optional[str]
    output_hash: Optional[str]
    human_review_status: str
    human_reviewer: Optional[str]
    human_reviewed_at: Optional[datetime]
    supersedes_id: Optional[UUID]
    metadata: dict[str, Any]
    record_hash_version: int
    record_integrity_status: Literal["verified", "legacy_unverified"]
    record_hash: str


class DecisionReview(BaseModel):
    status: str = Field(pattern=r"^(requested|affirmed|overturned|withdrawn)$")
    # Compatibility-only assertion.  Authenticated identity is authoritative;
    # a mismatching legacy reviewer value is rejected by the route.
    reviewer: Optional[str] = Field(default=None, min_length=1, max_length=512)
    note: Optional[str] = Field(default=None, max_length=50_000)


class DecisionReviewEventOut(BaseModel):
    id: UUID
    namespace: str
    barrier_group: Optional[str]
    decision_id: UUID
    sequence: int
    status: str
    reviewer_principal_id: str
    reviewer_principal_type: Optional[str]
    reviewer_role: Optional[str]
    auth_method: str
    credential_id: Optional[str]
    note: Optional[str]
    note_hash: Optional[str]
    prior_event_hash: Optional[str]
    event_hash: str
    reviewed_at: datetime


class DecisionReviewHistoryResult(BaseModel):
    decision_id: UUID
    total: int = Field(ge=0)
    returned: int = Field(ge=0)
    complete: bool
    has_more: bool
    next_sequence: Optional[int]
    page_chain_verified: bool
    chain_scope_complete: bool
    events: list[DecisionReviewEventOut]


class DecisionReceiptVerifyRequest(BaseModel):
    receipt: dict[str, Any]
    trusted_public_key: Optional[str] = None
    require_signature: bool = False


class DependencyChange(BaseModel):
    dependency_kind: Literal["source", "policy", "model", "tool", "permission"]
    dependency_value: str = Field(min_length=1, max_length=512)
    change_type: Literal[
        "changed", "corrected", "retired", "revoked", "recalled", "corrupted", "erased"
    ] = "changed"
    occurred_at: Optional[datetime] = None
    note: Optional[str] = Field(None, max_length=2000)
    agent_id: str = Field(default="lians-impact-monitor", min_length=1, max_length=255)
    limit: int = Field(default=100, ge=1, le=1000)
    record_event: bool = True


class DecisionImpactItem(BaseModel):
    decision: DecisionOut
    match_basis: list[str]
    impact_status: Literal["direct_reference", "reachable"]
    risk_score: int = Field(ge=0, le=100)
    priority: Literal["critical", "high", "medium", "low"]


class DecisionImpactResult(BaseModel):
    dependency: dict[str, str]
    change_type: str
    assessed_at: datetime
    total: int
    direct_count: int
    reachable_count: int
    search_truncated: bool
    change_event_id: Optional[UUID]
    items: list[DecisionImpactItem]


class LedgerEventCreate(BaseModel):
    event_type: str = Field(pattern=r"^(inference|human_oversight|system_change|data_subject|incident|memory)$")
    agent_id: str
    occurred_at: datetime
    subject_id: Optional[str] = None
    session_id: Optional[str] = None
    decision_id: Optional[UUID] = None
    model_id: Optional[str] = None
    model_version: Optional[str] = None
    payload: dict[str, Any] = Field(default_factory=dict)
    artifact_hash: Optional[str] = Field(None, pattern=r"^[0-9a-fA-F]{64}$")


class LedgerEventOut(BaseModel):
    id: UUID
    namespace: str
    event_type: str
    agent_id: str
    occurred_at: datetime
    recorded_at: datetime
    subject_id: Optional[str]
    session_id: Optional[str]
    decision_id: Optional[UUID]
    model_id: Optional[str]
    model_version: Optional[str]
    payload: dict[str, Any]
    artifact_hash: Optional[str]
    event_hash: str


class EraseRequest(BaseModel):
    subject_id: str = Field(min_length=1, max_length=1024)
    request_ref: str = Field(min_length=1, max_length=512)


class SubjectErasureSnapshotOut(BaseModel):
    memories: int = Field(ge=0)
    live_facts: int = Field(ge=0)
    relationships: int = Field(ge=0)
    pending_admissions: int = Field(ge=0)
    total_rows: int = Field(ge=0)


class SubjectErasureProgressOut(BaseModel):
    memories: int = Field(ge=0)
    live_facts: int = Field(ge=0)
    relationships: int = Field(ge=0)
    pending_admissions: int = Field(ge=0)
    rows_scrubbed: int = Field(ge=0)
    pages_completed: int = Field(ge=0)
    ratio: float = Field(ge=0.0, le=1.0)


class EraseResult(BaseModel):
    """Durable erasure job; DEK destruction is complete before this is returned."""

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
    snapshot: SubjectErasureSnapshotOut
    progress: SubjectErasureProgressOut
    processing_attempts: int = Field(ge=0)
    next_attempt_at: datetime
    last_error_code: Optional[str] = None
    last_error_digest: Optional[str] = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    failure_code: Optional[str] = None
    created_at: datetime
    started_at: Optional[datetime] = None
    updated_at: datetime
    completed_at: Optional[datetime] = None
    replayed: bool = False


class ApiKeyCreate(BaseModel):
    namespace: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,254}$")
    scopes: list[str] = Field(default_factory=list, max_length=50)
    label: Optional[str] = Field(default=None, max_length=255)
    role: Optional[Literal["owner", "analyst", "compliance", "readonly"]] = None
    barrier_group: Optional[str] = Field(
        default=None,
        pattern=r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,254}$",
    )

    @field_validator("barrier_group", mode="before")
    @classmethod
    def normalize_barrier_group(cls, value):
        if value is None:
            return None
        if not isinstance(value, str):
            return value
        normalized = value.strip()
        if is_reserved_barrier_group(normalized):
            raise ValueError("This information-barrier name is reserved")
        return normalized or None

    @field_validator("scopes")
    @classmethod
    def validate_api_key_scopes(cls, values: list[str]) -> list[str]:
        normalized = [value.strip() for value in values]
        if any(
            not value
            or len(value) > 100
            or not all(ch.isalnum() or ch in "_.:-" for ch in value)
            for value in normalized
        ):
            raise ValueError("scopes must contain valid 1-100 character scope names")
        if len(normalized) != len(set(normalized)):
            raise ValueError("scopes must not contain duplicates")
        return normalized

    @model_validator(mode="after")
    def require_api_key_authorization(self):
        # Preserve the historical no-role default without accidentally granting
        # write to a role such as compliance when scopes were omitted.
        if "scopes" not in self.model_fields_set and self.role is None:
            self.scopes = ["read", "write"]
        if self.role is None and not self.scopes:
            raise ValueError("an API key requires a named role, at least one scope, or both")
        return self


class ApiKeyOut(BaseModel):
    id: UUID
    namespace: str
    label: Optional[str]
    scopes: list[str]
    role: Optional[Literal["owner", "analyst", "compliance", "readonly"]] = None
    barrier_group: Optional[str] = None
    created_at: datetime
    rotated_at: Optional[datetime]
    revoked_at: Optional[datetime]
    provisioning_source: Literal["breakglass_admin", "tenant_oidc"] = "breakglass_admin"
    created_by: Optional[str] = None
    expires_at: Optional[datetime] = None
    last_used_at: Optional[datetime] = None
    rotated_from_id: Optional[UUID] = None
    version: int = 1

    model_config = {"from_attributes": True}


class ApiKeyCreated(ApiKeyOut):
    key: str  # plaintext raw key — returned ONCE at creation/rotation, never stored


class SupersessionResult(BaseModel):
    relation: str           # SUPERSEDES | REFINES | CONFIRMS | ADDS | CONTRADICTS_SAME_TIME
    confidence: float
    superseded_ids: list[UUID] = Field(default_factory=list)
    conflict_ids: list[UUID] = Field(default_factory=list)  # memories that CONTRADICTS_SAME_TIME
    # Out-of-order ingestion: an already-live fact with a LATER event_time makes
    # the incoming memory historical on arrival — its validity window closes at
    # that fact's event_time instead of staying open alongside it.
    superseded_by_id: Optional[UUID] = None
    rationale: Optional[str] = None


class SupersessionAction(BaseModel):
    action: Literal["confirm", "reject"]
    expected_superseded_by: Optional[UUID] = Field(
        ...,
        description="superseded_by returned by the review item being resolved",
    )
    reviewer_note: Optional[str] = Field(default=None, max_length=10_000)


class SupersessionActionResult(BaseModel):
    memory_id: UUID
    action: str
    applied_at: datetime


class BarrierGroupAssign(BaseModel):
    agent_id: str = Field(min_length=1, max_length=255)
    group_name: str = Field(min_length=1, max_length=255)
    expected_group_name: Optional[str] = Field(
        ...,
        min_length=1,
        max_length=255,
        description=(
            "Current assignment to replace, or null to assert that no assignment exists"
        ),
    )

    @field_validator("group_name")
    @classmethod
    def reject_reserved_group(cls, value: str) -> str:
        if is_reserved_barrier_group(value):
            raise ValueError("This information-barrier name is reserved")
        return value


class BarrierGroupOut(BaseModel):
    agent_id: str
    namespace: str
    group_name: str
    created_at: datetime

    model_config = {"from_attributes": True}


class MemoryBatchAdd(BaseModel):
    memories: list[MemoryAdd] = Field(min_length=1, max_length=100)


class MemoryBatchResult(BaseModel):
    added: int
    memories: list[MemoryOut]


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


class RetentionPolicyIn(BaseModel):
    expected_updated_at: Optional[datetime] = Field(
        ...,
        description=(
            "Persisted updated_at returned by GET, or null to assert that no policy row exists"
        ),
    )
    content_ttl_days: Optional[int] = None   # None = retain forever
    audit_retention_days: int = 1825          # 5 years default (CFTC swap dealer minimum)
    legal_hold: bool = False


class RetentionPolicyOut(BaseModel):
    namespace: str
    content_ttl_days: Optional[int]
    audit_retention_days: int
    legal_hold: bool
    updated_at: Optional[datetime]

    model_config = {"from_attributes": True}


class RetentionPruneResult(BaseModel):
    namespace: str
    memories_pruned: int
    cutoff_date: datetime
    remaining: int = 0
    complete: bool = True
    batch_limit: int = 500


class NamespaceBillingIn(BaseModel):
    expected_updated_at: Optional[datetime] = Field(
        ...,
        description=(
            "Persisted updated_at returned by GET, or null to assert that no policy row exists"
        ),
    )
    stripe_customer_id: Optional[str] = Field(default=None, max_length=255)

    @field_validator("stripe_customer_id")
    @classmethod
    def validate_stripe_customer_id(cls, value: Optional[str]) -> Optional[str]:
        if value is None:
            return None
        normalized = value.strip()
        if not normalized or any(char.isspace() for char in normalized):
            raise ValueError("stripe_customer_id must be non-empty and whitespace-free")
        return normalized


class NamespaceBillingOut(BaseModel):
    namespace: str
    stripe_customer_id: Optional[str]
    updated_at: Optional[datetime]

    model_config = {"from_attributes": True}


class ConflictFlagOut(BaseModel):
    """A detected conflict between two memories that disagree on the same fact."""
    id: UUID
    namespace: str
    agent_id: str
    memory_a_id: UUID          # pre-existing memory
    memory_b_id: UUID          # newly ingested memory that triggered detection
    memory_a_content: Optional[str]    # decrypted — None if erased
    memory_b_content: Optional[str]
    memory_a_source: Optional[str]
    memory_b_source: Optional[str]
    memory_a_event_time: datetime
    memory_b_event_time: datetime
    confidence: float
    detected_at: datetime
    status: str                # open | accept_a | accept_b | dismissed
    resolved_at: Optional[datetime]
    resolver_note: Optional[str]

    model_config = {"from_attributes": True}


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
    note: Optional[str] = Field(default=None, max_length=10_000)


class ConflictResolveResult(BaseModel):
    conflict_id: UUID
    resolution: str
    resolved_at: datetime
    memory_invalidated: Optional[UUID]   # the memory whose valid_to was set, if any


class LineageNode(BaseModel):
    """One version of a belief in the provenance graph."""
    id: UUID
    content: Optional[str]              # None if erased
    content_hash: str
    event_time: datetime
    ingestion_time: datetime
    valid_from: datetime
    valid_to: Optional[datetime]        # None = still live at this position
    source: Optional[str]
    importance: float
    supersession_confidence: Optional[float]
    erased_at: Optional[datetime]
    metadata: dict[str, Any]
    is_current: bool                    # True for a live graph tip


class LineageEdge(BaseModel):
    """A supersession transition in the returned belief-provenance graph."""
    from_id: UUID                       # older belief being superseded
    to_id: UUID                         # newer belief
    relation: str                       # SUPERSEDES | REFINES | CONFIRMS | ADDS | ...
    confidence: float = Field(ge=0.0, le=1.0)
    rationale: Optional[str]            # LLM rationale when Stage 3 ran
    adjudication_stage: int = Field(ge=1, le=3)
    superseded_at: datetime             # when the supersession was recorded
    audit_event_id: Optional[UUID] = None
    audit_chain_position: Optional[int] = Field(default=None, ge=1)
    audit_binding_status: Literal[
        "bound",
        "missing",
        "target_mismatch",
        "malformed",
    ] = "missing"


class MemoryLineageResult(BaseModel):
    """
    Bounded belief-provenance graph for a given memory.

    ``nodes`` are returned in deterministic topological order (roots before tips).
    Supersession may converge, so callers must follow explicit edge IDs rather
    than assuming adjacent nodes are connected. Singular root/tip fields are
    compatibility aliases; their plural forms are authoritative.
    """
    agent_id: str
    namespace: str
    queried_id: UUID                    # the ID the caller passed in
    root_id: UUID                       # canonical compatibility alias
    tip_id: UUID                        # canonical compatibility alias
    root_ids: list[UUID] = Field(default_factory=list)
    tip_ids: list[UUID] = Field(default_factory=list)
    shape: Literal["chain", "dag"] = "chain"
    depth: int                          # compatibility alias for returned nodes
    edge_count: int = Field(default=0, ge=0)
    truncated: bool = False
    has_more: bool = False
    complete: bool = True
    root_complete: bool = True
    tip_complete: bool = True
    reachable_nodes: int = Field(default=0, ge=0)
    reachable_nodes_is_lower_bound: bool = False
    audit_binding_complete: bool = True
    max_nodes: int = 1000
    nodes: list[LineageNode]
    edges: list[LineageEdge]


class FactHistoryResult(BaseModel):
    """
    All known versions of a structured fact, ordered oldest-first by event_time.

    Unlike lineage (which requires a memory_id), this query accepts a ticker
    + metric pair and scans a bounded deterministic window across temporal
    states. ``total_is_lower_bound`` and ``scan_complete`` disclose whether the
    normalized Python-side alias match inspected the entire candidate set.
    """
    ticker: str                          # canonical ticker (post-normalization)
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


class KnowledgeSnapshot(BaseModel):
    """
    Exact-count, keyset-paginated knowledge state at a point in time.

    Unlike recall, this applies no relevance ranking. ``total`` is exact and
    ``complete`` is true only when the response includes the entire snapshot.
    """
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


class MarkdownExportResult(BaseModel):
    """A chain-anchored, human-readable memory statement (see export_markdown.py)."""
    markdown: str                 # full document including the integrity footer
    document_sha256: str          # SHA-256 of the document body (footer excluded)
    audit_event_id: UUID          # export_markdown event anchoring the hash
    audit_row_hash: str           # that event's position in the tamper-evident chain
    namespace: str
    agent_id: str
    as_of: datetime
    generated_at: datetime
    memory_count: int
    snapshot_total: int
    snapshot_complete: bool


class ContaminationFlagOut(BaseModel):
    """Single lookahead-bias flag from a backtest contamination check."""
    memory_id: UUID
    event_time: datetime
    ingestion_time: datetime
    contamination_type: str          # "future_event" | "late_revision"
    delta_days: float                # days beyond simulation_as_of
    content_preview: Optional[str]   # None if content was erased
    source: Optional[str]
    metadata: dict[str, Any]


class ContaminationReportOut(BaseModel):
    """
    Result of a backtest-contamination check.

    ``is_clean=True`` means no recorded, visible Lians memory violates the
    cutoff inside the authenticated namespace/barrier. It does not prove that
    an external backtest used no unrecorded future input.
    contamination_rate is flags_total / memories_checked (0.0 if no memories).
    """
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
    flags: list[ContaminationFlagOut]
    contamination_rate: float
    is_clean: bool


class ErasureMemoryHashOut(BaseModel):
    memory_id: UUID
    content_hash: str = Field(pattern=r"^[0-9a-f]{64}$")


class ErasureCertificate(BaseModel):
    """
    Exact terminal proof plus one bounded memory-hash evidence page.

    The terminal audit row is committed with the final job transition. Full
    chain verification is a separate bounded operator workflow, so this read
    reports ``unchecked`` rather than conflating capacity exhaustion with trust.
    """
    certificate_id: UUID
    job_id: UUID
    namespace: str
    subject_ref: str
    request_ref: str
    key_destroyed_at: datetime
    completed_at: datetime
    memories_erased: int = Field(ge=0)
    live_facts_erased: int = Field(ge=0)
    relationships_erased: int = Field(ge=0)
    pending_admissions_erased: int = Field(ge=0)
    manifest_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    manifest_algorithm: Literal["lians-subject-erasure-memory-manifest-v1"]
    evidence: list[ErasureMemoryHashOut] = Field(max_length=500)
    content_hashes: list[str] = Field(max_length=500)
    hashes_returned: int = Field(ge=0, le=500)
    hashes_total: int = Field(ge=0)
    hashes_complete: bool
    has_more: bool
    next_memory_id: Optional[UUID] = None
    audit_event_id: UUID
    audit_row_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    chain_status: Literal["unchecked"]
    generated_at: datetime


class AuditChainViolation(BaseModel):
    row_id: str
    kind: str   # "hash_mismatch" | "orphaned_parent"
    detail: str


class AuditChainVerifyResult(BaseModel):
    namespace: str
    rows_checked: int
    status: str          # "ok" | "tampered"
    truncated: bool
    chain_tip: Optional[str]
    violations: list[AuditChainViolation]


class AuditExportRow(BaseModel):
    id: str
    namespace: str
    agent_id: str
    op: str
    memory_id: Optional[str]
    content_hash: Optional[str]
    payload: dict[str, Any]
    created_at: datetime
    prev_hash: Optional[str]
    row_hash: Optional[str]
    hash_version: int = 1
    chain_position: int


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
    chain_status: Optional[str] = None   # "ok" | "tampered" | None (not verified)
    chain_violations: Optional[list[AuditChainViolation]] = None
    chain_rows_checked: Optional[int] = None
    chain_truncated: Optional[bool] = None
    chain_tip: Optional[str] = None
    events: list[AuditExportRow]


# ── Relationship graph ──────────────────────────────────────────────────────────


class RelateRequest(BaseModel):
    """Assert a relationship edge: src_entity --rel_type--> dst_entity."""
    agent_id: str = Field(min_length=1, max_length=255)
    src_entity: str = Field(min_length=1, max_length=1000)
    rel_type: str = Field(min_length=1, max_length=200)
    dst_entity: str = Field(min_length=1, max_length=1000)
    event_time: datetime
    exclusive: bool = False              # invalidate other live src--rel_type--> edges
    subject_id: Optional[str] = Field(None, max_length=512)
    source: Optional[str] = Field(None, max_length=512)
    metadata: dict[str, Any] = Field(default_factory=dict, max_length=100)
    normalize: bool = False              # collapse company/ISIN/CUSIP to canonical ticker


class UnrelateRequest(BaseModel):
    agent_id: str = Field(min_length=1, max_length=255)
    src_entity: str = Field(min_length=1, max_length=1000)
    rel_type: str = Field(min_length=1, max_length=200)
    dst_entity: str = Field(min_length=1, max_length=1000)
    event_time: Optional[datetime] = None
    normalize: bool = False


class EdgeOut(BaseModel):
    id: str
    src: str
    rel_type: str
    dst: str
    event_time: Optional[str]
    valid_to: Optional[str]
    source: Optional[str]
    metadata: dict[str, Any] = Field(default_factory=dict)


class RelateResult(BaseModel):
    id: UUID
    src_entity: str
    rel_type: str
    dst_entity: str
    event_time: datetime
    valid_to: Optional[datetime]


class NeighborOut(BaseModel):
    entity: str
    depth: int


class NeighborsResult(BaseModel):
    entity: str
    depth: int
    as_of: Optional[str]
    neighbors: list[NeighborOut]
    direct_edges: list[EdgeOut]
    search_complete: bool
    truncated: bool
    nodes_examined: int
    edges_examined: int


class PathResult(BaseModel):
    src: str
    dst: str
    # None means the bounded traversal exhausted its node/edge budget before a
    # connection could be proved or disproved.
    connected: Optional[bool]
    hops: int
    as_of: Optional[str]
    path: list[EdgeOut]
    search_complete: bool
    truncated: bool
    nodes_examined: int
    edges_examined: int


# ── Context assembly ────────────────────────────────────────────────────────────


class ContextRequest(BaseModel):
    """Build a token-budgeted, ready-to-inject context block from recall."""
    agent_id: str = Field(min_length=1, max_length=255)
    query: str = Field(min_length=1, max_length=20_000)
    k: int = Field(default=10, ge=1, le=100)
    as_of: Optional[datetime] = None
    filters: dict[str, Any] = Field(default_factory=dict, max_length=100)
    max_tokens: int = Field(default=1500, ge=64, le=32000)
    header: str = Field(
        default="Relevant facts from memory (most recent, non-stale):",
        max_length=1000,
    )
    mmr: bool = False                     # diversity reranking before assembly
    # Active resurfacing: open conflicts push to the top of every context block
    # until adjudicated — an unresolved conflict must not silently age out.
    # Opt out per-call for surfaces where contested facts are handled elsewhere.
    # Historical ``as_of`` contexts always suppress current conflict state.
    surface_conflicts: bool = True
    max_conflicts: int = Field(default=5, ge=0, le=50)


class ContextResult(BaseModel):
    context: str                          # the assembled block, ready to inject
    memories: list[MemoryOut]             # the facts that fit the budget
    token_estimate: int
    truncated: bool                       # True if the budget cut off some facts
    retrieval_degraded: bool = False      # recall ran lexical-only (see RecallResult)
    graph_search_complete: bool = True
    candidate_window_complete: bool = True
    candidates_considered: int = 0
    candidate_limit: int = 0
    # Open conflicts surfaced into the block (oldest first) + the total count
    # still open for this agent, so callers can alert when the backlog grows
    # beyond what the block shows.
    open_conflicts: list[ConflictFlagOut] = Field(default_factory=list)
    open_conflicts_total: int = 0


# ── Graph extraction ────────────────────────────────────────────────────────────


class ExtractRequest(BaseModel):
    """Extract relationship edges from unstructured text and write them."""
    agent_id: str = Field(min_length=1, max_length=255)
    text: str = Field(min_length=1, max_length=100_000)
    event_time: datetime
    normalize: bool = False
    exclusive: bool = False
    use_llm: bool = False                 # opt-in LLM extraction (else rule-based)


class ExtractedTriplet(BaseModel):
    src: str
    rel_type: str
    dst: str


class ExtractResult(BaseModel):
    extracted: list[ExtractedTriplet]
    edges: list[EdgeOut]


# ── Admission control ───────────────────────────────────────────────────────────


class PendingAdmissionOut(BaseModel):
    id: UUID
    namespace: str
    agent_id: str
    content: str
    event_time: datetime
    source: Optional[str]
    subject_id: Optional[str]
    risk_tags: list[str]
    reasons: list[str]
    status: str
    created_at: datetime
    resolved_at: Optional[datetime]
    memory_id: Optional[UUID]

    model_config = {"from_attributes": True}


class AdmissionListResult(BaseModel):
    pending: list[PendingAdmissionOut]
    total: int
    returned: int
    complete: bool
    has_more: bool
    next_created_at: Optional[datetime] = None
    next_id: Optional[UUID] = None
    status_filter: Optional[str]


class AdmissionResolveRequest(BaseModel):
    action: Literal["approve", "admit", "reject"]
    note: Optional[str] = Field(default=None, max_length=10_000)
