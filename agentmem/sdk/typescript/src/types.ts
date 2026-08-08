// Lians TypeScript SDK — type definitions
// Mirrors the Pydantic schemas in src/lians/schemas.py

// ── Write ────────────────────────────────────────────────────────────────────

export interface MemoryAdd {
  agent_id: string;
  content: string;
  /** ISO-8601 timestamp of when this event occurred in the world — NOT ingestion time */
  event_time: string;
  source?: string;
  subject_id?: string;
  metadata?: Record<string, unknown>;
  /** Importance weight 0.0–1.0; default 0.5 */
  importance?: number;
}

// ── Core memory object ───────────────────────────────────────────────────────

export interface MemoryOut {
  id: string;
  namespace: string;
  agent_id: string;
  content: string | null;              // null if erased
  subject_id: string | null;
  event_time: string;                  // ISO 8601
  ingestion_time: string;
  valid_from: string;
  valid_to: string | null;             // null = still currently valid
  superseded_by: string | null;
  supersession_confidence: number | null;
  barrier_group: string | null;
  importance: number;
  source: string | null;
  content_hash: string;
  erased_at: string | null;
  metadata: Record<string, unknown>;
}

// ── Recall ───────────────────────────────────────────────────────────────────

export interface RecallRequest {
  agent_id: string;
  query: string;
  k?: number;
  /** ISO 8601 — point-in-time recall; omit for current valid memories */
  as_of?: string;
  filters?: Record<string, unknown>;
}

export interface RecallResult {
  memories: MemoryOut[];
  as_of: string | null;
  total_candidates: number;
  retrieval_degraded: boolean;
  graph_search_complete: boolean;
  candidate_window_complete: boolean;
  candidates_considered: number;
  candidate_limit: number;
  candidate_mode: string;
}

// ── Batch ────────────────────────────────────────────────────────────────────

export interface MemoryBatchResult {
  added: number;
  memories: MemoryOut[];
}

// ── Erasure (GDPR Art. 17 / CCPA) ───────────────────────────────────────────

export interface EraseRequest {
  subject_id: string;
  request_ref: string;
}

export interface SubjectErasureSnapshot {
  memories: number;
  live_facts: number;
  relationships: number;
  pending_admissions: number;
  total_rows: number;
}

export interface SubjectErasureProgress {
  memories: number;
  live_facts: number;
  relationships: number;
  pending_admissions: number;
  rows_scrubbed: number;
  pages_completed: number;
  ratio: number;
}

export interface EraseResult {
  job_id: string;
  namespace: string;
  subject_ref: string;
  request_ref: string;
  status: "pending" | "running" | "completed" | "failed";
  phase: "memories" | "live_facts" | "relationships" | "pending_admissions" | "finalizing" | "completed";
  key_destroyed_at: string;
  cache_fenced_at: string;
  snapshot: SubjectErasureSnapshot;
  progress: SubjectErasureProgress;
  processing_attempts: number;
  next_attempt_at: string;
  last_error_code: string | null;
  last_error_digest: string | null;
  failure_code: string | null;
  created_at: string;
  started_at: string | null;
  updated_at: string;
  completed_at: string | null;
  replayed: boolean;
}

// ── Lineage ──────────────────────────────────────────────────────────────────

export interface LineageNode {
  id: string;
  content: string | null;
  content_hash: string;
  event_time: string;
  ingestion_time: string;
  valid_from: string;
  valid_to: string | null;
  source: string | null;
  importance: number;
  supersession_confidence: number | null;
  erased_at: string | null;
  metadata: Record<string, unknown>;
  is_current: boolean;
}

export interface LineageEdge {
  from_id: string;
  to_id: string;
  relation: string;
  confidence: number;
  rationale: string | null;
  adjudication_stage: number;
  superseded_at: string;
  audit_event_id: string | null;
  audit_chain_position: number | null;
  audit_binding_status: "bound" | "missing" | "target_mismatch" | "malformed";
}

export interface MemoryLineageResult {
  agent_id: string;
  namespace: string;
  queried_id: string;
  root_id: string;
  tip_id: string;
  root_ids: string[];
  tip_ids: string[];
  shape: "chain" | "dag";
  depth: number;
  edge_count: number;
  truncated: boolean;
  has_more: boolean;
  complete: boolean;
  root_complete: boolean;
  tip_complete: boolean;
  reachable_nodes: number;
  reachable_nodes_is_lower_bound: boolean;
  audit_binding_complete: boolean;
  max_nodes: number;
  nodes: LineageNode[];
  edges: LineageEdge[];
}

// ── Conflicts ────────────────────────────────────────────────────────────────

export interface ConflictFlagOut {
  id: string;
  namespace: string;
  agent_id: string;
  memory_a_id: string;
  memory_b_id: string;
  memory_a_content: string | null;
  memory_b_content: string | null;
  memory_a_source: string | null;
  memory_b_source: string | null;
  memory_a_event_time: string;
  memory_b_event_time: string;
  confidence: number;
  detected_at: string;
  status: "open" | "accept_a" | "accept_b" | "dismissed";
  resolved_at: string | null;
  resolver_note: string | null;
}

export interface ConflictListResult {
  conflicts: ConflictFlagOut[];
  total: number;
  returned: number;
  complete: boolean;
  has_more: boolean;
  next_detected_at: string | null;
  next_id: string | null;
  status_filter: string | null;
}

export interface ConflictResolveRequest {
  resolution: "accept_a" | "accept_b" | "dismiss";
  note?: string;
}

export interface ConflictResolveResult {
  conflict_id: string;
  resolution: string;
  resolved_at: string;
  memory_invalidated: string | null;
}

// ── Supersession review ──────────────────────────────────────────────────────

export interface SupersessionReviewItem {
  event_id: string;
  memory_id: string;
  superseded_by: string | null;
  confidence: number;
  relation: string;
  rationale: string | null;
  adjudication_stage: number;
  created_at: string;
  content_hash: string | null;
}

export interface SupersessionReviewResult {
  items: SupersessionReviewItem[];
  total: number;
  returned: number;
  complete: boolean;
  has_more: boolean;
  next_chain_position: number | null;
  confidence_threshold: number;
}

/** Optimistic relationship precondition copied from a review item. */
export interface SupersessionActionRequest {
  expected_superseded_by: string | null;
  reviewer_note?: string | null;
}

export interface SupersessionActionResult {
  memory_id: string;
  action: "confirm" | "reject";
  applied_at: string;
}

// ── Audit / chain ────────────────────────────────────────────────────────────

export interface AuditEvent {
  id: string;
  namespace: string;
  agent_id: string;
  op: string;                          // add | supersede | recall | erase | ...
  memory_id: string | null;
  content_hash: string | null;
  payload: Record<string, unknown>;
  created_at: string;
  prev_hash: string | null;
  row_hash: string | null;
  hash_version: number;
  chain_position: number;
}

export interface AuditChainViolation {
  row_id: string;
  kind: string;
  detail: string;
}

