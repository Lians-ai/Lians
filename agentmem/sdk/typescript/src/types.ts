// AgentMem TypeScript SDK — type definitions
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

export interface MemoryRankingStage {
  stage: string;
  input_score: number;
  output_score: number;
  method?: string;
  position?: number;
  candidate_count?: number;
  [key: string]: unknown;
}

export interface MemoryScoreBreakdown {
  importance_score: number;
  confidence_score: number;
  trust_score: number;
  freshness_score: number;
  relevance_score: number;
  stability_score: number;
  safety_score: number;
  final_score: number;
  eligible: boolean;
  safety_eligible?: boolean;
  temporal_eligible?: boolean;
  purpose: "admission" | "recall";
  scoring_policy_version?: string;
  reference_time?: string;
  scoring_limits?: {
    text_sample_chars: number;
    token_cap: number;
    metadata_chars: number;
    metadata_items: number;
    metadata_depth: number;
    metadata_value_chars: number;
  };
  weights: Record<string, number>;
  reasons: string[];
  quality_score?: number;
  pre_fusion_score?: number;
  ranking_weights?: Record<string, number>;
  ranking_stages?: MemoryRankingStage[];
  fusion?: {
    method: string;
    facet_support: number;
    facet_count: number;
    scopes: string[];
    normalized_rrf_score: number;
    strongest_input_score: number;
    strongest_scope?: string;
  };
  [key: string]: unknown;
}

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
  /** Final bounded recall rank. Null/absent on non-recall surfaces. */
  score?: number | null;
  /** Deterministic component scores and ranking provenance for this recall hit. */
  score_breakdown?: MemoryScoreBreakdown | null;
}

// ── Recall ───────────────────────────────────────────────────────────────────

export interface RecallRequest {
  agent_id: string;
  query: string;
  k?: number;
  /** ISO 8601 — point-in-time recall; omit for current valid memories */
  as_of?: string;
  filters?: Record<string, unknown>;
  include_context?: boolean;
  strategy?: "standard" | "adaptive";
  max_query_variants?: number;
  mode?: "fast" | "deep" | "reconstruct";
  decision_envelope_id?: string;
}

export interface RecallResult {
  memories: MemoryOut[];
  as_of: string | null;
  total_candidates: number;
  retrieval_degraded: boolean;
  token_estimate: number;
  strategy: "standard" | "adaptive";
  query_variants: string[];
  retrieval_confidence: number;
  latency_ms: number;
  mode: "fast" | "deep" | "reconstruct";
  latency_budget_ms: number;
  deadline_exceeded: boolean;
  receipt_sha256: string;
  receipt: Record<string, unknown>;
  provenance_coverage: number;
}

export interface ContextRequest {
  agent_id: string;
  query: string;
  k?: number;
  as_of?: string;
  max_tokens?: number;
  header?: string;
  mmr?: boolean;
  surface_conflicts?: boolean;
  max_conflicts?: number;
  strategy?: "standard" | "adaptive";
  max_query_variants?: number;
  mode?: "fast" | "deep" | "reconstruct";
  decision_envelope_id?: string;
}

export interface ContextResult {
  context: string;
  context_text: string;
  memories: MemoryOut[];
  token_estimate: number;
  truncated: boolean;
  retrieval_degraded: boolean;
  strategy: string;
  query_variants: string[];
  retrieval_confidence: number;
  recall_latency_ms: number;
  mode: "fast" | "deep" | "reconstruct";
  receipt_sha256: string;
  receipt: Record<string, unknown>;
  provenance_coverage: number;
  deadline_exceeded: boolean;
  open_conflicts: ConflictFlagOut[];
  open_conflicts_total: number;
  excluded: Array<{ id: string; reason: string }>;
  budget: Record<string, unknown>;
  provenance: Record<string, unknown>;
  learning_applied: boolean;
  ranking_policy: string;
}

// ── Decision evidence ────────────────────────────────────────────────────────

export type CompletenessGrade =
  | "recorded"
  | "reconstructable"
  | "verifiable"
  | "replayable";

export interface CompletenessGap {
  code: string;
  label: string;
  blocks: CompletenessGrade;
  message: string;
}

export interface DecisionCompleteness {
  grade: CompletenessGrade | null;
  next_grade: CompletenessGrade | null;
  score: number;
  profile: string;
  checks: Record<string, boolean>;
  gaps: CompletenessGap[];
  evaluated_at: string;
}

export interface DecisionEnvelopeOpen {
  agent_id: string;
  decision_type: string;
  regime?: string;
  subject_id?: string;
  session_id?: string;
  trace_id?: string;
  run_id?: string;
  knowledge_as_of?: string;
  completeness_profile?: "standard" | "regulated_recordkeeping" | "human_review";
  required_checks?: Partial<Record<CompletenessGrade, string[]>>;
  metadata?: Record<string, unknown>;
}

export interface DecisionEvidenceCreate {
  evidence_type:
    | "memory"
    | "recall_receipt"
    | "otel_trace"
    | "otel_span"
    | "policy_decision"
    | "prompt"
    | "model"
    | "tool_call"
    | "tool_result"
    | "human_review"
    | "input"
    | "output"
    | "outcome"
    | "external";
  role?:
    | "available"
    | "retrieved"
    | "used"
    | "governed"
    | "executed"
    | "reviewed"
    | "produced"
    | "outcome";
  source_id: string;
  source_version?: string;
  artifact_hash?: string;
  occurred_at?: string;
  metadata?: Record<string, unknown>;
}

export interface DecisionEvidenceOut extends Required<
  Omit<DecisionEvidenceCreate, "source_version" | "artifact_hash" | "occurred_at">
