"""Typed public contracts for decision evidence, Recorder, and the control plane.

These ``TypedDict`` definitions intentionally have no runtime dependency on the
service package.  Applications can therefore instrument an agent without
installing FastAPI, SQLAlchemy, or Pydantic.
"""

from __future__ import annotations

from typing import Any, Generic, Literal, TypedDict, TypeVar

_PageItem = TypeVar("_PageItem")

RecorderProtocol = Literal["lians", "otlp.genai", "mcp", "a2a"]
CaptureMode = Literal["metadata_only", "hash_only", "full"]
GateDisposition = Literal["allow", "deny", "review"]
RiskLevel = Literal["low", "medium", "high", "critical"]
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
    priority: RiskLevel


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
    priority: RiskLevel
    match_sources: list[Literal["indexed", "legacy_fallback"]]


class ExhaustiveImpactAssessmentResults(TypedDict):
    assessment_id: str
    status: ExhaustiveImpactAssessmentState
    snapshot_complete: bool
    total_matches: int
    items: list[ExhaustiveImpactAssessmentMatch]
    next_cursor: int | None


class RecorderActor(TypedDict, total=False):
    agent_id: str
    principal_id: str
    roles: list[str]
    authentication_context: dict[str, Any]
    extensions: dict[str, Any]


class RecorderCorrelation(TypedDict, total=False):
    run_id: str
    trace_id: str
    span_id: str
    parent_span_id: str
    session_id: str
    task_id: str
    context_id: str
    message_id: str
    tool_call_id: str
    decision_id: str
    extensions: dict[str, Any]


class RecorderCapturePolicy(TypedDict, total=False):
    mode: CaptureMode
    sensitive_fields: list[str]


MeasurementProvenance = Literal[
    "provider-reported",
    "workload-reported",
    "client-measured",
    "deterministic",
    "human-authored",
    "model-judged",
    "estimated",
]


class RecorderMeasurement(TypedDict):
    value: float
    provenance: MeasurementProvenance


class RecorderTokenUsage(TypedDict, total=False):
    input: RecorderMeasurement
    output: RecorderMeasurement
    cached: RecorderMeasurement


class RecorderCost(TypedDict, total=False):
    amount: RecorderMeasurement
    currency: str
    attribution: str


class RecorderOperational(TypedDict, total=False):
    provider: str
    runtime_framework: str
    operation: str
    prompt_hash: str
    toolset_hash: str
    request_configuration_hash: str
    agent_version_id: str
    release_reference: str
    tokens: RecorderTokenUsage
    latency_ms: RecorderMeasurement
    finish_reason: str
    error_code: str
    cost: RecorderCost
    outcome_correlation: str


class RecorderEnvelopeRequired(TypedDict):
    protocol: RecorderProtocol
    payload: dict[str, Any]


class RecorderEnvelope(RecorderEnvelopeRequired, total=False):
    schema_version: Literal["0.1", "0.2"]
    event_type: str
    event_id: str
    idempotency_key: str
    occurred_at: str
    subject_id: str
    actor: RecorderActor
    correlation: RecorderCorrelation
    capture: RecorderCapturePolicy
    operational: RecorderOperational
    extensions: dict[str, Any]


class RecorderEvent(TypedDict):
    id: str
    run_id: str
    protocol: RecorderProtocol
    event_kind: str
    event_name: str | None
    phase: str
    status: str | None
    occurred_at: str
    recorded_at: str
    agent_id: str | None
    actor_attribution: Literal["claimed_unverified", "not_supplied"]
    ingested_by_principal_ref: str
    ingested_by_auth_method: str
    ingested_by_credential_id: str | None
    trace_id: str | None
    span_id: str | None
    task_id: str | None
    decision_id: str | None
    model_id: str | None
    input_hash: str | None
    output_hash: str | None
    capture_mode: CaptureMode
    capture_gaps: list[str]
    diagnostics: list[dict[str, Any]]
    operational: RecorderOperational
    event_hash: str
    event_hash_version: Literal[1, 2]