export interface AuditChainVerifyResult {
  namespace: string;
  rows_checked: number;
  status: "ok" | "partial" | "tampered";
  truncated: boolean;
  chain_tip: string | null;
  violations: AuditChainViolation[];
}

export interface AuditExportResult {
  namespace: string;
  from_: string | null;
  to: string | null;
  total_rows: number;
  returned_rows: number;
  has_more: boolean;
  complete: boolean;
  next_chain_position: number | null;
  snapshot_max_chain_position: number;
  chain_status: string | null;
  chain_violations: AuditChainViolation[] | null;
  chain_rows_checked: number | null;
  chain_truncated: boolean | null;
  chain_tip: string | null;
  events: AuditEvent[];
}

// ── Compliance report ────────────────────────────────────────────────────────

export interface ComplianceMemorySummary {
  total_memories: number;
  active_memories: number;
  superseded_memories: number;
  erased_memories: number;
  new_in_window: number;
  superseded_in_window: number;
}

export interface ComplianceAuditChain {
  status: "ok" | "partial" | "tampered" | "unchecked";
  rows_checked: number;
  violations: Record<string, unknown>[];
}

export interface ComplianceErasures {
  total_requests: number;
  total_records_erased: number;
  subject_ids: string[];
  subject_ids_total: number;
  subject_ids_complete: boolean;
  subject_ids_limit: number;
}

export interface ComplianceConflicts {
  open: number;
  resolved_accept_a: number;
  resolved_accept_b: number;
  dismissed: number;
  detected_in_window: number;
}

export interface ComplianceSupersessions {
  total_supersessions: number;
  confirmed_by_human: number;
  rejected_by_human: number;
  high_confidence: number;
  low_confidence: number;
}

export interface ComplianceRetention {
  content_ttl_days: number | null;
  audit_retention_days: number;
  legal_hold: boolean;
  stripe_customer_id: string | null;
}

export interface ComplianceReport {
  namespace: string;
  generated_at: string;
  window_from: string | null;
  window_to: string | null;
  summary: ComplianceMemorySummary;
  audit_chain: ComplianceAuditChain;
  erasures: ComplianceErasures;
  conflicts: ComplianceConflicts;
  supersessions: ComplianceSupersessions;
  retention: ComplianceRetention | null;
}

// ── Consequential decisions and portable receipts ───────────────────────────

export interface DecisionCreate {
  agent_id: string;
  decision_type: string;
  outcome: string;
  reason_codes?: string[];
  regime?: string;
  subject_id?: string;
  session_id?: string;
  model_id?: string;
  model_version?: string;
  policy_version?: string;
  /** ISO-8601 business/event time when the decision occurred. */
  decided_at: string;
  /** ISO-8601 business/event-time cutoff for the reconstructed knowledge boundary. */
  knowledge_as_of?: string;
  /** ISO-8601 recording-time cutoff that excludes later-ingested backdated evidence. */
  knowledge_recorded_as_of?: string;
  evidence_memory_ids?: string[];
  input_hash?: string;
  output_hash?: string;
  supersedes_id?: string;
  metadata?: Record<string, unknown>;
}

export interface DecisionOut {
  id: string;
  namespace: string;
  /** Workload-supplied label; this is not an authenticated identity. */
  agent_id: string;
  /** Canonical server-derived identity of the principal that recorded the decision. */
  recorded_by_principal_ref: string;
  recorded_by_auth_method: string;
  /** Non-secret, hash-addressed reference to the authenticating credential. */
  recorded_by_credential_ref: string | null;
  /** Server-derived principal category captured by DecisionRecord v3. */
  recorded_by_principal_type: string | null;
  /** Named authorization role at the exact recording boundary. */
  recorded_by_role: "owner" | "analyst" | "compliance" | "readonly" | null;
  /** Canonical effective scopes, including role-derived grants, at write time. */
  recorded_by_scopes: string[];
  decision_type: string;
  outcome: string;
  reason_codes: string[];
  regime: string | null;
  subject_id: string | null;
  session_id: string | null;
  model_id: string | null;
  model_version: string | null;
  policy_version: string | null;
  decided_at: string;
  recorded_at: string;
  knowledge_as_of: string;
  knowledge_recorded_as_of: string;
  evidence_memory_ids: string[];
  input_hash: string | null;
  output_hash: string | null;
  human_review_status: string;
  human_reviewer: string | null;
  human_reviewed_at: string | null;
  supersedes_id: string | null;
  metadata: Record<string, unknown>;
  record_hash_version: number;
  record_integrity_status: "verified" | "legacy_unverified";
  record_hash: string;
}

/** Pagination metadata carried in additive headers by legacy array endpoints. */
export interface CompatibilityListPage<T> {
  items: T[];
  /** Exact filtered collection cardinality before applying the page cursor. */
  total: number;
  limit: number;
  returned: number;
  has_more: boolean;
  /** True when no page follows the supplied cursor. */
  page_complete: boolean;
  /** True only when this un-cursored array is the complete collection. */
  collection_complete: boolean;
  next_cursor: Record<string, string> | null;
}

export interface LedgerEventOut {
  id: string;
  namespace: string;
  event_type: string;
  /** Workload-supplied label; this is not an authenticated identity. */
  agent_id: string;
  occurred_at: string;
  recorded_at: string;
  subject_id: string | null;
  session_id: string | null;
  decision_id: string | null;
  model_id: string | null;
  model_version: string | null;
  payload: Record<string, unknown>;
  artifact_hash: string | null;
  event_hash: string;
}

export interface EvidenceArtifactOut {
  id: string;
  namespace: string;
  barrier_group: string | null;
  kind: "source" | "policy" | "model" | "tool" | "permission" | "instruction" | "input" | "output";
  identifier: string;
  version: string | null;
  coordinate: string;
  hash_algorithm: string;
  artifact_hash: string | null;
  identity_hash: string;
  metadata: Record<string, unknown>;
  risk_metadata: Record<string, unknown>;
  created_by_agent_id: string | null;
  recorded_at: string;
}

export interface DecisionEvidenceGraphResult {
  decision_id: string;
  namespace: string;
  links_total: number;
  links_returned: number;
  links_complete: boolean;
  has_more: boolean;
  next_relation: "direct" | "reachable" | null;
  next_link_id: string | null;
  artifacts_total: number;
  artifacts_returned: number;
  direct_count: number;
  reachable_count: number;
  artifacts: Array<Record<string, unknown>>;
  links: Array<Record<string, unknown>>;
  coverage: Record<string, unknown>;
}

export interface DecisionReviewEvent {
  id: string;
  namespace: string;
  barrier_group: string | null;
  decision_id: string;
  sequence: number;
  status: string;
  reviewer_principal_id: string;
  reviewer_principal_type: string | null;
  reviewer_role: string | null;
  auth_method: string;
  credential_id: string | null;
  note: string | null;
  note_hash: string | null;
  prior_event_hash: string | null;
  event_hash: string;
  reviewed_at: string;
}

export interface DecisionReviewHistoryResult {
  decision_id: string;
  total: number;
  returned: number;
  complete: boolean;
  has_more: boolean;
  next_sequence: number | null;
  page_chain_verified: boolean;
  chain_scope_complete: boolean;
  events: DecisionReviewEvent[];
}

