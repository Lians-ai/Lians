from __future__ import annotations
from datetime import datetime
from typing import Any, Optional
from uuid import UUID
from pydantic import BaseModel, Field


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
    context_before_id: Optional[UUID] = None
    context_after_id: Optional[UUID] = None
    context_before_metadata: Optional[dict[str, Any]] = None
    context_after_metadata: Optional[dict[str, Any]] = None
    context_before_2: Optional[str] = None
    context_after_2: Optional[str] = None
    context_before_2_id: Optional[UUID] = None
    context_after_2_id: Optional[UUID] = None
    context_before_2_metadata: Optional[dict[str, Any]] = None
    context_after_2_metadata: Optional[dict[str, Any]] = None

    model_config = {"from_attributes": True}


class RecallRequest(BaseModel):
    agent_id: str = Field(min_length=1, max_length=255)
    query: str = Field(min_length=1, max_length=20_000)
    k: int = Field(default=5, ge=1, le=200)
    as_of: Optional[datetime] = None
    filters: dict[str, Any] = Field(default_factory=dict, max_length=100)
    # Attach each hit's temporally-adjacent neighbors (context_before/_after).
    # Measured on LongMemEval: answer-session retrieval coverage was ~100%
    # while judged QA sat at 89% — the failures were an answerer misreading
    # isolated fragments whose meaning lived in the adjacent turn.
    include_context: bool = False
    # ``adaptive`` keeps simple questions on the standard fast path and plans
    # a few deterministic retrieval facets for temporal, relational, and
    # aggregation questions. No LLM or benchmark labels are involved.
    strategy: str = Field(default="standard", pattern="^(standard|adaptive)$")
    max_query_variants: int = Field(default=4, ge=1, le=4)
    # fast: bounded single-query path; deep: adaptive multi-facet recall;
    # reconstruct: exhaustive point-in-time/evidence-oriented recall.
    mode: str = Field(default="fast", pattern="^(fast|deep|reconstruct)$")
    # When present, the recall receipt and returned memory versions are bound
    # to this decision envelope before the response is returned.
    decision_envelope_id: Optional[UUID] = None


class RecallResult(BaseModel):
    memories: list[MemoryOut]
    as_of: Optional[datetime]
    total_candidates: int
    # True when the embedding provider was unavailable and recall proceeded
    # lexical-only (BM25 + recency + importance). The same flag is written to
    # the audit chain, so a decision made under degraded recall is
    # reconstructable as such.
    retrieval_degraded: bool = False
    # Rough size of the returned memory contents (~4 chars/token) so callers
    # can budget the prompt cost of injecting this recall into an LLM call.
    token_estimate: int = 0
    strategy: str = "standard"
    query_variants: list[str] = Field(default_factory=list)
    retrieval_confidence: float = 0.0
    latency_ms: float = 0.0
    mode: str = "fast"
    latency_budget_ms: float = 100.0
    deadline_exceeded: bool = False
    # Content address over query policy + result IDs/hashes. This is not a
    # signature; it lets downstream evidence packs detect result mutation.
    receipt_sha256: str = ""
    # Canonical receipt payload. Hash this object with sorted compact JSON to
    # independently reproduce receipt_sha256 without exposing the raw query.
    receipt: dict[str, Any] = Field(default_factory=dict)
    provenance_coverage: float = 0.0


class MemoryFeedbackCreate(BaseModel):
    agent_id: str
    signal: str = Field(pattern="^(helpful|incorrect|outdated|duplicate|ignored)$")
    weight: float = Field(default=1.0, ge=0.0, le=1.0)
    outcome: Optional[str] = None
    query: Optional[str] = None
    source: Optional[str] = None
    note: Optional[str] = Field(default=None, max_length=2000)


class MemoryFeedbackOut(BaseModel):
    id: UUID
    memory_id: UUID
    agent_id: str
    signal: str
    weight: float
    outcome: Optional[str] = None
    policy_action: str
    memory_importance: float
    created_at: datetime


class MemoryLearningSummary(BaseModel):
    agent_id: Optional[str] = None
    total_feedback: int
    helpful: int
    incorrect: int
    outdated: int
    duplicate: int
    ignored: int
    helpful_rate: float
    memories_pending_review: int


