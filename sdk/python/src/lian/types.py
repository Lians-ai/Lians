"""
AgentMem Python SDK — Pydantic v2 type definitions.

Mirrors the REST API schemas.  All datetime fields are UTC-aware.
"""
from __future__ import annotations

from datetime import datetime
from typing import Any, Optional
from uuid import UUID

from pydantic import BaseModel, Field


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


# ── Batch ─────────────────────────────────────────────────────────────────────

class MemoryBatchResult(BaseModel):
    added: int
    memories: list[MemoryOut]


# ── Erasure ───────────────────────────────────────────────────────────────────

class EraseRequest(BaseModel):
    subject_id: str
    request_ref: str


class EraseResult(BaseModel):
    subject_id: str
    memories_erased: int
    request_ref: str


class ErasureCertificate(BaseModel):
    certificate_id: str
    subject_id: str
    namespace: str
    request_ref: Optional[str]
    erased_at: datetime
    memories_erased: int
    content_hashes: list[str]
    chain_status: str
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
    nodes: list[LineageNode]
    edges: list[LineageEdge]


# ── Fact history ──────────────────────────────────────────────────────────────

class FactHistoryResult(BaseModel):
    ticker: str
    metric: str
    agent_id: str
    namespace: str
    total: int
    items: list[MemoryOut]


# ── Knowledge snapshot ────────────────────────────────────────────────────────

class KnowledgeSnapshot(BaseModel):
    agent_id: str
    namespace: str
    as_of: datetime
    total: int
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
    status_filter: Optional[str]


class ConflictResolveRequest(BaseModel):
    resolution: str                  # "accept_a" | "accept_b" | "dismiss"
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
    confidence_threshold: float


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


class AuditExportResult(BaseModel):
    namespace: str
    from_: Optional[datetime] = None
    to: Optional[datetime] = None
    total_rows: int
    chain_status: Optional[str]
    chain_violations: Optional[list[dict[str, Any]]]
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
    secret: str                      # shown once at registration


class WebhookUpdateRequest(BaseModel):
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