export interface DecisionReceiptIssuer {
  name: string;
  category: "decision_evidence_infrastructure";
  key_id: string | null;
}

export interface DecisionReceiptDecision {
  id: string;
  namespace: string | null;
  type: string;
  outcome: string | null;
  reason_codes: string[];
  regime: string | null;
  subject_id: string | null;
  decided_at: string;
  recorded_at: string;
  knowledge_as_of: string;
  knowledge_recorded_as_of: string | null;
  record_hash: string | null;
  record_hash_version: number | null;
  record_integrity_status: "verified" | "legacy_unverified" | null;
  supersedes_id: string | null;
}

export interface DecisionReceiptActor {
  /** Deprecated trust-wise: caller-claimed workload label. */
  agent_id: string;
  claimed_agent_id: string;
  principal: {
    id: string | null;
    auth_method: string | null;
    credential_ref: string | null;
    type?: string | null;
    role?: "owner" | "analyst" | "compliance" | "readonly" | null;
    scopes?: string[];
  } | null;
  recorded_by: {
    principal_ref: string | null;
    auth_method: string | null;
    credential_ref: string | null;
    principal_type?: string | null;
    role?: "owner" | "analyst" | "compliance" | "readonly" | null;
    scopes?: string[];
    authorization_snapshot_verified?: boolean;
  };
}

export interface DecisionReceiptRecordingWriteAuthorization {
  verified: boolean;
  decision: "allowed" | "unverified";
  action: "decision.record";
  principal_ref: string | null;
  principal_type: string | null;
  role: "owner" | "analyst" | "compliance" | "readonly" | null;
  scopes: string[];
  auth_method: string | null;
  credential_ref: string | null;
}

export interface DecisionReceiptDeclaredWorkflowAuthorization {
  verified: false;
  source: "caller_supplied_decision_metadata";
  authorization?: unknown;
  permissions?: unknown;
}

export interface DecisionReceiptAuthorization {
  recording_write?: DecisionReceiptRecordingWriteAuthorization;
  declared_workflow_context?: DecisionReceiptDeclaredWorkflowAuthorization | null;
  [key: string]: unknown;
}

export interface DecisionReceiptModel {
  provider: string | null;
  id: string | null;
  version: string | null;
  system_instruction_hash: string | null;
  configuration_hash: string | null;
}

export interface DecisionReceiptArtifacts {
  input_hash: string | null;
  output_hash: string | null;
}

export interface DecisionReceiptTool {
  [key: string]: unknown;
  name?: string;
  tool_id?: string;
  definition_hash?: string;
  result_hash?: string;
}

export interface DecisionReceiptSource {
  memory_id: string;
  source: string | null;
  source_version: string | null;
  content: string | null;
  content_hash: string | null;
  valid_from: string | null;
  valid_to: string | null;
  recorded_at: string | null;
  erased_at: string | null;
}

export interface DecisionReceiptPolicy {
  version: string | null;
  evaluation: Record<string, unknown> | string | null;
}

export interface DecisionReceiptHumanReview {
  status: string | null;
  reviewer: string | null;
  reviewed_at: string | null;
}

export interface DecisionReceiptCorrelation {
  session_id: string | null;
  trace_id: string | null;
  span_id: string | null;
}

export interface DecisionReceiptSnapshotItem {
  memory_id: string;
  content_hash: string | null;
  valid_from: string | null;
  valid_to: string | null;
}

export interface DecisionReceiptReconstruction {
  knowledge_as_of: string;
  knowledge_recorded_as_of: string | null;
  snapshot_count: number;
  cited_source_count: number;
  snapshot_manifest: DecisionReceiptSnapshotItem[];
}

export interface DecisionReceiptAuditChain {
  status: string;
  rows_checked?: number;
  violations?: unknown[];
  lians_evidence_graph?: DecisionReceiptEvidenceGraphManifest;
  receipt_exported_at?: string;
  [key: string]: unknown;
}

export interface DecisionReceiptEvidenceGraphArtifact {
  id: string;
  kind: "source" | "policy" | "model" | "tool" | "permission" | "instruction" | "input" | "output";
  identifier: string;
  version: string | null;
  hash_algorithm: string;
  artifact_hash: string | null;
  identity_hash: string;
  recorded_at: string;
  metadata: Record<string, string | number | boolean>;
}

export interface DecisionReceiptEvidenceGraphEntry {
  link_id: string;
  relation: "direct" | "reachable";
  match_basis: string[];
  artifact: DecisionReceiptEvidenceGraphArtifact;
}

export interface DecisionReceiptEvidenceGraphManifest {
  schema: "lians.evidence-graph-manifest.v1";
  decision_id: string;
  snapshot_max_link_sequence: number;
  entries: DecisionReceiptEvidenceGraphEntry[];
  links_total: number;
  artifacts_total: number;
  direct_count: number;
  reachable_count: number;
  complete: true;
  normalization: Record<string, unknown>;
  manifest_hash: string;
}

export interface DecisionReceiptCompletenessCheck {
  id: string;
  label: string;
  weight: number;
  status: "present" | "missing";
  evidence: string;
}

export interface DecisionReceiptCompleteness {
  score: number;
  grade: "A" | "B" | "C" | "D" | "F";
  status: "complete" | "incomplete";
  checks: DecisionReceiptCompletenessCheck[];
  missing: string[];
}

export interface DecisionReceiptSignature {
  algorithm: "ed25519";
  key_id: string;
  public_key: string;
  value: string;
}

export interface DecisionReceiptIntegrity {
  hash_algorithm: "sha-256";
  canonicalization: "json-sort-keys-utf8-v1";
  receipt_hash: string;
  signature: DecisionReceiptSignature | null;
}

export interface DecisionReceipt {
  $schema: "https://lians.ai/specs/decision-receipt/v0.1/schema.json";
  receipt_version: "0.1";
  receipt_id: string;
  issued_at: string;
  issuer: DecisionReceiptIssuer;
  decision: DecisionReceiptDecision;
  actor: DecisionReceiptActor;
  model: DecisionReceiptModel;
  artifacts: DecisionReceiptArtifacts;
  tools: DecisionReceiptTool[];
  sources: DecisionReceiptSource[];
  policy: DecisionReceiptPolicy;
  authorization: DecisionReceiptAuthorization | null;
  human_review: DecisionReceiptHumanReview;
  correlation: DecisionReceiptCorrelation;
  reconstruction: DecisionReceiptReconstruction;
  audit_chain: DecisionReceiptAuditChain;
  completeness: DecisionReceiptCompleteness;
  integrity: DecisionReceiptIntegrity;
}

export interface DecisionReceiptVerifyRequest {
  /** A typed receipt or untrusted parsed JSON to verify on the server. */
  receipt: DecisionReceipt | Record<string, unknown>;
  /** Raw Ed25519 public key encoded as hexadecimal or base64. */
  trusted_public_key?: string;
  require_signature?: boolean;
}