class MemoryReviewResolve(BaseModel):
    agent_id: str
    action: str = Field(pattern="^(keep|retire|replace)$")
    reviewer: str = Field(min_length=1, max_length=200)
    note: Optional[str] = Field(default=None, max_length=2000)
    correction: Optional[str] = Field(default=None, min_length=1, max_length=100000)


class MemoryReviewResult(BaseModel):
    memory_id: UUID
    agent_id: str
    action: str
    status: str
    reviewer: str
    resolved_at: datetime
    replacement_memory_id: Optional[UUID] = None


class MemoryMaintenanceResult(BaseModel):
    namespace: str
    memories_scanned: int
    memories_demoted: int
    consolidation_candidates: int
    dry_run: bool
    candidate_memory_ids: list[UUID] = Field(default_factory=list)


class ExperienceCreate(BaseModel):
    agent_id: str = Field(min_length=1, max_length=255)
    task: str = Field(min_length=1, max_length=1000)
    decision: dict[str, Any] = Field(min_length=1)
    context_memory_ids: list[UUID] = Field(min_length=1, max_length=100)
    metadata: dict[str, Any] = Field(default_factory=dict, max_length=100)


class ExperienceOutcome(BaseModel):
    outcome: dict[str, Any] = Field(min_length=1)
    reward: float = Field(ge=-1.0, le=1.0)
    reviewer_feedback: Optional[str] = Field(default=None, max_length=4000)


class ExperienceOut(BaseModel):
    id: UUID
    namespace: str
    agent_id: str
    task: str
    task_key: str
    decision: dict[str, Any]
    context_memory_ids: list[UUID]
    metadata: dict[str, Any] = Field(validation_alias="metadata_")
    outcome: Optional[dict[str, Any]]
    reward: Optional[float]
    reviewer_feedback: Optional[str]
    status: str
    created_at: datetime
    completed_at: Optional[datetime]

    model_config = {"from_attributes": True}


class ExperienceListResult(BaseModel):
    experiences: list[ExperienceOut]
    total: int


class ReflectionGenerateRequest(BaseModel):
    agent_id: str = Field(min_length=1, max_length=255)
    minimum_support: int = Field(default=2, ge=2, le=20)
    minimum_reward: float = Field(default=0.6, ge=0.0, le=1.0)


class ReflectionReviewRequest(BaseModel):
    action: str = Field(pattern="^(approve|reject)$")
    reviewer: str = Field(min_length=1, max_length=200)
    note: Optional[str] = Field(default=None, max_length=2000)


class ReflectionProposalOut(BaseModel):
    id: UUID
    namespace: str
    agent_id: str
    task_key: str
    content: str
    supporting_experience_ids: list[UUID]
    confidence: float
    status: str
    reviewer_note: Optional[str]
    promoted_memory_id: Optional[UUID]
    created_at: datetime
    reviewed_at: Optional[datetime]

    model_config = {"from_attributes": True}


class ReflectionListResult(BaseModel):
    proposals: list[ReflectionProposalOut]
    total: int


class AuditReconstructRequest(BaseModel):
    agent_id: str
    as_of: datetime
    query: Optional[str] = None


class AuditReconstructResult(BaseModel):
    memories: list[MemoryOut]
    event_trail: list[dict[str, Any]]
    as_of: datetime


