from __future__ import annotations
from datetime import datetime
from typing import Any, Optional
from uuid import UUID
from pydantic import BaseModel, Field


class MemoryAdd(BaseModel):
    agent_id: str
    content: str
    event_time: datetime
    source: Optional[str] = None
    subject_id: Optional[str] = None
    metadata: dict[str, Any] = Field(default_factory=dict)
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

    model_config = {"from_attributes": True}


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


class AuditReconstructRequest(BaseModel):
    agent_id: str
    as_of: datetime
    query: Optional[str] = None


class AuditReconstructResult(BaseModel):
    memories: list[MemoryOut]
    event_trail: list[dict[str, Any]]
    as_of: datetime


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
    relation: str           # SUPERSEDES | CONFIRMS | ADDS | CONTRADICTS_SAME_TIME
    confidence: float
    superseded_ids: list[UUID] = Field(default_factory=list)
    conflict_ids: list[UUID] = Field(default_factory=list)  # memories that CONTRADICTS_SAME_TIME
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
    memories: list[MemoryAdd]


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
    agent_id: str
    src_entity: str
    rel_type: str
    dst_entity: str
    event_time: datetime
    exclusive: bool = False              # invalidate other live src--rel_type--> edges
    subject_id: Optional[str] = None
    source: Optional[str] = None
    metadata: dict[str, Any] = Field(default_factory=dict)
    normalize: bool = False              # collapse company/ISIN/CUSIP to canonical ticker


class UnrelateRequest(BaseModel):
    agent_id: str
    src_entity: str
    rel_type: str
    dst_entity: str
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
    agent_id: str
    query: str
    k: int = Field(default=10, ge=1, le=100)
    as_of: Optional[datetime] = None
    max_tokens: int = Field(default=1500, ge=64, le=32000)
    header: str = "Relevant facts from memory (most recent, non-stale):"
    mmr: bool = False                     # diversity reranking before assembly


class ContextResult(BaseModel):
    context: str                          # the assembled block, ready to inject
    memories: list[MemoryOut]             # the facts that fit the budget
    token_estimate: int
    truncated: bool                       # True if the budget cut off some facts


# ── Graph extraction ────────────────────────────────────────────────────────────


class ExtractRequest(BaseModel):
    """Extract relationship edges from unstructured text and write them."""
    agent_id: str
    text: str
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