export interface DecisionReceiptVerificationResult {
  valid: boolean;
  hash_valid: boolean;
  signature_present: boolean;
  signature_valid: boolean;
  trusted_key: boolean | null;
  receipt_hash?: string;
  errors: string[];
}

export type DecisionDependencyKind =
  | "source"
  | "policy"
  | "model"
  | "tool"
  | "permission"
  | "instruction"
  | "input"
  | "output";

export type DecisionDependencyChangeType =
  | "changed"
  | "corrected"
  | "retired"
  | "revoked"
  | "recalled"
  | "corrupted"
  | "erased";

export interface DecisionDependencyChange {
  dependency_kind: DecisionDependencyKind;
  dependency_value: string;
  change_type?: DecisionDependencyChangeType;
  occurred_at?: string | null;
  note?: string | null;
  agent_id?: string;
  limit?: number;
  record_event?: boolean;
}

export interface DecisionImpactItem {
  decision: DecisionOut;
  match_basis: string[];
  impact_status: "direct_reference" | "reachable";
  risk_score: number;
  priority: "critical" | "high" | "medium" | "low";
}

export interface DecisionDependency {
  kind: DecisionDependencyKind;
  value: string;
}

export type DecisionImpactAnalysisMode =
  | "indexed"
  | "hybrid_legacy_fallback"
  | "legacy_fallback";

export interface DecisionImpactResult {
  dependency: DecisionDependency;
  change_type: DecisionDependencyChangeType;
  assessed_at: string;
  total: number;
  direct_count: number;
  reachable_count: number;
  search_truncated: boolean;
  change_event_id: string | null;
  items: DecisionImpactItem[];
  analysis_mode: DecisionImpactAnalysisMode;
  indexed_decisions_matched: number;
  legacy_decisions_matched: number;
  legacy_candidates_scanned: number;
  legacy_fallback_truncated: boolean;
  total_is_lower_bound: boolean;
  legacy_fallback_scope: "incomplete_kind_coverage";
}

export interface ExhaustiveImpactAssessmentCreate {
  idempotency_key: string;
  dependency_kind: DecisionDependencyKind;
  dependency_value: string;
  change_type?: DecisionDependencyChangeType;
  occurred_at?: string | null;
  note?: string | null;
  record_event?: boolean;
}

export interface ExhaustiveImpactAssessmentAdvance {
  page_size?: number;
  max_pages?: number;
}

export type ExhaustiveImpactAssessmentState =
  | "pending"
  | "running"
  | "completed"
  | "failed";

export interface ExhaustiveImpactAssessmentStatus {
  id: string;
  namespace: string;
  barrier_group: string | null;
  dependency: DecisionDependency;
  change_type: DecisionDependencyChangeType;
  status: ExhaustiveImpactAssessmentState;
  snapshot_max_coverage_sequence: number;
  snapshot_max_link_sequence: number;
  snapshot_decision_count: number;
  cursor_coverage_sequence: number;
  decisions_scanned: number;
  fallback_candidates_scanned: number;
  indexed_decisions_matched: number;
  legacy_decisions_matched: number;
  matches_found: number;
  direct_count: number;
  reachable_count: number;
  pages_completed: number;
  record_event: boolean;
  completion_event_id: string | null;
  failure_code: string | null;
  created_at: string;
  started_at: string | null;
  updated_at: string;
  completed_at: string | null;
  snapshot_complete: boolean;
  completion_scope: "explicit_registration_sequence_snapshot";
  disclosure: string;
}

export interface ExhaustiveImpactAssessmentMatch {
  sequence: number;
  decision: DecisionOut;
  match_basis: string[];
  impact_status: "direct_reference" | "reachable";
  risk_score: number;
  priority: "critical" | "high" | "medium" | "low";
  match_sources: ("indexed" | "legacy_fallback")[];
}

export interface ExhaustiveImpactAssessmentResults {
  assessment_id: string;
  status: ExhaustiveImpactAssessmentState;
  snapshot_complete: boolean;
  total_matches: number;
  items: ExhaustiveImpactAssessmentMatch[];
  next_cursor: number | null;
}

export interface ExhaustiveImpactAssessmentResultsOptions {
  after?: number;
  limit?: number;
}

// ── Universal Recorder ──────────────────────────────────────────────────────

export type RecorderProtocol = "lians" | "otlp.genai" | "mcp" | "a2a";
export type RecorderCaptureMode = "metadata_only" | "hash_only" | "full";

export interface RecorderActor {
  agent_id?: string;
  principal_id?: string;
  roles?: string[];
  authentication_context?: Record<string, unknown>;
  extensions?: Record<string, unknown>;
}

export interface RecorderCorrelation {
  run_id?: string;
  trace_id?: string;
  span_id?: string;
  parent_span_id?: string;
  session_id?: string;
  task_id?: string;
  context_id?: string;
  message_id?: string;
  tool_call_id?: string;
  decision_id?: string;
  extensions?: Record<string, unknown>;
}

export interface RecorderCapturePolicy {
  /** Hash-only is the SDK and service default. */
  mode?: RecorderCaptureMode;
  sensitive_fields?: string[];
}

export type RecorderMeasurementProvenance =
  | "provider-reported"
  | "workload-reported"
  | "client-measured"
  | "deterministic"
  | "human-authored"
  | "model-judged"
  | "estimated";

export interface RecorderMeasurement {
  value: number;
  provenance: RecorderMeasurementProvenance;
}

export interface RecorderOperational {
  provider?: string;
  runtime_framework?: string;
  operation?: string;
  prompt_hash?: string;
  toolset_hash?: string;
  request_configuration_hash?: string;
  agent_version_id?: string;
  release_reference?: string;
  tokens?: {
    input?: RecorderMeasurement;
    output?: RecorderMeasurement;
    cached?: RecorderMeasurement;
  };
  latency_ms?: RecorderMeasurement;
  finish_reason?: string;
  error_code?: string;
  cost?: {
    amount?: RecorderMeasurement;
    currency?: string;
    attribution?: string;
  };
  outcome_correlation?: string;
}

export interface RecorderEnvelope {
  schema_version?: "0.1" | "0.2";
  protocol: RecorderProtocol;
  event_type?: string;
  event_id?: string;
  idempotency_key?: string;
  occurred_at?: string;
  subject_id?: string;
  actor?: RecorderActor;
  correlation?: RecorderCorrelation;
  capture?: RecorderCapturePolicy;
  operational?: RecorderOperational;
  payload: Record<string, unknown>;
  extensions?: Record<string, unknown>;
}