class DecisionCreate(BaseModel):
    agent_id: str
    decision_type: str = Field(min_length=1, max_length=100)
    outcome: str = Field(min_length=1, max_length=500)
    reason_codes: list[str] = Field(default_factory=list, max_length=100)
    regime: Optional[str] = Field(None, max_length=100)
    subject_id: Optional[str] = None
    session_id: Optional[str] = None
    trace_id: Optional[str] = Field(None, pattern=r"^[0-9a-fA-F]{32}$")
    run_id: Optional[str] = Field(None, max_length=512)
    model_id: Optional[str] = None
    model_version: Optional[str] = None
    model_artifact_hash: Optional[str] = Field(None, pattern=r"^[0-9a-fA-F]{64}$")
    policy_id: Optional[str] = Field(None, max_length=512)
    policy_version: Optional[str] = None
    policy_artifact_hash: Optional[str] = Field(None, pattern=r"^[0-9a-fA-F]{64}$")
    prompt_id: Optional[str] = Field(None, max_length=512)
    prompt_version: Optional[str] = Field(None, max_length=255)
    prompt_artifact_hash: Optional[str] = Field(None, pattern=r"^[0-9a-fA-F]{64}$")
    runtime_version: Optional[str] = Field(None, max_length=255)
    decided_at: datetime
    knowledge_as_of: Optional[datetime] = None
    evidence_memory_ids: list[UUID] = Field(default_factory=list, max_length=1000)
    input_hash: Optional[str] = Field(None, pattern=r"^[0-9a-fA-F]{64}$")
    output_hash: Optional[str] = Field(None, pattern=r"^[0-9a-fA-F]{64}$")
    replay_manifest_hash: Optional[str] = Field(None, pattern=r"^[0-9a-fA-F]{64}$")
    completeness_profile: str = Field(
        default="standard",
        pattern=r"^(standard|regulated_recordkeeping|human_review)$",
    )
    required_checks: dict[str, list[str]] = Field(default_factory=dict)
    supersedes_id: Optional[UUID] = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class DecisionOut(BaseModel):
    id: UUID
    envelope_id: Optional[UUID] = None
    namespace: str
    agent_id: str
    decision_type: str
    outcome: str
    reason_codes: list[str]
    regime: Optional[str]
    subject_id: Optional[str]
    session_id: Optional[str]
    trace_id: Optional[str] = None
    run_id: Optional[str] = None
    model_id: Optional[str]
    model_version: Optional[str]
    policy_version: Optional[str]
    prompt_id: Optional[str] = None
    prompt_version: Optional[str] = None
    runtime_version: Optional[str] = None
    decided_at: datetime
    recorded_at: datetime
    knowledge_as_of: datetime
    evidence_memory_ids: list[UUID]
    input_hash: Optional[str]
    output_hash: Optional[str]
    replay_manifest_hash: Optional[str] = None
    human_review_status: str
    human_reviewer: Optional[str]
    human_reviewed_at: Optional[datetime]
    supersedes_id: Optional[UUID]
    metadata: dict[str, Any]
    record_hash: str


class CompletenessGap(BaseModel):
    code: str
    label: str
    blocks: str
    message: str


class DecisionCompleteness(BaseModel):
    grade: Optional[str]
    next_grade: Optional[str]
    score: float
    profile: str
    checks: dict[str, bool]
    gaps: list[CompletenessGap]
    evaluated_at: datetime


class DecisionEvidenceCreate(BaseModel):
    evidence_type: str = Field(
        pattern=(
            r"^(memory|recall_receipt|otel_trace|otel_span|policy_decision|"
            r"prompt|model|tool_call|tool_result|human_review|input|output|"
            r"outcome|external)$"
        )
    )
    role: str = Field(
        default="used",
        pattern=r"^(available|retrieved|used|governed|executed|reviewed|produced|outcome)$",
    )
    source_id: str = Field(min_length=1, max_length=512)
    source_version: Optional[str] = Field(None, max_length=255)
    artifact_hash: Optional[str] = Field(None, pattern=r"^[0-9a-fA-F]{64}$")
    occurred_at: Optional[datetime] = None
    metadata: dict[str, Any] = Field(default_factory=dict, max_length=100)


class DecisionEvidenceBatch(BaseModel):
    evidence: list[DecisionEvidenceCreate] = Field(min_length=1, max_length=1000)


class DecisionEvidenceOut(BaseModel):
    id: UUID
    namespace: str
    envelope_id: UUID
    evidence_type: str
    role: str
    source_id: str
    source_version: Optional[str]
    artifact_hash: Optional[str]
    occurred_at: Optional[datetime]
    metadata: dict[str, Any]
    created_at: datetime