class RecorderRunReadiness(TypedDict):
    run_id: str
    correlation_type: str
    boundary_kind: Literal["run", "decision"]
    status: str
    event_count: int
    protocols: list[RecorderProtocol]
    score: int
    receipt_ready: bool
    ready_at: str | None
    missing_fields: list[str]
    diagnostics: list[dict[str, Any]]
    first_event_at: str
    last_event_at: str
    time_to_readiness_ms: int | None


class RecorderIngestResult(TypedDict):
    accepted: bool
    duplicate: bool
    event: RecorderEvent
    readiness: RecorderRunReadiness


class RecorderBatchRejection(TypedDict):
    index: int
    code: str
    detail: str


class RecorderBatchResult(TypedDict):
    received: int
    accepted: int
    duplicates: int
    rejected: int
    results: list[RecorderIngestResult]
    rejections: list[RecorderBatchRejection]
    ready_run_ids: list[str]


class RecorderEvidenceIndexJob(TypedDict):
    id: str
    decision_id: str
    status: Literal["pending", "running", "completed", "failed"]
    snapshot_max_recorded_at: str
    snapshot_max_event_id: str
    snapshot_event_count: int
    cursor_recorded_at: str | None
    cursor_event_id: str | None
    events_indexed: int
    events_remaining: int
    artifacts_created: int
    links_created: int
    pages_completed: int
    processing_attempts: int
    progress_ratio: float
    complete: bool
    next_attempt_at: str
    last_error_code: str | None
    last_error_digest: str | None
    failure_code: str | None
    created_at: str
    started_at: str | None
    updated_at: str
    completed_at: str | None
    failed_at: str | None


class FirstReceiptReadiness(TypedDict):
    namespace: str
    evaluated_at: str
    total_runs: int
    ready_runs: int
    waiting_runs: int
    readiness_rate: float
    first_ready_run_id: str | None
    first_ready_at: str | None
    next_actions: list[str]
    runs: list[RecorderRunReadiness]


class GatePolicyRuleCreateRequired(TypedDict):
    name: str


class GatePolicyRuleCreate(GatePolicyRuleCreateRequired, total=False):
    description: str
    priority: int
    enabled: bool
    action_on_failure: Literal["deny", "review"]
    applies_to_decision_types: list[str]
    applies_to_risk_levels: list[RiskLevel]
    required_receipt_grade: Literal["A", "B", "C", "D", "F"]
    require_trusted_issuer: bool
    require_sources_current: bool
    require_policy_attached: bool
    required_principal_scopes: list[str]
    minimum_approval_count: int
    required_approval_roles: list[str]
    allowed_approval_principal_types: list[Literal["human", "workload", "api_key"]]
    maximum_approval_age_seconds: int
    require_information_barrier_match: bool
    block_untrusted_content: bool
    max_untrusted_content_score: int


class GatePolicySetCreateRequired(TypedDict):
    name: str
    version: str
    protected_actions: list[str]
    target_ref_prefixes: list[str]
    enforcement_principal_ids: list[str]
    rules: list[GatePolicyRuleCreate]


class GatePolicySetCreate(GatePolicySetCreateRequired, total=False):
    actor_id: str
    description: str
    barrier_group: str | None
    default_disposition: GateDisposition
    maximum_permit_ttl_seconds: int
    metadata: dict[str, Any]


class GateReceiptContext(TypedDict, total=False):
    grade: Literal["A", "B", "C", "D", "F"]
    receipt_hash: str
    issuer_id: str
    key_id: str
    # Used for in-process cryptographic verification; the Gate persists only
    # its digest reference, never this potentially sensitive document.
    document: dict[str, Any]


class GateApprovalRequired(TypedDict):
    principal_id: str
    role: str


class GateApproval(GateApprovalRequired, total=False):
    status: Literal["approved", "rejected", "pending"]
    attestation_ref: str
    principal_type: str
    auth_method: str
    attested_at: str


class GateApprovalAttestationCreateRequired(TypedDict):
    action: str
    decision_id: str
    policy_set_id: str
    target_ref: str