export interface RecorderEvent {
  id: string;
  run_id: string;
  protocol: RecorderProtocol;
  event_kind: string;
  event_name: string | null;
  phase: string;
  status: string | null;
  occurred_at: string;
  recorded_at: string;
  /** Caller-reported actor fields are labels, never authenticated identity. */
  agent_id: string | null;
  actor_attribution: "claimed_unverified" | "not_supplied";
  /** Canonical server-derived identity of the credential that ingested the event. */
  ingested_by_principal_ref: string;
  ingested_by_auth_method: string;
  /** Opaque credential identifier; this is not credential material. */
  ingested_by_credential_id: string | null;
  trace_id: string | null;
  span_id: string | null;
  task_id: string | null;
  decision_id: string | null;
  model_id: string | null;
  input_hash: string | null;
  output_hash: string | null;
  capture_mode: RecorderCaptureMode;
  capture_gaps: string[];
  diagnostics: Array<Record<string, unknown>>;
  operational: RecorderOperational;
  event_hash: string;
  /** v1 denotes explicitly unverified legacy history; new writes use v2. */
  event_hash_version: 1 | 2;
}

export interface RecorderRunReadiness {
  run_id: string;
  correlation_type: string;
  boundary_kind: "run" | "decision";
  status: string;
  event_count: number;
  protocols: RecorderProtocol[];
  score: number;
  receipt_ready: boolean;
  ready_at: string | null;
  missing_fields: string[];
  diagnostics: Array<Record<string, unknown>>;
  first_event_at: string;
  last_event_at: string;
  time_to_readiness_ms: number | null;
}

export interface RecorderIngestResult {
  accepted: boolean;
  duplicate: boolean;
  event: RecorderEvent;
  readiness: RecorderRunReadiness;
}

export interface RecorderEvidenceIndexJob {
  id: string;
  decision_id: string;
  status: "pending" | "running" | "completed" | "failed";
  snapshot_max_recorded_at: string;
  snapshot_max_event_id: string;
  snapshot_event_count: number;
  cursor_recorded_at: string | null;
  cursor_event_id: string | null;
  events_indexed: number;
  events_remaining: number;
  artifacts_created: number;
  links_created: number;
  pages_completed: number;
  processing_attempts: number;
  progress_ratio: number;
  complete: boolean;
  next_attempt_at: string;
  last_error_code: string | null;
  last_error_digest: string | null;
  failure_code: string | null;
  created_at: string;
  started_at: string | null;
  updated_at: string;
  completed_at: string | null;
  failed_at: string | null;
}

export interface RecorderBatchRejection {
  index: number;
  code: string;
  detail: string;
}

export interface RecorderBatchResult {
  received: number;
  accepted: number;
  duplicates: number;
  rejected: number;
  results: RecorderIngestResult[];
  rejections: RecorderBatchRejection[];
  ready_run_ids: string[];
}

export interface FirstReceiptReadiness {
  namespace: string;
  evaluated_at: string;
  total_runs: number;
  ready_runs: number;
  waiting_runs: number;
  readiness_rate: number;
  first_ready_run_id: string | null;
  first_ready_at: string | null;
  next_actions: string[];
  runs: RecorderRunReadiness[];
}

// ── Runtime Gate and investigations ─────────────────────────────────────────

export type RiskLevel = "low" | "medium" | "high" | "critical";
export type GateDisposition = "allow" | "deny" | "review";
export type ReceiptGrade = "A" | "B" | "C" | "D" | "F";

export interface ReceiptIssuerCreate {
  actor_id?: string;
  name: string;
  issuer_uri?: string;
  description?: string;
  barrier_group?: string | null;
  metadata?: Record<string, unknown>;
}

export interface ReceiptIssuer {
  id: string;
  namespace: string;
  barrier_group: string | null;
  name: string;
  issuer_uri: string | null;
  description: string | null;
  status: "active" | "revoked";
  metadata: Record<string, unknown>;
  created_by: string;
  created_at: string;
  revoked_by: string | null;
  revoked_at: string | null;
  revocation_reason: string | null;
}

export interface TrustedReceiptKeyCreate {
  actor_id?: string;
  key_id: string;
  algorithm?: "ed25519";
  public_key: string;
  valid_from?: string;
  valid_until?: string;
  metadata?: Record<string, unknown>;
}

export interface TrustedReceiptKeyRotate extends TrustedReceiptKeyCreate {
  reason: string;
}

export interface TrustedReceiptKey {
  id: string;
  namespace: string;
  barrier_group: string | null;
  issuer_id: string;
  key_id: string;
  algorithm: "ed25519";
  public_key: string;
  public_key_format: "raw-base64";
  fingerprint_sha256: string;
  status: "active" | "revoked";
  valid_from: string;
  valid_until: string | null;
  created_by: string;
  created_at: string;
  revoked_by: string | null;
  revoked_at: string | null;
  revocation_reason: string | null;
  rotated_at: string | null;
  rotated_from_key_id: string | null;
  replaced_by_key_id: string | null;
  rotation_reason: string | null;
  metadata: Record<string, unknown>;
}

export interface GatePolicyRuleCreate {
  name: string;
  description?: string;
  priority?: number;
  enabled?: boolean;
  action_on_failure?: "deny" | "review";
  applies_to_decision_types?: string[];
  applies_to_risk_levels?: RiskLevel[];
  required_receipt_grade?: ReceiptGrade;
  require_trusted_issuer?: boolean;
  require_sources_current?: boolean;
  require_policy_attached?: boolean;
  required_principal_scopes?: string[];
  minimum_approval_count?: number;
  required_approval_roles?: string[];
  allowed_approval_principal_types?: Array<"human" | "workload" | "api_key">;
  maximum_approval_age_seconds?: number;
  require_information_barrier_match?: boolean;
  block_untrusted_content?: boolean;
  max_untrusted_content_score?: number;
}

export interface GatePolicySetCreate {
  actor_id?: string;
  name: string;
  version: string;
  description?: string;
  barrier_group?: string | null;
  default_disposition?: GateDisposition;
  protected_actions: string[];
  target_ref_prefixes: string[];
  enforcement_principal_ids: string[];
  maximum_permit_ttl_seconds?: number;
  rules: GatePolicyRuleCreate[];
  metadata?: Record<string, unknown>;
}

export interface GatePolicySet {
  id: string;
  namespace: string;
  barrier_group: string | null;
  name: string;
  version: string;
  description: string | null;
  status: "draft" | "active" | "retired";
  default_disposition: GateDisposition;
  protected_actions: string[];
  target_ref_prefixes: string[];
  enforcement_principal_ids: string[];
  maximum_permit_ttl_seconds: number;
  created_by: string;
  created_at: string;
  activated_by: string | null;
  activated_at: string | null;
  retired_at: string | null;
  policy_hash: string;
  metadata: Record<string, unknown>;
  rules?: Array<Record<string, unknown>>;
}

export interface GateReceiptContext {
  grade?: ReceiptGrade;
  receipt_hash?: string;
  issuer_id?: string;
  key_id?: string;
  /** Verified in process. The Gate persists only a digest reference. */
  document?: DecisionReceipt | Record<string, unknown>;
}

export interface GateApproval {
  principal_id: string;
  role: string;
  status?: "approved" | "rejected" | "pending";
  attestation_ref?: string;
  principal_type?: string;
  auth_method?: string;
  attested_at?: string;
}