class DecisionEnvelopeOpen(BaseModel):
    agent_id: str = Field(min_length=1, max_length=255)
    decision_type: str = Field(min_length=1, max_length=100)
    regime: Optional[str] = Field(None, max_length=100)
    subject_id: Optional[str] = Field(None, max_length=512)
    session_id: Optional[str] = Field(None, max_length=512)
    trace_id: Optional[str] = Field(None, pattern=r"^[0-9a-fA-F]{32}$")
    run_id: Optional[str] = Field(None, max_length=512)
    knowledge_as_of: Optional[datetime] = None
    completeness_profile: str = Field(
        default="standard",
        pattern=r"^(standard|regulated_recordkeeping|human_review)$",
    )
    required_checks: dict[str, list[str]] = Field(default_factory=dict)
    metadata: dict[str, Any] = Field(default_factory=dict, max_length=100)


class DecisionEnvelopeSeal(BaseModel):
    outcome: str = Field(min_length=1, max_length=500)
    reason_codes: list[str] = Field(default_factory=list, max_length=100)
    decided_at: datetime
    knowledge_as_of: Optional[datetime] = None
    model_id: Optional[str] = Field(None, max_length=512)
    model_version: Optional[str] = Field(None, max_length=255)
    model_artifact_hash: Optional[str] = Field(None, pattern=r"^[0-9a-fA-F]{64}$")
    policy_id: Optional[str] = Field(None, max_length=512)
    policy_version: Optional[str] = Field(None, max_length=255)
    policy_artifact_hash: Optional[str] = Field(None, pattern=r"^[0-9a-fA-F]{64}$")
    prompt_id: Optional[str] = Field(None, max_length=512)
    prompt_version: Optional[str] = Field(None, max_length=255)
    prompt_artifact_hash: Optional[str] = Field(None, pattern=r"^[0-9a-fA-F]{64}$")
    runtime_version: Optional[str] = Field(None, max_length=255)
    evidence_memory_ids: list[UUID] = Field(default_factory=list, max_length=1000)
    input_hash: Optional[str] = Field(None, pattern=r"^[0-9a-fA-F]{64}$")
    output_hash: Optional[str] = Field(None, pattern=r"^[0-9a-fA-F]{64}$")
    replay_manifest_hash: Optional[str] = Field(None, pattern=r"^[0-9a-fA-F]{64}$")
    supersedes_id: Optional[UUID] = None
    metadata: dict[str, Any] = Field(default_factory=dict, max_length=100)


class DecisionEnvelopeOut(BaseModel):
    id: UUID
    namespace: str
    agent_id: str
    decision_type: str
    regime: Optional[str]
    subject_id: Optional[str]
    session_id: Optional[str]
    trace_id: Optional[str]
    run_id: Optional[str]
    knowledge_as_of: Optional[datetime]
    completeness_profile: str
    required_checks: dict[str, list[str]]
    metadata: dict[str, Any]
    status: str
    version: int
    decision_id: Optional[UUID]
    created_at: datetime
    sealed_at: Optional[datetime]
    completeness: DecisionCompleteness


class DecisionDetailOut(BaseModel):
    decision: DecisionOut
    completeness: DecisionCompleteness
    evidence: list[DecisionEvidenceOut]


class BlastRadiusDecision(BaseModel):
    decision: DecisionOut
    matching_roles: list[str]
    matching_link_ids: list[UUID]
    completeness: DecisionCompleteness


class BlastRadiusResult(BaseModel):
    schema_: str = Field(
        default="https://lians.ai/schemas/blast-radius/v1",
        alias="schema",
    )
    generated_at: datetime
    evidence_type: str
    source_id: str
    source_version: Optional[str]
    artifact_hash: Optional[str]
    impacted_decisions: int
    impacted_open_envelopes: int
    matching_links: int
    decisions: list[BlastRadiusDecision]