class GateApprovalAttestationCreate(GateApprovalAttestationCreateRequired, total=False):
    change_event_id: str
    target_barrier_group: str | None
    receipt_hash: str
    status: Literal["approved", "rejected"]
    statement: str
    evidence_refs: list[str]
    expires_at: str


class GateApprovalAttestationSupersedeRequired(TypedDict):
    status: Literal["approved", "rejected", "revoked"]


class GateApprovalAttestationSupersede(GateApprovalAttestationSupersedeRequired, total=False):
    statement: str
    evidence_refs: list[str]
    expires_at: str


class GateApprovalAttestation(TypedDict, total=False):
    id: str
    namespace: str
    barrier_group: str | None
    series_key: str
    sequence: int
    approval_principal_id: str
    attested_by: str
    principal_type: str | None
    attester_role: str
    auth_method: str
    credential_id: str | None
    status: Literal["approved", "rejected", "revoked"]
    action: str
    decision_id: str | None
    change_event_id: str | None
    policy_set_id: str
    policy_hash: str
    target_ref: str | None
    target_barrier_group: str | None
    receipt_hash: str | None
    context_hash: str
    statement: str | None
    statement_hash: str | None
    evidence_refs: list[str]
    expires_at: str | None
    supersedes_id: str | None
    prior_attestation_hash: str | None
    attestation_hash: str
    attested_at: str


class UntrustedContentSignalRequired(TypedDict):
    signal_type: str
    score: int


class UntrustedContentSignal(UntrustedContentSignalRequired, total=False):
    source: str
    trusted: bool
    details: dict[str, Any]


class GateEvaluationRequired(TypedDict):
    action: str
    target_ref: str
    decision_id: str
    enforcement_principal_id: str
    permit_ttl_seconds: int
    execution_request_hash: str


class GateEvaluationRequest(GateEvaluationRequired, total=False):
    principal_id: str
    # Advanced assertion fields. Normal callers should omit these because the
    # service derives scopes and barriers from the authenticated credential.
    principal_scopes: list[str]
    principal_barrier_group: str | None
    target_barrier_group: str | None
    decision_type: str
    risk_level: RiskLevel
    change_event_id: str
    policy_set_id: str
    policy_name: str
    policy_version: str
    receipt: GateReceiptContext
    sources_current: bool
    attached_policy_version: str
    approval_ids: list[str]
    untrusted_content_signals: list[UntrustedContentSignal]
    context: dict[str, Any]


class GatePolicySet(TypedDict, total=False):
    id: str
    namespace: str
    barrier_group: str | None
    name: str
    version: str
    description: str | None
    status: Literal["draft", "active", "retired"]
    default_disposition: GateDisposition
    protected_actions: list[str]
    target_ref_prefixes: list[str]
    enforcement_principal_ids: list[str]
    maximum_permit_ttl_seconds: int
    created_by: str
    created_at: str
    activated_by: str | None
    activated_at: str | None
    retired_at: str | None
    policy_hash: str
    metadata: dict[str, Any]
    rules: list[dict[str, Any]]


class GateDecision(TypedDict, total=False):
    id: str
    namespace: str
    barrier_group: str | None
    policy_set_id: str
    policy_name: str
    policy_version: str
    policy_hash: str
    principal_id: str
    action: str
    target_ref: str
    enforcement_principal_id: str | None
    execution_request_hash: str | None
    decision_id: str | None
    change_event_id: str | None
    receipt_hash: str | None
    disposition: GateDisposition
    reasons: list[dict[str, Any]]
    applied_rules: list[dict[str, Any]]
    input_snapshot: dict[str, Any]
    request_hash: str
    evaluation_hash: str
    evaluated_at: str


class GateExecutionPermitIssued(TypedDict):
    permit_id: str
    evaluation_id: str
    enforcement_principal_id: str
    action: str
    target_ref: str
    decision_id: str
    execution_request_hash: str
    issued_at: str
    expires_at: str
    token: str


class GateEvaluationResult(GateDecision, total=False):
    execution_permit: GateExecutionPermitIssued | None