> {
  id: string;
  namespace: string;
  envelope_id: string;
  source_version: string | null;
  artifact_hash: string | null;
  occurred_at: string | null;
  created_at: string;
}

export interface DecisionEnvelopeOut extends DecisionEnvelopeOpen {
  id: string;
  namespace: string;
  status: "open" | "sealed" | "abandoned";
  version: number;
  decision_id: string | null;
  created_at: string;
  sealed_at: string | null;
  completeness: DecisionCompleteness;
}

export interface DecisionEnvelopeSeal {
  outcome: string;
  decided_at: string;
  reason_codes?: string[];
  knowledge_as_of?: string;
  model_id?: string;
  model_version?: string;
  model_artifact_hash?: string;
  policy_id?: string;
  policy_version?: string;
  policy_artifact_hash?: string;
  prompt_id?: string;
  prompt_version?: string;
  prompt_artifact_hash?: string;
  runtime_version?: string;
  evidence_memory_ids?: string[];
  input_hash?: string;
  output_hash?: string;
  replay_manifest_hash?: string;
  supersedes_id?: string;
  metadata?: Record<string, unknown>;
}

export interface DecisionDetail {
  decision: Record<string, unknown>;
  completeness: DecisionCompleteness;
  evidence: DecisionEvidenceOut[];
}

export interface BlastRadiusResult {
  schema: string;
  generated_at: string;
  evidence_type: string;
  source_id: string;
  source_version: string | null;
  artifact_hash: string | null;
  impacted_decisions: number;
  impacted_open_envelopes: number;
  matching_links: number;
  decisions: Array<{
    decision: Record<string, unknown>;
    matching_roles: string[];
    matching_link_ids: string[];
    completeness: DecisionCompleteness;
  }>;
}

export interface ExperienceCreate {
  agent_id: string;
  task: string;
  decision: Record<string, unknown>;
  context_memory_ids: string[];
  metadata?: Record<string, unknown>;
}

export interface ExperienceOutcome {
  outcome: Record<string, unknown>;
  reward: number;
  reviewer_feedback?: string;
}

export interface ExperienceOut extends ExperienceCreate {
  id: string;
  namespace: string;
  task_key: string;
  outcome: Record<string, unknown> | null;
  reward: number | null;
  reviewer_feedback: string | null;
  status: "open" | "completed";
  created_at: string;
  completed_at: string | null;
}

export interface ReflectionProposal {
  id: string;
  namespace: string;
  agent_id: string;
  task_key: string;
  content: string;
  supporting_experience_ids: string[];
  confidence: number;
  status: "pending" | "approved" | "rejected";
  reviewer_note: string | null;
  promoted_memory_id: string | null;
  created_at: string;
  reviewed_at: string | null;
}

// ── Batch ────────────────────────────────────────────────────────────────────

export interface MemoryBatchResult {
  added: number;
  memories: MemoryOut[];
}

export interface MessageIngestRequest {
  agent_id: string;
  messages: Array<{
    role: "user" | "assistant" | "system" | "tool";
    content: string;
    event_time?: string;
    metadata?: Record<string, unknown>;
  }>;
  event_time?: string;
  source?: string;
  subject_id?: string;
  metadata?: Record<string, unknown>;
  importance?: number;
  roles?: Array<"user" | "assistant" | "system" | "tool">;
  /** Retain durable user preferences/facts even when `roles` omits user. */
  capture_durable_user_memories?: boolean;
}

// ── Erasure (GDPR Art. 17 / CCPA) ───────────────────────────────────────────

export interface EraseRequest {
  subject_id: string;
  request_ref: string;
}

export interface EraseResult {
  subject_id: string;
  memories_erased: number;
  request_ref: string;
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
}

export interface MemoryLineageResult {
  agent_id: string;
  namespace: string;
  queried_id: string;
  root_id: string;
  tip_id: string;
  depth: number;
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
  confidence_threshold: number;
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
}

export interface AuditExportResult {
  namespace: string;
  from_: string | null;
  to: string | null;
  total_rows: number;
  chain_status: string | null;
  chain_violations: unknown[] | null;
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
  status: "ok" | "tampered" | "unchecked";
  rows_checked: number;
  violations: Record<string, unknown>[];
}

export interface ComplianceErasures {
  total_requests: number;
  total_records_erased: number;
  subject_ids: string[];
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
  total: number;
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
  /** All known versions ordered oldest-first by event_time */
  items: MemoryOut[];
}

// ── Knowledge snapshot ───────────────────────────────────────────────────────

/**
 * Complete knowledge state of an agent at a given point in time.
 * Returned by GET /v1/snapshot — the one-call compliance demo.
 */
export interface KnowledgeSnapshot {
  agent_id: string;
  namespace: string;
  as_of: string;           // ISO 8601
  total: number;
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
  flags: ContaminationFlag[];
  contamination_rate: number;
  /** true = no lookahead bias detected; the backtest is clean */
  is_clean: boolean;
}

// ── Erasure certificate ───────────────────────────────────────────────────────

export interface ErasureCertificate {
  certificate_id: string;
  subject_id: string;
  namespace: string;
  request_ref: string | null;
  erased_at: string;
  memories_erased: number;
  content_hashes: string[];
  chain_status: "ok" | "tampered" | "unchecked";
  generated_at: string;
}

// ── Client options ───────────────────────────────────────────────────────────

export interface LiansClientOptions {
  /** Base URL of the AgentMem server, e.g. https://agentmem.example.com */
  baseUrl: string;
  /** API key (X-API-Key header) */
  apiKey: string;
  /** Admin secret for privileged endpoints (X-Admin-Secret header) */
  adminSecret?: string;
  /** Request timeout in milliseconds (default: 30000) */
  timeoutMs?: number;
}