class EvidenceChangeCreate(BaseModel):
    evidence_type: str = Field(min_length=1, max_length=100)
    source_id: str = Field(min_length=1, max_length=512)
    source_version: Optional[str] = Field(None, max_length=255)
    artifact_hash: Optional[str] = Field(None, pattern=r"^[0-9a-fA-F]{64}$")
    new_source_version: Optional[str] = Field(None, max_length=255)
    new_artifact_hash: Optional[str] = Field(None, pattern=r"^[0-9a-fA-F]{64}$")
    change_kind: str = Field(
        pattern=r"^(revised|retracted|compromised|expired|policy_changed|model_changed)$"
    )
    severity: str = Field(default="medium", pattern=r"^(info|low|medium|high|critical)$")
    changed_at: datetime
    actor_id: str = Field(default="evidence-monitor", min_length=1, max_length=255)
    reason: Optional[str] = Field(None, max_length=4000)
    metadata: dict[str, Any] = Field(default_factory=dict, max_length=100)


class DecisionReview(BaseModel):
    status: str = Field(pattern=r"^(requested|affirmed|overturned|withdrawn)$")
    reviewer: str = Field(min_length=1)
    note: Optional[str] = None


class LedgerEventCreate(BaseModel):
    event_type: str = Field(
        pattern=(
            r"^(inference|human_oversight|system_change|data_subject|incident|memory|"
            r"source_change|policy_decision|tool_call|tool_result)$"
        )
    )
    agent_id: str
    occurred_at: datetime
    subject_id: Optional[str] = None
    session_id: Optional[str] = None
    decision_id: Optional[UUID] = None
    decision_envelope_id: Optional[UUID] = None
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


class DecisionReconstructionOut(BaseModel):
    schema_: str = Field(alias="schema")
    generated_at: datetime
    decision: DecisionOut
    envelope: DecisionEnvelopeOut
    completeness: DecisionCompleteness
    evidence: list[DecisionEvidenceOut]
    knowledge_snapshot: list[MemoryOut]
    ledger_events: list[LedgerEventOut]
    trace_spans: list[dict[str, Any]]
    timeline: list[dict[str, Any]]


class EvidenceChangeResult(BaseModel):
    change_event: LedgerEventOut
    blast_radius: BlastRadiusResult


class EraseRequest(BaseModel):
    subject_id: str
    request_ref: str


class EraseResult(BaseModel):
    subject_id: str
    memories_erased: int
    request_ref: str


class ApiKeyCreate(BaseModel):
    namespace: str
    scopes: list[str] = Field(default=["read", "write"])
    label: Optional[str] = None


class ApiKeyScopesUpdate(BaseModel):
    scopes: list[str] = Field(min_length=1)


class ApiKeyOut(BaseModel):
    id: UUID
    namespace: str
    label: Optional[str]
    scopes: list[str]
    created_at: datetime
    rotated_at: Optional[datetime]
    revoked_at: Optional[datetime]

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
    action: str  # "confirm" | "reject"
    reviewer_note: Optional[str] = None


class SupersessionActionResult(BaseModel):
    memory_id: UUID
    action: str
    applied_at: datetime


class BarrierGroupAssign(BaseModel):
    agent_id: str
    group_name: str


class BarrierGroupOut(BaseModel):
    agent_id: str
    namespace: str
    group_name: str
    created_at: datetime

    model_config = {"from_attributes": True}


class MemoryBatchAdd(BaseModel):
    memories: list[MemoryAdd] = Field(max_length=100)


class MemoryBatchResult(BaseModel):
    added: int
    memories: list[MemoryOut]


class ConversationMessage(BaseModel):
    role: str = Field(pattern="^(user|assistant|system|tool)$")
    content: str = Field(min_length=1, max_length=100_000)
    event_time: Optional[datetime] = None
    metadata: dict[str, Any] = Field(default_factory=dict, max_length=100)


class MessageIngestRequest(BaseModel):
    agent_id: str = Field(min_length=1, max_length=255)
    messages: list[ConversationMessage] = Field(min_length=1, max_length=100)
    event_time: Optional[datetime] = None
    source: Optional[str] = Field(default="conversation", max_length=512)
    subject_id: Optional[str] = Field(default=None, max_length=512)
    metadata: dict[str, Any] = Field(default_factory=dict, max_length=100)
    importance: float = Field(default=0.5, ge=0.0, le=1.0)
    roles: list[str] = Field(default=["assistant"], min_length=1, max_length=4)


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
    confidence_threshold: float