class GateExecutionPermitConsume(TypedDict):
    permit_id: str
    token: str
    action: str
    target_ref: str
    decision_id: str
    execution_request_hash: str


class GateExecutionPermitConsumption(TypedDict):
    id: str
    namespace: str
    barrier_group: str | None
    permit_id: str
    evaluation_id: str
    policy_set_id: str
    decision_id: str
    consuming_principal_id: str
    action: str
    target_ref: str
    execution_request_hash: str
    grant_hash: str
    consumed_at: str
    consumption_hash: str


class InvestigationCaseCreateRequired(TypedDict):
    title: str


class InvestigationCaseCreate(InvestigationCaseCreateRequired, total=False):
    actor_id: str
    description: str
    severity: RiskLevel
    owner_principal: str
    barrier_group: str | None
    decision_id: str
    change_event_id: str
    gate_decision_id: str
    metadata: dict[str, Any]


class InvestigationCaseUpdateRequired(TypedDict):
    expected_updated_at: str


class InvestigationCaseUpdate(InvestigationCaseUpdateRequired, total=False):
    actor_id: str
    owner_principal: str | None
    status: Literal["open", "in_review", "remediating", "resolved"]
    severity: RiskLevel
    resolution_summary: str | None


class InvestigationCase(TypedDict, total=False):
    id: str
    namespace: str
    barrier_group: str | None
    title: str
    description: str | None
    severity: RiskLevel
    status: str
    owner_principal: str | None
    decision_id: str | None
    change_event_id: str | None
    gate_decision_id: str | None
    opened_by: str
    opened_at: str
    updated_at: str
    closed_at: str | None
    resolution_summary: str | None
    metadata: dict[str, Any]


class RemediationTaskCreateRequired(TypedDict):
    title: str
    expected_case_updated_at: str


class RemediationTaskCreate(RemediationTaskCreateRequired, total=False):
    actor_id: str
    description: str
    owner_principal: str
    due_at: str
    decision_id: str
    change_event_id: str
    metadata: dict[str, Any]


class RemediationTaskUpdateRequired(TypedDict):
    expected_updated_at: str


class RemediationTaskUpdate(RemediationTaskUpdateRequired, total=False):
    actor_id: str
    owner_principal: str | None
    status: Literal["pending", "in_progress", "blocked", "cancelled"]
    due_at: str | None


class RemediationTask(TypedDict, total=False):
    id: str
    namespace: str
    barrier_group: str | None
    case_id: str
    title: str
    description: str | None
    status: str
    owner_principal: str | None
    due_at: str | None
    decision_id: str | None
    change_event_id: str | None
    created_by: str
    created_at: str
    updated_at: str
    closed_at: str | None
    metadata: dict[str, Any]


class ClosureAttestationCreateRequired(TypedDict):
    expected_updated_at: str
    statement: str
    evidence_refs: list[str]


class ClosureAttestationCreate(ClosureAttestationCreateRequired, total=False):
    actor_id: str
    resolution_summary: str


class ClosureAttestation(TypedDict, total=False):
    id: str
    namespace: str
    barrier_group: str | None
    resource_type: Literal["case", "task"]
    resource_id: str
    attested_by: str
    statement: str | None
    statement_hash: str
    hash_version: Literal[1, 2]
    evidence_refs: list[str]
    decision_id: str | None
    change_event_id: str | None
    attestation_hash: str
    attested_at: str


class AttestedClosure(TypedDict):
    resource_type: Literal["case", "task"]
    resource_id: str
    status: Literal["closed"]
    attestation: ClosureAttestation


class SupersessionActionResult(TypedDict):
    memory_id: str
    action: Literal["confirm", "reject"]
    applied_at: str


class Principal(TypedDict):
    namespace: str
    scopes: list[str]
    barrier_group: str | None
    principal_id: str | None
    principal_type: str | None
    auth_method: str
    credential_id: str | None


class _WorkloadCredentialCreateRequired(TypedDict):
    ttl_seconds: int