export interface GateApprovalAttestationCreate {
  action: string;
  decision_id: string;
  change_event_id?: string;
  policy_set_id: string;
  target_ref: string;
  target_barrier_group?: string | null;
  receipt_hash?: string;
  status?: "approved" | "rejected";
  statement?: string;
  evidence_refs?: string[];
  expires_at?: string;
}

export interface GateApprovalAttestationSupersede {
  status: "approved" | "rejected" | "revoked";
  statement?: string;
  evidence_refs?: string[];
  expires_at?: string;
}

export interface GateApprovalAttestation {
  id: string;
  namespace: string;
  barrier_group: string | null;
  series_key: string;
  sequence: number;
  approval_principal_id: string;
  attested_by: string;
  principal_type: string | null;
  attester_role: string;
  auth_method: string;
  credential_id: string | null;
  status: "approved" | "rejected" | "revoked";
  action: string;
  decision_id: string | null;
  change_event_id: string | null;
  policy_set_id: string;
  policy_hash: string;
  target_ref: string | null;
  target_barrier_group: string | null;
  receipt_hash: string | null;
  context_hash: string;
  statement: string | null;
  statement_hash: string | null;
  evidence_refs: string[];
  expires_at: string | null;
  supersedes_id: string | null;
  prior_attestation_hash: string | null;
  attestation_hash: string;
  attested_at: string;
}

export interface UntrustedContentSignal {
  signal_type: string;
  source?: string;
  score: number;
  trusted?: boolean;
  details?: Record<string, unknown>;
}

export interface GateEvaluationRequest {
  action: string;
  target_ref: string;
  decision_id: string;
  enforcement_principal_id: string;
  permit_ttl_seconds: number;
  /** SHA-256 of the mediator's canonical actual provider/tool request. */
  execution_request_hash: string;
  principal_id?: string;
  /** Advanced assertion; normal clients should rely on authenticated identity. */
  principal_scopes?: string[];
  /** Advanced assertion; normal clients should rely on authenticated identity. */
  principal_barrier_group?: string | null;
  target_barrier_group?: string | null;
  decision_type?: string;
  risk_level?: RiskLevel;
  change_event_id?: string;
  policy_set_id?: string;
  policy_name?: string;
  policy_version?: string;
  receipt?: GateReceiptContext;
  sources_current?: boolean;
  attached_policy_version?: string;
  /** IDs of immutable, server-verified approval attestations. */
  approval_ids?: string[];
  untrusted_content_signals?: UntrustedContentSignal[];
  context?: Record<string, unknown>;
}

export interface GateDecision {
  id: string;
  namespace: string;
  barrier_group: string | null;
  policy_set_id: string;
  policy_name: string;
  policy_version: string;
  policy_hash: string;
  principal_id: string;
  action: string;
  target_ref: string;
  enforcement_principal_id: string | null;
  execution_request_hash: string | null;
  decision_id: string | null;
  change_event_id: string | null;
  receipt_hash: string | null;
  disposition: GateDisposition;
  reasons: Array<Record<string, unknown>>;
  applied_rules: Array<Record<string, unknown>>;
  input_snapshot: Record<string, unknown>;
  request_hash: string;
  evaluation_hash: string;
  evaluated_at: string;
}

export interface GateExecutionPermitIssued {
  permit_id: string;
  evaluation_id: string;
  enforcement_principal_id: string;
  action: string;
  target_ref: string;
  decision_id: string;
  execution_request_hash: string;
  issued_at: string;
  expires_at: string;
  /** Returned once. Never log, persist, trace, or put this value in a URL. */
  token: string;
}

export interface GateEvaluationResult extends GateDecision {
  execution_permit: GateExecutionPermitIssued | null;
}

export interface GateExecutionPermitConsume {
  permit_id: string;
  token: string;
  action: string;
  target_ref: string;
  decision_id: string;
  execution_request_hash: string;
}

export interface GateExecutionPermitConsumption {
  id: string;
  namespace: string;
  barrier_group: string | null;
  permit_id: string;
  evaluation_id: string;
  policy_set_id: string;
  decision_id: string;
  consuming_principal_id: string;
  action: string;
  target_ref: string;
  execution_request_hash: string;
  grant_hash: string;
  consumed_at: string;
  consumption_hash: string;
}

export interface InvestigationCaseCreate {
  actor_id?: string;
  title: string;
  description?: string;
  severity?: RiskLevel;
  owner_principal?: string;
  barrier_group?: string | null;
  decision_id?: string;
  change_event_id?: string;
  gate_decision_id?: string;
  metadata?: Record<string, unknown>;
}

export interface InvestigationCaseUpdate {
  /** Exact `updated_at` from the case version being changed. */
  expected_updated_at: string;
  actor_id?: string;
  owner_principal?: string | null;
  status?: "open" | "in_review" | "remediating" | "resolved";
  severity?: RiskLevel;
  resolution_summary?: string | null;
}

export interface InvestigationCase {
  id: string;
  namespace: string;
  barrier_group: string | null;
  title: string;
  description: string | null;
  severity: RiskLevel;
  status: string;
  owner_principal: string | null;
  decision_id: string | null;
  change_event_id: string | null;
  gate_decision_id: string | null;
  opened_by: string;
  opened_at: string;
  updated_at: string;
  closed_at: string | null;
  resolution_summary: string | null;
  metadata: Record<string, unknown>;
}

export interface RemediationTaskCreate {
  /** Exact `updated_at` from the parent case version receiving the task. */
  expected_case_updated_at: string;
  actor_id?: string;
  title: string;
  description?: string;
  owner_principal?: string;
  due_at?: string;
  decision_id?: string;
  change_event_id?: string;
  metadata?: Record<string, unknown>;
}

export interface RemediationTaskUpdate {
  /** Exact `updated_at` from the task version being changed. */
  expected_updated_at: string;
  actor_id?: string;
  owner_principal?: string | null;
  status?: "pending" | "in_progress" | "blocked" | "cancelled";
  due_at?: string | null;
}

export interface RemediationTask {
  id: string;
  namespace: string;
  barrier_group: string | null;
  case_id: string;
  title: string;
  description: string | null;
  status: string;
  owner_principal: string | null;
  due_at: string | null;
  decision_id: string | null;
  change_event_id: string | null;
  created_by: string;
  created_at: string;
  updated_at: string;
  closed_at: string | null;
  metadata: Record<string, unknown>;
}

export interface ClosureAttestationCreate {
  /** Exact `updated_at` from the case or task version being closed. */
  expected_updated_at: string;
  actor_id?: string;
  statement: string;
  evidence_refs: string[];
  resolution_summary?: string;
}

export interface ClosureAttestation {
  id: string;
  namespace: string;
  barrier_group: string | null;
  resource_type: "case" | "task";
  resource_id: string;
  attested_by: string;
  /** Present only on a no-store response requested with `includeStatement`. */
  statement: string | null;
  statement_hash: string;
  hash_version: 1 | 2;
  evidence_refs: string[];
  decision_id: string | null;
  change_event_id: string | null;
  attestation_hash: string;
  attested_at: string;
}

export interface AttestedClosure {
  resource_type: "case" | "task";
  resource_id: string;
  status: "closed";
  attestation: ClosureAttestation;
}