class RetentionPolicyIn(BaseModel):
    content_ttl_days: Optional[int] = None   # None = retain forever
    audit_retention_days: int = 1825          # 5 years default (CFTC swap dealer minimum)
    legal_hold: bool = False


class RetentionPolicyOut(BaseModel):
    namespace: str
    content_ttl_days: Optional[int]
    audit_retention_days: int
    legal_hold: bool
    updated_at: datetime

    model_config = {"from_attributes": True}


class RetentionPruneResult(BaseModel):
    namespace: str
    memories_pruned: int
    cutoff_date: datetime


class NamespaceBillingIn(BaseModel):
    stripe_customer_id: Optional[str] = None   # None clears the customer (stops billing)


class NamespaceBillingOut(BaseModel):
    namespace: str
    stripe_customer_id: Optional[str]

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
    status_filter: Optional[str]


class ConflictResolveRequest(BaseModel):
    resolution: str            # accept_a | accept_b | dismiss
    note: Optional[str] = None


class ConflictResolveResult(BaseModel):
    conflict_id: UUID
    resolution: str
    resolved_at: datetime
    memory_invalidated: Optional[UUID]   # the memory whose valid_to was set, if any


class LineageNode(BaseModel):
    """One version of a belief in the provenance chain."""
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
    is_current: bool                    # True for the live tip of the chain


class LineageEdge(BaseModel):
    """A supersession transition between two consecutive belief versions."""
    from_id: UUID                       # older belief being superseded
    to_id: UUID                         # newer belief
    relation: str                       # SUPERSEDES | CONFIRMS | ADDS | CONTRADICTS_SAME_TIME
    confidence: float
    rationale: Optional[str]            # LLM rationale when Stage 3 ran
    adjudication_stage: int             # 1 | 2 | 3
    superseded_at: datetime             # when the supersession was recorded


class MemoryLineageResult(BaseModel):
    """
    Full belief provenance chain for a given memory.

    ``nodes`` are ordered oldest-first (root → tip).
    ``edges[i]`` connects ``nodes[i]`` to ``nodes[i+1]``.
    The queried memory may be anywhere in the chain.
    """
    agent_id: str
    namespace: str
    queried_id: UUID                    # the ID the caller passed in
    root_id: UUID                       # oldest ancestor in the chain
    tip_id: UUID                        # most recent descendant (current belief)
    depth: int                          # number of nodes
    nodes: list[LineageNode]
    edges: list[LineageEdge]


class FactHistoryResult(BaseModel):
    """
    All known versions of a structured fact, ordered oldest-first by event_time.

    Unlike lineage (which requires a memory_id), this query accepts a ticker
    + metric pair and returns every recorded value across all temporal states
    (including superseded ones).  Entity normalization is applied so 'Apple',
    'AAPL', and 'US0378331005' all map to the same series.
    """
    ticker: str                          # canonical ticker (post-normalization)
    metric: str
    agent_id: str
    namespace: str
    total: int
    items: list[MemoryOut]