class WorkloadCredentialCreate(_WorkloadCredentialCreateRequired, total=False):
    label: str | None
    role: Literal["owner", "analyst", "compliance", "readonly"] | None
    scopes: list[str]
    barrier_group: str | None


class WorkloadCredentialRotate(TypedDict):
    expected_version: int
    ttl_seconds: int


class WorkloadCredential(TypedDict):
    id: str
    namespace: str
    label: str | None
    scopes: list[str]
    effective_scopes: list[str]
    role: Literal["owner", "analyst", "compliance", "readonly"] | None
    barrier_group: str | None
    provisioning_source: Literal["tenant_oidc"]
    created_by: str
    created_at: str
    expires_at: str
    last_used_at: str | None
    rotated_from_id: str | None
    rotated_at: str | None
    revoked_at: str | None
    version: int
    status: Literal["active", "expired", "revoked", "rotated"]


class WorkloadCredentialCreated(WorkloadCredential):
    secret: str


MeteringStatus = Literal["pending", "leased", "retry", "delivered", "dead_letter"]


class MeteringInventory(TypedDict):
    delivery_enabled: bool
    worker_enabled: bool
    provider_configured: bool
    async_error_destination_configured: bool
    worker_healthy: bool
    worker_last_poll_at: str | None
    worker_last_heartbeat_at: str | None
    worker_last_delivery_at: str | None
    worker_last_error_at: str | None
    worker_last_error_digest: str | None
    worker_terminal_error: str | None
    pending_events: int
    leased_events: int
    retry_events: int
    delivered_events: int
    dead_letter_events: int
    oldest_due_at: str | None


class MeteringEvent(TypedDict):
    id: str
    namespace: str
    event_name: str
    provider_identifier: str
    quantity: int
    status: MeteringStatus
    attempt_count: int
    attempt_limit: int
    replay_count: int
    next_attempt_at: str
    first_attempt_at: str | None
    last_attempt_at: str | None
    delivered_at: str | None
    dead_lettered_at: str | None
    last_status_code: int | None
    last_error_code: str | None
    last_error_digest: str | None
    occurred_at: str
    created_at: str
    updated_at: str


class MeteringReplayRequest(TypedDict):
    reconciliation: Literal["provider_confirmed_not_accepted"]
    reconciliation_reference: str


class ScimTenantReconciliation(TypedDict):
    id: str
    tenant_config_id: str
    namespace: str
    target_config_version: int
    target_enabled: bool
    target_revoked_at: str | None
    status: Literal["pending", "running", "completed", "failed", "superseded"]
    snapshot_max_created_at: str | None
    snapshot_max_user_id: str | None
    snapshot_user_count: int
    cursor_created_at: str | None
    cursor_user_id: str | None
    users_reconciled: int
    pages_completed: int
    processing_attempts: int
    consecutive_failures: int
    attempt_limit: int
    next_attempt_at: str
    lease_expires_at: str | None
    heartbeat_at: str | None
    last_attempt_at: str | None
    last_error_code: str | None
    last_error_digest: str | None
    failure_code: str | None
    created_at: str
    started_at: str | None
    updated_at: str
    completed_at: str | None
    failed_at: str | None
    superseded_at: str | None
    snapshot_complete: bool
    progress_complete: bool
    completion_scope: Literal["tenant_user_created_at_id_snapshot"]


class InvestigatorQueueItem(TypedDict):
    decision: dict[str, Any]
    priority_score: int
    priority_level: Literal["low", "medium", "high", "critical"]
    posture: Literal["defensible", "needs_attention", "blocked"]
    signals: list[str]
    latest_gate_disposition: str | None
    open_case_count: int
    maximum_evidence_risk_score: int | None
    review_status: str
    normalized_evidence_complete: bool


class InvestigatorQueue(TypedDict):
    generated_at: str
    items: list[InvestigatorQueueItem]
    candidates_scanned: int
    scan_limit: int
    scan_truncated: bool
    total_is_lower_bound: bool