export interface Principal {
  namespace: string;
  scopes: string[];
  barrier_group: string | null;
  principal_id: string | null;
  principal_type: string | null;
  auth_method: string;
  credential_id: string | null;
}

export interface WorkloadCredentialCreate {
  label?: string;
  role?: "owner" | "analyst" | "compliance" | "readonly";
  scopes?: string[];
  barrier_group?: string;
  ttl_seconds: number;
}

export interface WorkloadCredentialRotate {
  expected_version: number;
  ttl_seconds: number;
}

export interface WorkloadCredential {
  id: string;
  namespace: string;
  label: string | null;
  scopes: string[];
  effective_scopes: string[];
  role: "owner" | "analyst" | "compliance" | "readonly" | null;
  barrier_group: string | null;
  provisioning_source: "tenant_oidc";
  created_by: string;
  created_at: string;
  expires_at: string;
  last_used_at: string | null;
  rotated_from_id: string | null;
  rotated_at: string | null;
  revoked_at: string | null;
  version: number;
  status: "active" | "expired" | "revoked" | "rotated";
}

export interface WorkloadCredentialCreated extends WorkloadCredential {
  /** Plaintext secret returned once. Store it directly in a secret manager. */
  secret: string;
}

export type MeteringStatus = "pending" | "leased" | "retry" | "delivered" | "dead_letter";

export interface MeteringInventory {
  delivery_enabled: boolean;
  worker_enabled: boolean;
  provider_configured: boolean;
  async_error_destination_configured: boolean;
  worker_healthy: boolean;
  worker_last_poll_at: string | null;
  worker_last_heartbeat_at: string | null;
  worker_last_delivery_at: string | null;
  worker_last_error_at: string | null;
  worker_last_error_digest: string | null;
  worker_terminal_error: string | null;
  pending_events: number;
  leased_events: number;
  retry_events: number;
  delivered_events: number;
  dead_letter_events: number;
  oldest_due_at: string | null;
}

export interface MeteringEvent {
  id: string;
  namespace: string;
  event_name: string;
  provider_identifier: string;
  quantity: number;
  status: MeteringStatus;
  attempt_count: number;
  attempt_limit: number;
  replay_count: number;
  next_attempt_at: string;
  first_attempt_at: string | null;
  last_attempt_at: string | null;
  delivered_at: string | null;
  dead_lettered_at: string | null;
  last_status_code: number | null;
  last_error_code: string | null;
  last_error_digest: string | null;
  occurred_at: string;
  created_at: string;
  updated_at: string;
}

export interface MeteringReplayRequest {
  reconciliation: "provider_confirmed_not_accepted";
  reconciliation_reference: string;
}

export interface ScimTenantReconciliation {
  id: string;
  tenant_config_id: string;
  namespace: string;
  target_config_version: number;
  target_enabled: boolean;
  target_revoked_at: string | null;
  status: "pending" | "running" | "completed" | "failed" | "superseded";
  snapshot_max_created_at: string | null;
  snapshot_max_user_id: string | null;
  snapshot_user_count: number;
  cursor_created_at: string | null;
  cursor_user_id: string | null;
  users_reconciled: number;
  pages_completed: number;
  processing_attempts: number;
  consecutive_failures: number;
  attempt_limit: number;
  next_attempt_at: string;
  lease_expires_at: string | null;
  heartbeat_at: string | null;
  last_attempt_at: string | null;
  last_error_code: string | null;
  last_error_digest: string | null;
  failure_code: string | null;
  created_at: string;
  started_at: string | null;
  updated_at: string;
  completed_at: string | null;
  failed_at: string | null;
  superseded_at: string | null;
  snapshot_complete: boolean;
  progress_complete: boolean;
  completion_scope: "tenant_user_created_at_id_snapshot";
}

// ── Investigator flagship read model ────────────────────────────────────────

export interface InvestigatorClosure {
  id: string;
  resource_type: "case" | "task";
  resource_id: string;
  attested_by: string;
  statement: string | null;
  statement_sha256: string;
  evidence_refs: string[];
  attestation_hash: string;
  integrity_valid: boolean;
  attested_at: string;
}

export interface InvestigatorCaseBundle {
  case: InvestigationCase;
  tasks: RemediationTask[];
  closures: InvestigatorClosure[];
}

export interface InvestigatorIntegrity {
  audit_chain: Record<string, unknown>;
  review_chain_status: "ok" | "missing" | "tampered" | "partial";
  review_chain_violations: Array<Record<string, unknown>>;
  approval_attestations_status: "valid" | "missing" | "invalid" | "partial";
  approval_attestations_valid: boolean | null;
  invalid_approval_attestation_ids: string[];
}

export interface InvestigatorRiskSummary {
  posture: "defensible" | "needs_attention" | "blocked";
  priority_score: number;
  priority_level: RiskLevel;
  receipt_grade: string;
  receipt_score: number;
  receipt_missing: string[];
  maximum_evidence_risk_score: number | null;
  latest_gate_disposition: string | null;
  gate_disposition_counts: Record<string, number>;
  open_case_count: number;
  overdue_task_count: number;
  blockers: string[];
  attention_signals: string[];
  recommended_actions: string[];
}

export interface InvestigatorLinks {
  decision: string;
  receipt: string;
  evidence_pack: string;
  evidence_graph: string;
  timeline: string;
  review_history: string;
  gate_evaluations: string;
  approval_attestations: string;
  cases: string;
}

export interface InvestigatorCollectionWindow {
  limit: number;
  returned: number;
  total: number;
  total_is_lower_bound: boolean;
  truncated: boolean;
  complete: boolean;
  ordering: string;
  scope: string;
}

export interface InvestigatorReportCoverage {
  complete: boolean;
  audit_scope_complete: boolean;
  receipt_evidence_scope_complete: boolean;
  evidence_links: InvestigatorCollectionWindow;
  evidence_artifacts: InvestigatorCollectionWindow;
  timeline: InvestigatorCollectionWindow;
  gate_evaluations: InvestigatorCollectionWindow;
  approval_attestations: InvestigatorCollectionWindow;
  review_history: InvestigatorCollectionWindow;
  cases: InvestigatorCollectionWindow;
  remediation_tasks: InvestigatorCollectionWindow;
  closure_attestations: InvestigatorCollectionWindow;
}

export interface DecisionInvestigationReport {
  report_version: "1.1";
  generated_at: string;
  decision: DecisionOut;
  risk: InvestigatorRiskSummary;
  receipt_completeness: Record<string, unknown>;
  coverage: InvestigatorReportCoverage;
  evidence_graph: Record<string, unknown>;
  timeline: Array<Record<string, unknown>>;
  gate_evaluations: GateDecision[];
  approval_attestations: GateApprovalAttestation[];
  review_history: Array<Record<string, unknown>>;
  cases: InvestigatorCaseBundle[];
  integrity: InvestigatorIntegrity;
  links: InvestigatorLinks;
  disclosures: string[];
}

