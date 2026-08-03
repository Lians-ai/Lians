"""Typed contracts for the Lians Investigator flagship read model."""
from __future__ import annotations

from datetime import datetime
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, Field

from .control_schemas import (
    GateApprovalAttestationOut,
    GateDecisionOut,
    InvestigationCaseOut,
    RemediationTaskOut,
)
from .evidence_schemas import DecisionEvidenceGraphOut
from .schemas import DecisionOut, DecisionReviewEventOut, LedgerEventOut

InvestigationPosture = Literal["defensible", "needs_attention", "blocked"]
PriorityLevel = Literal["low", "medium", "high", "critical"]


class InvestigatorCollectionWindow(BaseModel):
    """Disclosure for one deterministically bounded embedded collection."""

    limit: int = Field(ge=1)
    returned: int = Field(ge=0)
    total: int = Field(ge=0)
    total_is_lower_bound: bool
    truncated: bool
    complete: bool
    ordering: str
    scope: str


class InvestigatorReportCoverage(BaseModel):
    """Machine-readable completeness boundary for an Investigator report."""

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


class InvestigatorClosureOut(BaseModel):
    id: UUID
    resource_type: Literal["case", "task"]
    resource_id: UUID
    attested_by: str
    statement: str | None
    statement_sha256: str
    evidence_refs: list[str]
    attestation_hash: str
    integrity_valid: bool
    attested_at: datetime


class InvestigatorCaseBundle(BaseModel):
    case: InvestigationCaseOut
    tasks: list[RemediationTaskOut]
    closures: list[InvestigatorClosureOut]


class InvestigatorIntegrity(BaseModel):
    audit_chain: dict[str, Any]
    review_chain_status: Literal["ok", "missing", "tampered", "partial"]
    review_chain_violations: list[dict[str, Any]] = Field(default_factory=list)
    approval_attestations_status: Literal["valid", "missing", "invalid", "partial"]
    approval_attestations_valid: bool | None
    invalid_approval_attestation_ids: list[UUID] = Field(default_factory=list)


class InvestigatorRiskSummary(BaseModel):
    posture: InvestigationPosture
    priority_score: int = Field(ge=0, le=100)
    priority_level: PriorityLevel
    receipt_grade: str
    receipt_score: int = Field(ge=0, le=100)
    receipt_missing: list[str]
    maximum_evidence_risk_score: int | None = Field(default=None, ge=0, le=100)
    latest_gate_disposition: str | None
    gate_disposition_counts: dict[str, int]
    open_case_count: int
    overdue_task_count: int
    blockers: list[str]
    attention_signals: list[str]
    recommended_actions: list[str]


class InvestigatorLinks(BaseModel):
    decision: str
    receipt: str
    evidence_pack: str
    evidence_graph: str
    timeline: str
    review_history: str
    gate_evaluations: str
    approval_attestations: str
    cases: str


class DecisionInvestigationReport(BaseModel):
    report_version: Literal["1.1"] = "1.1"
    generated_at: datetime
    decision: DecisionOut
    risk: InvestigatorRiskSummary
    receipt_completeness: dict[str, Any]
    coverage: InvestigatorReportCoverage
    evidence_graph: DecisionEvidenceGraphOut
    timeline: list[LedgerEventOut]
    gate_evaluations: list[GateDecisionOut]
    approval_attestations: list[GateApprovalAttestationOut]
    review_history: list[DecisionReviewEventOut]
    cases: list[InvestigatorCaseBundle]
    integrity: InvestigatorIntegrity
    links: InvestigatorLinks
    disclosures: list[str]


class InvestigatorQueueItem(BaseModel):
    decision: DecisionOut
    priority_score: int = Field(ge=0, le=100)
    priority_level: PriorityLevel
    posture: InvestigationPosture
    signals: list[str]
    latest_gate_disposition: str | None
    open_case_count: int
    maximum_evidence_risk_score: int | None = Field(default=None, ge=0, le=100)
    review_status: str
    normalized_evidence_complete: bool


class InvestigatorQueueOut(BaseModel):
    generated_at: datetime
    items: list[InvestigatorQueueItem]
    candidates_scanned: int
    scan_limit: int
    scan_truncated: bool
    total_is_lower_bound: bool