class InvestigatorRiskSummary(TypedDict):
    posture: Literal["defensible", "needs_attention", "blocked"]
    priority_score: int
    priority_level: Literal["low", "medium", "high", "critical"]
    receipt_grade: str
    receipt_score: int
    receipt_missing: list[str]
    maximum_evidence_risk_score: int | None
    latest_gate_disposition: str | None
    gate_disposition_counts: dict[str, int]
    open_case_count: int
    overdue_task_count: int
    blockers: list[str]
    attention_signals: list[str]
    recommended_actions: list[str]


class InvestigatorCollectionWindow(TypedDict):
    limit: int
    returned: int
    total: int
    total_is_lower_bound: bool
    truncated: bool
    complete: bool
    ordering: str
    scope: str


class InvestigatorReportCoverage(TypedDict):
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


class InvestigatorIntegrity(TypedDict):
    audit_chain: dict[str, Any]
    review_chain_status: Literal["ok", "missing", "tampered", "partial"]
    review_chain_violations: list[dict[str, Any]]
    approval_attestations_status: Literal["valid", "missing", "invalid", "partial"]
    approval_attestations_valid: bool | None
    invalid_approval_attestation_ids: list[str]


class DecisionInvestigationReport(TypedDict, total=False):
    report_version: Literal["1.1"]
    generated_at: str
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


class LiansDiscovery(TypedDict):
    name: Literal["Lians"]
    category: Literal["decision_evidence_infrastructure"]
    api_version: str
    decision_receipt_version: str
    universal_recorder_version: str
    protocols: list[str]
    authentication: list[str]
    links: dict[str, str]


class PlatformCapabilities(TypedDict):
    generated_at: str
    namespace: str
    principal_type: str
    authentication_method: str
    information_barrier_scoped: bool
    components: dict[str, dict[str, Any]]
    standards: dict[str, dict[str, Any]]
    privacy: dict[str, Any]
    links: dict[str, str]


class PlatformReadiness(TypedDict):
    generated_at: str
    namespace: str
    status: Literal["ready", "degraded", "configuration_required"]
    production_baseline_ready: bool
    control_plane_ready: bool
    enterprise_identity_ready: bool
    checks: list[dict[str, Any]]
    inventory: dict[str, int]
    disclosures: list[str]


class IssuerCreateRequired(TypedDict):
    name: str


class IssuerCreate(IssuerCreateRequired, total=False):
    actor_id: str
    issuer_uri: str
    description: str
    barrier_group: str | None
    metadata: dict[str, Any]


class ReceiptIssuer(TypedDict, total=False):
    id: str
    namespace: str
    barrier_group: str | None
    name: str
    issuer_uri: str | None
    description: str | None
    status: Literal["active", "revoked"]
    metadata: dict[str, Any]
    created_by: str
    created_at: str
    revoked_by: str | None
    revoked_at: str | None
    revocation_reason: str | None


class TrustedKeyCreateRequired(TypedDict):
    key_id: str
    public_key: str


class TrustedKeyCreate(TrustedKeyCreateRequired, total=False):
    actor_id: str
    algorithm: Literal["ed25519"]
    valid_from: str
    valid_until: str
    metadata: dict[str, Any]


class TrustedKeyRotateRequired(TypedDict):
    key_id: str
    public_key: str
    reason: str


class TrustedKeyRotate(TrustedKeyRotateRequired, total=False):
    actor_id: str
    algorithm: Literal["ed25519"]
    valid_from: str
    valid_until: str
    metadata: dict[str, Any]


class TrustedReceiptKey(TypedDict, total=False):
    id: str
    namespace: str
    barrier_group: str | None
    issuer_id: str
    key_id: str
    algorithm: Literal["ed25519"]
    public_key: str
    public_key_format: Literal["raw-base64"]
    fingerprint_sha256: str
    status: Literal["active", "revoked"]
    valid_from: str
    valid_until: str | None
    created_by: str
    created_at: str
    revoked_by: str | None
    revoked_at: str | None
    revocation_reason: str | None
    rotated_at: str | None
    rotated_from_key_id: str | None
    replaced_by_key_id: str | None
    rotation_reason: str | None
    metadata: dict[str, Any]