class KnowledgeSnapshot(BaseModel):
    """
    Complete knowledge state of an agent at a given point in time.

    Unlike recall (which does vector search + ranking), this is exhaustive —
    every memory that was valid as of `as_of` is returned.  Use this for
    audit reconstruction: "show me everything the agent knew on 2025-03-14."

    This is the one-call compliance demo that closes deals with risk committees
    and regulators: SEC examiners can verify the agent's complete knowledge state
    at any past T without hunting through logs.
    """
    agent_id: str
    namespace: str
    as_of: datetime
    total: int
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

    is_clean=True is the proof a quant fund needs before trusting a backtest.
    contamination_rate is flags / memories_checked (0.0 if no memories).
    """
    agent_id: str
    namespace: str
    simulation_as_of: datetime
    memories_checked: int
    flags: list[ContaminationFlagOut]
    contamination_rate: float
    is_clean: bool


class ErasureCertificate(BaseModel):
    """
    Cryptographic proof that a data subject's content was permanently destroyed.

    The certificate proves:
      1. N memories had their encrypted content destroyed on `erased_at`.
      2. The SHA-256 content_hashes are preserved — the erasure is auditable
         but the content is unrecoverable.
      3. The audit chain remains intact after the erasure (chain_status = "ok").
      4. This certificate itself has a unique `certificate_id` for external
         reference (e.g., filing with a supervisory authority).

    Compliance officers buy proofs, not promises.  This is the proof.
    """
    certificate_id: str             # stable UUID derived from subject + erased_at
    subject_id: str
    namespace: str
    request_ref: Optional[str]      # the erasure request reference from the caller
    erased_at: datetime             # when the DEK was destroyed
    memories_erased: int
    content_hashes: list[str]       # SHA-256 of each erased memory's original content
    chain_status: str               # "ok" | "tampered" | "unchecked"
    generated_at: datetime


class AuditChainViolation(BaseModel):
    row_id: str
    kind: str   # "hash_mismatch" | "orphaned_parent"
    detail: str


class AuditChainVerifyResult(BaseModel):
    namespace: str
    rows_checked: int
    status: str          # "ok" | "tampered"
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


class AuditExportResult(BaseModel):
    namespace: str
    from_: Optional[datetime] = None
    to: Optional[datetime] = None
    total_rows: int
    chain_status: Optional[str] = None   # "ok" | "tampered" | None (not verified)
    chain_violations: Optional[list[AuditChainViolation]] = None
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


class PathResult(BaseModel):
    src: str
    dst: str
    connected: bool
    hops: int
    as_of: Optional[str]
    path: list[EdgeOut]


# ── Context assembly ────────────────────────────────────────────────────────────


class ContextRequest(BaseModel):
    """Build a token-budgeted, ready-to-inject context block from recall."""
    agent_id: str = Field(min_length=1, max_length=255)
    query: str = Field(min_length=1, max_length=20_000)
    k: int = Field(default=10, ge=1, le=200)
    as_of: Optional[datetime] = None
    max_tokens: int = Field(default=1500, ge=64, le=32000)
    header: str = Field(
        default="Relevant facts from memory (most recent, non-stale):",
        max_length=1000,
    )
    mmr: bool = False                     # diversity reranking before assembly
    # Active resurfacing: open conflicts push to the top of every context block
    # until adjudicated — an unresolved conflict must not silently age out.
    # Opt out per-call for surfaces where contested facts are handled elsewhere.
    surface_conflicts: bool = True
    max_conflicts: int = Field(default=5, ge=0, le=50)
    strategy: str = Field(default="adaptive", pattern="^(standard|adaptive)$")
    max_query_variants: int = Field(default=4, ge=1, le=4)
    mode: str = Field(default="deep", pattern="^(fast|deep|reconstruct)$")
    decision_envelope_id: Optional[UUID] = None


class ContextResult(BaseModel):
    context: str                          # the assembled block, ready to inject
    context_text: str = ""                # canonical alias for SDK/UI consumers
    memories: list[MemoryOut]             # the facts that fit the budget
    token_estimate: int
    truncated: bool                       # True if the budget cut off some facts
    retrieval_degraded: bool = False      # recall ran lexical-only (see RecallResult)
    strategy: str = "adaptive"
    query_variants: list[str] = Field(default_factory=list)
    retrieval_confidence: float = 0.0
    recall_latency_ms: float = 0.0
    mode: str = "deep"
    receipt_sha256: str = ""
    receipt: dict[str, Any] = Field(default_factory=dict)
    provenance_coverage: float = 0.0
    deadline_exceeded: bool = False
    # Open conflicts surfaced into the block (oldest first) + the total count
    # still open for this agent, so callers can alert when the backlog grows
    # beyond what the block shows.
    open_conflicts: list[ConflictFlagOut] = Field(default_factory=list)
    open_conflicts_total: int = 0
    excluded: list[dict[str, Any]] = Field(default_factory=list)
    budget: dict[str, Any] = Field(default_factory=dict)
    provenance: dict[str, Any] = Field(default_factory=dict)
    learning_applied: bool = False
    ranking_policy: str = "relevance-only-v1"


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
    status_filter: Optional[str]


class AdmissionResolveRequest(BaseModel):
    action: str                       # approve | reject
    note: Optional[str] = None