export interface InvestigatorQueueItem {
  decision: DecisionOut;
  priority_score: number;
  priority_level: RiskLevel;
  posture: "defensible" | "needs_attention" | "blocked";
  signals: string[];
  latest_gate_disposition: string | null;
  open_case_count: number;
  maximum_evidence_risk_score: number | null;
  review_status: string;
  normalized_evidence_complete: boolean;
}

export interface InvestigatorQueue {
  generated_at: string;
  items: InvestigatorQueueItem[];
  candidates_scanned: number;
  scan_limit: number;
  scan_truncated: boolean;
  total_is_lower_bound: boolean;
}

export interface LiansDiscovery {
  name: "Lians";
  category: "decision_evidence_infrastructure";
  api_version: string;
  decision_receipt_version: string;
  universal_recorder_version: string;
  protocols: string[];
  authentication: string[];
  links: Record<string, string>;
}

export interface PlatformCapabilities {
  generated_at: string;
  namespace: string;
  principal_type: string;
  authentication_method: string;
  information_barrier_scoped: boolean;
  components: Record<string, Record<string, unknown>>;
  standards: Record<string, Record<string, unknown>>;
  privacy: Record<string, unknown>;
  links: Record<string, string>;
}

export interface PlatformReadinessCheck {
  id: string;
  status: "pass" | "warning" | "fail" | "not_configured";
  message: string;
  required_for: string[];
}

export interface PlatformReadiness {
  generated_at: string;
  namespace: string;
  status: "ready" | "degraded" | "configuration_required";
  production_baseline_ready: boolean;
  control_plane_ready: boolean;
  enterprise_identity_ready: boolean;
  checks: PlatformReadinessCheck[];
  inventory: Record<string, number>;
  disclosures: string[];
}

// ── Webhooks ─────────────────────────────────────────────────────────────────

export type WebhookEventType =
  | "memory.superseded"
  | "memory.conflict"
  | "memory.erased"
  | "supersession.rejected";

export interface WebhookEndpoint {
  id: string;
  namespace: string;
  url: string;
  events: WebhookEventType[];
  enabled: boolean;
  description: string | null;
  created_at: string;
  updated_at: string;
}

export interface WebhookRegisterRequest {
  url: string;
  events: WebhookEventType[];
  /** If omitted the server generates a random 32-byte hex secret */
  secret?: string;
  description?: string;
}

export interface WebhookRegisterResult {
  endpoint: WebhookEndpoint;
  /** The HMAC secret — returned ONCE at registration; store it securely */
  secret: string;
}

export interface WebhookUpdateRequest {
  /** Exact `updated_at` from the endpoint version being changed. */
  expected_updated_at: string;
  enabled?: boolean;
  events?: WebhookEventType[];
  description?: string;
}

export interface WebhookDelivery {
  id: string;
  event_type: string;
  attempt: number;
  status_code: number | null;
  error: string | null;
  delivered_at: string | null;
  created_at: string;
}

export interface WebhookDeliveryListResult {
  deliveries: WebhookDelivery[];
  /** Exact number of delivery rows stored for the endpoint. */
  total: number;
  /** Number included in this bounded response. */
  returned: number;
  /** True only when this response contains the complete stored history. */
  complete: boolean;
  /** True when a subsequent keyset page exists. */
  has_more?: boolean;
  /** Supply this value with next_after_id to continue the stable page. */
  next_after_created_at?: string | null;
  /** Supply this value with next_after_created_at to continue the stable page. */
  next_after_id?: string | null;
}

// ── Webhook payload (received by your endpoint) ───────────────────────────────

export interface WebhookPayload<T = Record<string, unknown>> {
  id: string;
  event: WebhookEventType;
  namespace: string;
  timestamp: string;
  data: T;
}

// ── Fact history ─────────────────────────────────────────────────────────────

export interface FactHistoryResult {
  /** Canonical ticker after entity normalization (AAPL, not 'Apple Inc.') */
  ticker: string;
  metric: string;
  agent_id: string;
  namespace: string;
  total: number;
  total_is_lower_bound: boolean;
  has_more: boolean;
  scan_complete: boolean;
  rows_scanned: number;
  scan_limit: number;
  /** Matches found in the bounded scan, ordered oldest-first by event_time. */
  items: MemoryOut[];
}

// ── Knowledge snapshot ───────────────────────────────────────────────────────

/**
 * Exact-count, keyset-paginated knowledge state at a point in time.
 */
export interface KnowledgeSnapshot {
  agent_id: string;
  namespace: string;
  as_of: string;           // ISO 8601
  recorded_as_of: string;  // fixed transaction-time watermark
  total: number;
  returned: number;
  complete: boolean;
  has_more: boolean;
  next_event_time: string | null;
  next_id: string | null;
  items: MemoryOut[];
}

// ── Backtest contamination ────────────────────────────────────────────────────

export interface ContaminationFlag {
  memory_id: string;
  event_time: string;
  ingestion_time: string;
  /** "future_event" = event_time > simulation_as_of; "late_revision" = ingestion_time > simulation_as_of */
  contamination_type: "future_event" | "late_revision";
  /** Days the event/ingestion exceeds the simulation checkpoint */
  delta_days: number;
  content_preview: string | null;
  source: string | null;
  metadata: Record<string, unknown>;
}

export interface ContaminationReport {
  agent_id: string;
  namespace: string;
  simulation_as_of: string;
  memories_checked: number;
  flags_total: number;
  flags_returned: number;
  flags_complete: boolean;
  has_more: boolean;
  next_event_time: string | null;
  next_id: string | null;
  flags: ContaminationFlag[];
  contamination_rate: number;
  /** True only for recorded memories visible inside the authenticated scope. */
  is_clean: boolean;
}

// ── Erasure certificate ───────────────────────────────────────────────────────

export interface ErasureCertificate {
  certificate_id: string;
  job_id: string;
  namespace: string;
  subject_ref: string;
  request_ref: string;
  key_destroyed_at: string;
  completed_at: string;
  memories_erased: number;
  live_facts_erased: number;
  relationships_erased: number;
  pending_admissions_erased: number;
  manifest_sha256: string;
  manifest_algorithm: "lians-subject-erasure-memory-manifest-v1";
  evidence: Array<{ memory_id: string; content_hash: string }>;
  content_hashes: string[];
  hashes_returned: number;
  hashes_total: number;
  hashes_complete: boolean;
  has_more: boolean;
  next_memory_id: string | null;
  audit_event_id: string;
  audit_row_hash: string;
  chain_status: "unchecked";
  generated_at: string;
}

// ── Client options ───────────────────────────────────────────────────────────

export interface LiansClientOptions {
  /** Base URL of the Lians server, e.g. https://lians.example */
  baseUrl: string;
  /** API key (X-API-Key). Supply this or accessToken, never both. */
  apiKey?: string;
  /** OIDC/workload access token (Authorization: Bearer). Never logged by the SDK. */
  accessToken?: string;
  /** Admin secret for privileged endpoints (X-Admin-Secret header) */
  adminSecret?: string;
  /** Request timeout in milliseconds (default: 30000) */
  timeoutMs?: number;
}
