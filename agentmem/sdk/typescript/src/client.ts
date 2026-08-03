/**
 * Lians TypeScript SDK — async HTTP client for the REST API.
 *
 * Lians is a financial-grade AI evidence and memory layer that provides:
 *  - Compliance-grade recall: bitemporal model with SEC 17a-4 hash chain, GDPR
 *    crypto-shred (audit hash survives), and PostgreSQL RLS information barriers.
 *    mem0 has no temporal model. Graphiti/Zep has temporal graph queries but no
 *    compliance stack (no hash chain, no crypto-shred, no information barriers).
 *  - Automatic supersession: the LLM engine detects when a new fact replaces an
 *    old one and invalidates the stale record, so recall always returns the
 *    current truth without your agent needing to deduplicate.
 *  - Crypto-shred erasure: GDPR Art. 17 / CCPA right-to-erasure via per-subject
 *    DEK destruction.  The audit trail is preserved as content hashes so the
 *    erasure itself is provable.
 *  - Tamper-evident hash chain: every audit event is linked in a SHA-256 chain
 *    that can be verified at any time with verifyChain().
 *
 * @example
 * const client = new LiansClient({
 *   baseUrl: "https://mem.yourfirm.internal",
 *   apiKey: process.env.LIANS_API_KEY!,
 *   adminSecret: process.env.LIANS_ADMIN_SECRET,
 * });
 * const result = await client.recall({ agent_id: "equity-desk", query: "AAPL price target" });
 */

import type {
  LiansClientOptions,
  MemoryAdd,
  MemoryOut,
  MemoryBatchResult,
  RecallRequest,
  RecallResult,
  EraseRequest,
  EraseResult,
  ErasureCertificate,
  MemoryLineageResult,
  ConflictListResult,
  ConflictResolveRequest,
  ConflictResolveResult,
  SupersessionReviewResult,
  SupersessionActionRequest,
  SupersessionActionResult,
  AuditChainVerifyResult,
  AuditExportResult,
  WebhookEndpoint,
  WebhookRegisterRequest,
  WebhookRegisterResult,
  WebhookUpdateRequest,
  WebhookDeliveryListResult,
  ComplianceReport,
  CompatibilityListPage,
  FactHistoryResult,
  KnowledgeSnapshot,
  ContaminationReport,
  DecisionCreate,
  DecisionEvidenceGraphResult,
  DecisionOut,
  DecisionReviewHistoryResult,
  DecisionReceipt,
  DecisionReceiptVerifyRequest,
  DecisionReceiptVerificationResult,
  DecisionDependencyChange,
  DecisionImpactResult,
  ExhaustiveImpactAssessmentAdvance,
  ExhaustiveImpactAssessmentCreate,
  ExhaustiveImpactAssessmentResults,
  ExhaustiveImpactAssessmentResultsOptions,
  ExhaustiveImpactAssessmentStatus,
  EvidenceArtifactOut,
  AttestedClosure,
  ClosureAttestation,
  ClosureAttestationCreate,
  FirstReceiptReadiness,
  GateDecision,
  GateEvaluationRequest,
  GateEvaluationResult,
  GateExecutionPermitConsume,
  GateExecutionPermitConsumption,
  GateApprovalAttestation,
  GateApprovalAttestationCreate,
  GateApprovalAttestationSupersede,
  GatePolicySet,
  GatePolicySetCreate,
  InvestigationCase,
  InvestigationCaseCreate,
  InvestigationCaseUpdate,
  DecisionInvestigationReport,
  InvestigatorQueue,
  LiansDiscovery,
  LedgerEventOut,
  MeteringEvent,
  MeteringInventory,
  MeteringReplayRequest,
  MeteringStatus,
  PlatformCapabilities,
  PlatformReadiness,
  Principal,
  RecorderBatchResult,
  RecorderEnvelope,
  RecorderEvent,
  RecorderEvidenceIndexJob,
  RecorderIngestResult,
  RecorderRunReadiness,
  ScimTenantReconciliation,
  ReceiptIssuer,
  ReceiptIssuerCreate,
  RemediationTask,
  RemediationTaskCreate,
  RemediationTaskUpdate,
  TrustedReceiptKey,
  TrustedReceiptKeyCreate,
  TrustedReceiptKeyRotate,
  WorkloadCredential,
  WorkloadCredentialCreate,
  WorkloadCredentialCreated,
  WorkloadCredentialRotate,
} from "./types.js";

/** Runtime version of this SDK, kept in lock-step with package metadata. */
export const VERSION = "0.5.0";
export const USER_AGENT = `lians-typescript-sdk/${VERSION}`;

// ── Error class ───────────────────────────────────────────────────────────────

/** Thrown by LiansClient when the server returns a non-2xx response. */
export class LiansError extends Error {
  constructor(
    public readonly status: number,
    public readonly body: string,
    message: string,
    /** Parsed Retry-After delay; numeric only, with no retained header value. */
    public readonly retryAfterMs?: number,
  ) {
    super(message);
    this.name = "LiansError";
  }
}

// ── Internal request options ──────────────────────────────────────────────────

interface ReqOpts {
  json?: unknown;
  params?: Record<string, string | number | boolean | undefined | null>;
  admin?: boolean;
  headers?: Record<string, string>;
  pageCursorNames?: readonly [string, string];
}

function requiredPageHeader(response: Response, name: string): string {
  const value = response.headers.get(name);
  if (value === null) {
    throw new Error(`Lians pagination response is missing required header ${name}`);
  }
  return value;
}

function pageInteger(response: Response, name: string): number {
  const raw = requiredPageHeader(response, name);
  if (!/^\d+$/.test(raw)) {
    throw new Error(`Lians pagination header ${name} is not a non-negative integer`);
  }
  const value = Number(raw);
  if (!Number.isSafeInteger(value)) {
    throw new Error(`Lians pagination header ${name} exceeds the safe integer range`);
  }
  return value;
}

function pageBoolean(response: Response, name: string): boolean {
  const raw = requiredPageHeader(response, name).toLowerCase();
  if (raw !== "true" && raw !== "false") {
    throw new Error(`Lians pagination header ${name} is not a boolean`);
  }
  return raw === "true";
}

function compatibilityListPage<T>(
  response: Response,
  payload: unknown,
  cursorNames: readonly [string, string],
): CompatibilityListPage<T> {
  if (!Array.isArray(payload)) {
    throw new Error("Lians pagination response body must be a JSON array");
  }
  const total = pageInteger(response, "X-Lians-Total-Count");
  const limit = pageInteger(response, "X-Lians-Page-Limit");
  const returned = pageInteger(response, "X-Lians-Page-Returned");
  const hasMore = pageBoolean(response, "X-Lians-Has-More");
  const pageComplete = pageBoolean(response, "X-Lians-Page-Complete");
  const collectionComplete = pageBoolean(response, "X-Lians-Collection-Complete");
  if (
    returned !== payload.length ||
    returned > limit ||
    total < returned ||
    pageComplete === hasMore ||
    (collectionComplete && hasMore)
  ) {
    throw new Error("Lians pagination response headers are inconsistent");
  }

  let nextCursor: Record<string, string> | null = null;
  if (hasMore) {
    nextCursor = {};
    for (const name of cursorNames) {
      const suffix = name
        .split("_")
        .map((part) => part.charAt(0).toUpperCase() + part.slice(1))
        .join("-");
      nextCursor[name] = requiredPageHeader(response, `X-Lians-Next-${suffix}`);
    }
  }
  return {
    items: payload as T[],
    total,
    limit,
    returned,
    has_more: hasMore,
    page_complete: pageComplete,
    collection_complete: collectionComplete,
    next_cursor: nextCursor,
  };
}

// ── Client ────────────────────────────────────────────────────────────────────

export class LiansClient {
  private readonly baseUrl: string;
  private readonly apiKey: string | undefined;
  private readonly accessToken: string | undefined;
  private readonly adminSecret: string | undefined;
  private readonly timeoutMs: number;

  constructor(options: LiansClientOptions) {
    if (options.apiKey && options.accessToken) {
      throw new Error("Supply apiKey or accessToken, not both");
    }
    this.baseUrl = options.baseUrl.replace(/\/$/, "");
    this.apiKey = options.apiKey;
    this.accessToken = options.accessToken;
    this.adminSecret = options.adminSecret;
    this.timeoutMs = options.timeoutMs ?? 30_000;
  }

  // ── Internal ──────────────────────────────────────────────────────────────

  private async _req<T>(
    method: string,
    path: string,
    opts: ReqOpts = {},
  ): Promise<T> {
    const { json, params, admin, headers: extraHeaders, pageCursorNames } = opts;

    // Build URL with query parameters
    let url = `${this.baseUrl}${path}`;
    if (params) {
      const qs = new URLSearchParams();
      for (const [k, v] of Object.entries(params)) {
        if (v !== undefined && v !== null) qs.set(k, String(v));
      }
      const s = qs.toString();
      if (s) url += `?${s}`;
    }

    // Build headers
    const headers: Record<string, string> = {
      "Content-Type": "application/json",
      "User-Agent": USER_AGENT,
    };
    if (this.apiKey) headers["X-API-Key"] = this.apiKey;
    if (this.accessToken) headers.Authorization = `Bearer ${this.accessToken}`;
    if (admin && this.adminSecret) {
      headers["X-Admin-Secret"] = this.adminSecret;
    }
    if (extraHeaders) Object.assign(headers, extraHeaders);

    // Timeout via AbortController
    const controller = new AbortController();
    const timer = setTimeout(() => controller.abort(), this.timeoutMs);

    let res: Response;
    try {
      res = await fetch(url, {
        method,
        headers,
        body: json !== undefined ? JSON.stringify(json) : undefined,
        signal: controller.signal,
      });
    } finally {
      clearTimeout(timer);
    }

    if (!res.ok) {
      const body = await res.text().catch(() => res.statusText);
      throw new LiansError(
        res.status,
        body,
        `Lians ${method} ${path} → ${res.status}: ${body}`,
        responseRetryAfterMs(res),
      );
    }

    if (res.status === 204) return undefined as unknown as T;
    const payload: unknown = await res.json();
    if (pageCursorNames) {
      return compatibilityListPage<unknown>(res, payload, pageCursorNames) as T;
    }
    return payload as T;
  }

  // ── Write ─────────────────────────────────────────────────────────────────

  /** Store a financial fact, observation, or decision with its event timestamp. */
  addMemory(req: MemoryAdd, opts: { idempotencyKey?: string } = {}): Promise<MemoryOut> {
    return this._req<MemoryOut>("POST", "/v1/memories", {
      json: req,
      headers: opts.idempotencyKey ? { "Idempotency-Key": opts.idempotencyKey } : undefined,
    });
  }

  /**
   * Add multiple memories in a single request.
   * Items are processed sequentially, so a later item can supersede an earlier
   * one within the same batch (useful when loading a time-series of revisions).
   */
  batchAdd(
    memories: MemoryAdd[],
    opts: { idempotencyKey?: string } = {},
  ): Promise<MemoryBatchResult> {
    return this._req<MemoryBatchResult>("POST", "/v1/memories/batch", {
      json: { memories },
      headers: opts.idempotencyKey ? { "Idempotency-Key": opts.idempotencyKey } : undefined,
    });
  }

  // ── Read ──────────────────────────────────────────────────────────────────

  /**
   * Retrieve the most relevant current memories for a query.
   * Superseded facts are excluded at the database level. Pass `as_of` for
   * point-in-time recall backed by a compliance audit stack (hash chain,
   * crypto-shred, RLS information barriers) absent from mem0 and Graphiti/Zep.
   */
  recall(req: RecallRequest): Promise<RecallResult> {
    return this._req<RecallResult>("POST", "/v1/recall", { json: req });
  }

  /**
   * Return a bounded supersession graph for a memory.
   * Useful for audit and human review: follow explicit edges and retain the
   * completeness and immutable audit-binding fields with any conclusion.
   */
  getLineage(memoryId: string, maxNodes = 1000): Promise<MemoryLineageResult> {
    return this._req<MemoryLineageResult>("GET", `/v1/memories/${memoryId}/lineage`, {
      params: { max_nodes: maxNodes },
    });
  }

  // ── Decision evidence ────────────────────────────────────────────────────

  /**
   * Record a consequential decision and its explicit evidence boundary.
   *
   * `knowledge_as_of` is the business/event-time cutoff. The independent
   * `knowledge_recorded_as_of` cutoff prevents later-ingested, backdated
   * evidence from silently changing the reconstructed decision boundary.
   */
  recordDecision(
    req: DecisionCreate,
    opts: { idempotencyKey?: string } = {},
  ): Promise<DecisionOut> {
    return this._req<DecisionOut>("POST", "/v1/decisions", {
      json: req,
      headers: opts.idempotencyKey ? { "Idempotency-Key": opts.idempotencyKey } : undefined,
    });
  }

  /** List decisions with exact totals and a stable timestamp/UUID continuation. */
  listDecisionsPage(
    opts: {
      agentId?: string;
      subjectId?: string;
      regime?: string;
      limit?: number;
      beforeDecidedAt?: string;
      beforeId?: string;
    } = {},
  ): Promise<CompatibilityListPage<DecisionOut>> {
    return this._req<CompatibilityListPage<DecisionOut>>("GET", "/v1/decisions", {
      params: {
        agent_id: opts.agentId,
        subject_id: opts.subjectId,
        regime: opts.regime,
        limit: opts.limit,
        before_decided_at: opts.beforeDecidedAt,
        before_id: opts.beforeId,
      },
      pageCursorNames: ["before_decided_at", "before_id"],
    });
  }

  /** List system-of-record events with exact totals and paired continuation. */
  listLedgerEventsPage(
    opts: {
      eventType?: string;
      agentId?: string;
      decisionId?: string;
      limit?: number;
      beforeOccurredAt?: string;
      beforeId?: string;
    } = {},
  ): Promise<CompatibilityListPage<LedgerEventOut>> {
    return this._req<CompatibilityListPage<LedgerEventOut>>(
      "GET",
      "/v1/records/events",
      {
        params: {
          event_type: opts.eventType,
          agent_id: opts.agentId,
          decision_id: opts.decisionId,
          limit: opts.limit,
          before_occurred_at: opts.beforeOccurredAt,
          before_id: opts.beforeId,
        },
        pageCursorNames: ["before_occurred_at", "before_id"],
      },
    );
  }

  /** List evidence artifacts with exact totals and paired continuation. */
  listEvidenceArtifactsPage(
    opts: {
      kind?: EvidenceArtifactOut["kind"];
      identifier?: string;
      version?: string;
      coordinate?: string;
      artifactHash?: string;
      limit?: number;
      beforeRecordedAt?: string;
      beforeId?: string;
    } = {},
  ): Promise<CompatibilityListPage<EvidenceArtifactOut>> {
    return this._req<CompatibilityListPage<EvidenceArtifactOut>>(
      "GET",
      "/v1/decisions/evidence/artifacts",
      {
        params: {
          kind: opts.kind,
          identifier: opts.identifier,
          version: opts.version,
          coordinate: opts.coordinate,
          artifact_hash: opts.artifactHash,
          limit: opts.limit,
          before_recorded_at: opts.beforeRecordedAt,
          before_id: opts.beforeId,
        },
        pageCursorNames: ["before_recorded_at", "before_id"],
      },
    );
  }

  /** Export a portable, completeness-scored Decision Receipt v0.1. */
  decisionReceipt(
    decisionId: string,
    opts: { verify?: boolean; includeSourceContent?: boolean } = {},
  ): Promise<DecisionReceipt> {
    return this._req<DecisionReceipt>("GET", `/v1/decisions/${decisionId}/receipt`, {
      params: {
        verify: opts.verify,
        include_source_content: opts.includeSourceContent,
      },
    });
  }

  /** Read one bounded evidence-link page with exact graph cardinalities. */
  decisionEvidenceGraph(
    decisionId: string,
    opts: {
      limit?: number;
      after_relation?: "direct" | "reachable";
      after_link_id?: string;
    } = {},
  ): Promise<DecisionEvidenceGraphResult> {
    return this._req<DecisionEvidenceGraphResult>(
      "GET",
      `/v1/decisions/${decisionId}/evidence-graph`,
      {
        params: {
          limit: opts.limit,
          after_relation: opts.after_relation,
          after_link_id: opts.after_link_id,
        },
      },
    );
  }

  /** Read one internally verified immutable review-chain page. */
  decisionReviewHistory(
    decisionId: string,
    opts: { include_notes?: boolean; after_sequence?: number; limit?: number } = {},
  ): Promise<DecisionReviewHistoryResult> {
    return this._req<DecisionReviewHistoryResult>(
      "GET",
      `/v1/decisions/${decisionId}/review-history`,
      {
        params: {
          include_notes: opts.include_notes,
          after_sequence: opts.after_sequence,
          limit: opts.limit,
        },
      },
    );
  }

  /** Verify a Decision Receipt digest and optional Ed25519 signature. */
  verifyDecisionReceipt(
    req: DecisionReceiptVerifyRequest,
  ): Promise<DecisionReceiptVerificationResult> {
    return this._req<DecisionReceiptVerificationResult>("POST", "/v1/receipts/verify", {
      json: req,
    });
  }

  /**
   * Find decisions that directly or transitively reference a changed evidence
   * dependency, including sources, policies, models, tools, and I/O artifacts.
   */
  assessDecisionImpact(req: DecisionDependencyChange): Promise<DecisionImpactResult> {
    return this._req<DecisionImpactResult>("POST", "/v1/decisions/impact", { json: req });
  }

  /** Freeze a decision/evidence snapshot for exhaustive impact analysis. */
  startExhaustiveImpactAssessment(
    req: ExhaustiveImpactAssessmentCreate,
  ): Promise<ExhaustiveImpactAssessmentStatus> {
    return this._req<ExhaustiveImpactAssessmentStatus>(
      "POST",
      "/v1/decisions/impact-assessments",
      { json: req },
    );
  }

  /** Read durable progress for an exhaustive impact assessment. */
  getExhaustiveImpactAssessment(
    assessmentId: string,
  ): Promise<ExhaustiveImpactAssessmentStatus> {
    return this._req<ExhaustiveImpactAssessmentStatus>(
      "GET",
      `/v1/decisions/impact-assessments/${assessmentId}`,
    );
  }

  /** Resume a durable assessment by a bounded number of keyset pages. */
  advanceExhaustiveImpactAssessment(
    assessmentId: string,
    req: ExhaustiveImpactAssessmentAdvance = {},
  ): Promise<ExhaustiveImpactAssessmentStatus> {
    return this._req<ExhaustiveImpactAssessmentStatus>(
      "POST",
      `/v1/decisions/impact-assessments/${assessmentId}/advance`,
      { json: req },
    );
  }

  /** Read a keyset-paginated page of persisted assessment matches. */
  listExhaustiveImpactAssessmentResults(
    assessmentId: string,
    options: ExhaustiveImpactAssessmentResultsOptions = {},
  ): Promise<ExhaustiveImpactAssessmentResults> {
    return this._req<ExhaustiveImpactAssessmentResults>(
      "GET",
      `/v1/decisions/impact-assessments/${assessmentId}/results`,
      { params: { after: options.after, limit: options.limit } },
    );
  }

  // ── Universal Recorder ──────────────────────────────────────────────────

  /** Ingest one native Lians, OTLP GenAI, MCP, or A2A event. */
  ingestRecorderEvent(
    event: RecorderEnvelope,
    opts: { idempotencyKey?: string } = {},
  ): Promise<RecorderIngestResult> {
    return this._req<RecorderIngestResult>("POST", "/v1/recorder/events", {
      json: opts.idempotencyKey
        ? { ...event, idempotency_key: opts.idempotencyKey }
        : event,
    });
  }

  /** Ingest up to 500 mixed-protocol events in one transaction. */
  ingestRecorderBatch(
    events: RecorderEnvelope[],
    opts: { atomic?: boolean } = {},
  ): Promise<RecorderBatchResult> {
    return this._req<RecorderBatchResult>("POST", "/v1/recorder/batch", {
      json: { events, atomic: opts.atomic ?? true },
    });
  }

  recorderRunReadiness(runId: string): Promise<RecorderRunReadiness> {
    return this._req<RecorderRunReadiness>(
      "GET",
      `/v1/recorder/runs/${runId}/readiness`,
    );
  }

  recorderRunEvents(runId: string, limit = 500): Promise<RecorderEvent[]> {
    return this._req<RecorderEvent[]>("GET", `/v1/recorder/runs/${runId}/events`, {
      params: { limit },
    });
  }

  /** Traverse a run in immutable ingestion order with exact cardinality. */
  recorderRunEventsPage(
    runId: string,
    opts: {
      limit?: number;
      beforeRecordedAt?: string;
      beforeId?: string;
    } = {},
  ): Promise<CompatibilityListPage<RecorderEvent>> {
    return this._req<CompatibilityListPage<RecorderEvent>>(
      "GET",
      `/v1/recorder/runs/${runId}/events`,
      {
        params: {
          limit: opts.limit,
          before_recorded_at: opts.beforeRecordedAt,
          before_id: opts.beforeId,
        },
        pageCursorNames: ["before_recorded_at", "before_id"],
      },
    );
  }

  recorderReadiness(
    opts: { agentId?: string; limit?: number } = {},
  ): Promise<FirstReceiptReadiness> {
    return this._req<FirstReceiptReadiness>("GET", "/v1/recorder/readiness", {
      params: { agent_id: opts.agentId, limit: opts.limit },
    });
  }

  recorderEvidenceIndexJob(jobId: string): Promise<RecorderEvidenceIndexJob> {
    return this._req<RecorderEvidenceIndexJob>(
      "GET",
      `/v1/recorder/indexing/jobs/${jobId}`,
    );
  }

  recorderEvidenceIndexJobForDecision(
    decisionId: string,
  ): Promise<RecorderEvidenceIndexJob> {
    return this._req<RecorderEvidenceIndexJob>(
      "GET",
      `/v1/recorder/indexing/decisions/${decisionId}`,
    );
  }

  retryRecorderEvidenceIndexJob(jobId: string): Promise<RecorderEvidenceIndexJob> {
    return this._req<RecorderEvidenceIndexJob>(
      "POST",
      `/v1/recorder/indexing/jobs/${jobId}/retry`,
    );
  }

  // ── Runtime Gate and investigations ─────────────────────────────────────

  createReceiptIssuer(issuer: ReceiptIssuerCreate): Promise<ReceiptIssuer> {
    return this._req<ReceiptIssuer>("POST", "/v1/control/trust/issuers", {
      json: issuer,
    });
  }

  receiptIssuers(
    includeRevoked = false,
    page: { offset?: number; limit?: number } = {},
  ): Promise<ReceiptIssuer[]> {
    return this._req<ReceiptIssuer[]>("GET", "/v1/control/trust/issuers", {
      params: {
        include_revoked: includeRevoked,
        offset: page.offset,
        limit: page.limit,
      },
    });
  }

  revokeReceiptIssuer(
    issuerId: string,
    reason: string,
    actorId?: string,
  ): Promise<ReceiptIssuer> {
    return this._req<ReceiptIssuer>(
      "POST",
      `/v1/control/trust/issuers/${issuerId}/revoke`,
      { json: { reason, actor_id: actorId } },
    );
  }

  registerTrustedReceiptKey(
    issuerId: string,
    key: TrustedReceiptKeyCreate,
  ): Promise<TrustedReceiptKey> {
    return this._req<TrustedReceiptKey>(
      "POST",
      `/v1/control/trust/issuers/${issuerId}/keys`,
      { json: key },
    );
  }

  trustedReceiptKeys(
    issuerId: string,
    includeRevoked = false,
    page: { offset?: number; limit?: number } = {},
  ): Promise<TrustedReceiptKey[]> {
    return this._req<TrustedReceiptKey[]>(
      "GET",
      `/v1/control/trust/issuers/${issuerId}/keys`,
      {
        params: {
          include_revoked: includeRevoked,
          offset: page.offset,
          limit: page.limit,
        },
      },
    );
  }

  resolveTrustedReceiptKey(keyId: string, at?: string): Promise<TrustedReceiptKey> {
    return this._req<TrustedReceiptKey>("GET", `/v1/control/trust/keys/${keyId}`, {
      params: { at },
    });
  }

  rotateTrustedReceiptKey(
    issuerId: string,
    keyId: string,
    replacement: TrustedReceiptKeyRotate,
  ): Promise<TrustedReceiptKey> {
    return this._req<TrustedReceiptKey>(
      "POST",
      `/v1/control/trust/issuers/${issuerId}/keys/${keyId}/rotate`,
      { json: replacement },
    );
  }

  revokeTrustedReceiptKey(
    issuerId: string,
    keyId: string,
    reason: string,
    actorId?: string,
  ): Promise<TrustedReceiptKey> {
    return this._req<TrustedReceiptKey>(
      "POST",
      `/v1/control/trust/issuers/${issuerId}/keys/${keyId}/revoke`,
      { json: { reason, actor_id: actorId } },
    );
  }

  createGatePolicy(policy: GatePolicySetCreate): Promise<GatePolicySet> {
    return this._req<GatePolicySet>("POST", "/v1/control/gate/policies", {
      json: policy,
    });
  }

  gatePolicies(
    opts: {
      name?: string;
      status?: string;
      include_rules?: boolean;
      offset?: number;
      limit?: number;
    } = {},
  ): Promise<GatePolicySet[]> {
    return this._req<GatePolicySet[]>("GET", "/v1/control/gate/policies", {
      params: opts,
    });
  }

  gatePolicy(policyId: string): Promise<GatePolicySet> {
    return this._req<GatePolicySet>("GET", `/v1/control/gate/policies/${policyId}`);
  }

  activateGatePolicy(policyId: string, actorId?: string): Promise<GatePolicySet> {
    return this._req<GatePolicySet>(
      "POST",
      `/v1/control/gate/policies/${policyId}/activate`,
      { json: { actor_id: actorId } },
    );
  }

  /** Append a role-bound approval for one exact Gate boundary. */
  createGateApproval(
    attestation: GateApprovalAttestationCreate,
  ): Promise<GateApprovalAttestation> {
    return this._req<GateApprovalAttestation>("POST", "/v1/control/gate/approvals", {
      json: attestation,
    });
  }

  supersedeGateApproval(
    approvalId: string,
    successor: GateApprovalAttestationSupersede,
  ): Promise<GateApprovalAttestation> {
    return this._req<GateApprovalAttestation>(
      "POST",
      `/v1/control/gate/approvals/${approvalId}/supersede`,
      { json: successor },
    );
  }

  gateApprovals(
    opts: {
      contextHash?: string;
      decisionId?: string;
      status?: GateApprovalAttestation["status"];
      onlyCurrent?: boolean;
      includeStatement?: boolean;
      limit?: number;
    } = {},
  ): Promise<GateApprovalAttestation[]> {
    return this._req<GateApprovalAttestation[]>("GET", "/v1/control/gate/approvals", {
      params: {
        context_hash: opts.contextHash,
        decision_id: opts.decisionId,
        status: opts.status,
        only_current: opts.onlyCurrent,
        include_statement: opts.includeStatement,
        limit: opts.limit,
      },
    });
  }

  gateApproval(
    approvalId: string,
    opts: { includeStatement?: boolean } = {},
  ): Promise<GateApprovalAttestation> {
    return this._req<GateApprovalAttestation>(
      "GET",
      `/v1/control/gate/approvals/${approvalId}`,
      { params: { include_statement: opts.includeStatement } },
    );
  }

  /**
   * Evaluate an action. Omit principal scopes and barriers in normal use: the
   * service derives them from the authenticated API key or workload token.
   * Receipt documents are verified in process and only their hash is stored.
   */
  evaluateGate(request: GateEvaluationRequest): Promise<GateEvaluationResult> {
    return this._req<GateEvaluationResult>("POST", "/v1/control/gate/evaluate", {
      json: request,
    });
  }

  /** Redeem a one-time permit as its exact policy-authorized mediator. */
  consumeGateExecutionPermit(
    request: GateExecutionPermitConsume,
  ): Promise<GateExecutionPermitConsumption> {
    return this._req<GateExecutionPermitConsumption>(
      "POST",
      "/v1/control/gate/permits/consume",
      { json: request },
    );
  }

  gateEvaluations(
    opts: { disposition?: GateDecision["disposition"]; decisionId?: string; limit?: number } = {},
  ): Promise<GateDecision[]> {
    return this._req<GateDecision[]>("GET", "/v1/control/gate/evaluations", {
      params: {
        disposition: opts.disposition,
        decision_id: opts.decisionId,
        limit: opts.limit,
      },
    });
  }

  gateEvaluation(evaluationId: string): Promise<GateDecision> {
    return this._req<GateDecision>(
      "GET",
      `/v1/control/gate/evaluations/${evaluationId}`,
    );
  }

  createInvestigationCase(request: InvestigationCaseCreate): Promise<InvestigationCase> {
    return this._req<InvestigationCase>("POST", "/v1/control/investigations/cases", {
      json: request,
    });
  }

  investigationCases(
    opts: {
      status?: string;
      ownerPrincipal?: string;
      decisionId?: string;
      limit?: number;
    } = {},
  ): Promise<InvestigationCase[]> {
    return this._req<InvestigationCase[]>("GET", "/v1/control/investigations/cases", {
      params: {
        status: opts.status,
        owner_principal: opts.ownerPrincipal,
        decision_id: opts.decisionId,
        limit: opts.limit,
      },
    });
  }

  investigationCase(caseId: string): Promise<InvestigationCase> {
    return this._req<InvestigationCase>(
      "GET",
      `/v1/control/investigations/cases/${caseId}`,
    );
  }

  updateInvestigationCase(
    caseId: string,
    request: InvestigationCaseUpdate,
  ): Promise<InvestigationCase> {
    return this._req<InvestigationCase>(
      "PATCH",
      `/v1/control/investigations/cases/${caseId}`,
      { json: request },
    );
  }

  createRemediationTask(
    caseId: string,
    request: RemediationTaskCreate,
  ): Promise<RemediationTask> {
    return this._req<RemediationTask>(
      "POST",
      `/v1/control/investigations/cases/${caseId}/tasks`,
      { json: request },
    );
  }

  remediationTasks(
    caseId: string,
    status?: string,
    page: { offset?: number; limit?: number } = {},
  ): Promise<RemediationTask[]> {
    return this._req<RemediationTask[]>(
      "GET",
      `/v1/control/investigations/cases/${caseId}/tasks`,
      { params: { status, offset: page.offset, limit: page.limit } },
    );
  }

  updateRemediationTask(
    taskId: string,
    request: RemediationTaskUpdate,
  ): Promise<RemediationTask> {
    return this._req<RemediationTask>(
      "PATCH",
      `/v1/control/investigations/tasks/${taskId}`,
      { json: request },
    );
  }

  closeRemediationTask(
    taskId: string,
    attestation: ClosureAttestationCreate,
  ): Promise<AttestedClosure> {
    return this._req<AttestedClosure>(
      "POST",
      `/v1/control/investigations/tasks/${taskId}/close`,
      { json: attestation },
    );
  }

  closeInvestigationCase(
    caseId: string,
    attestation: ClosureAttestationCreate,
  ): Promise<AttestedClosure> {
    return this._req<AttestedClosure>(
      "POST",
      `/v1/control/investigations/cases/${caseId}/close`,
      { json: attestation },
    );
  }

  closureAttestation(
    resourceType: "case" | "task",
    resourceId: string,
    opts: { includeStatement?: boolean } = {},
  ): Promise<ClosureAttestation> {
    return this._req<ClosureAttestation>(
      "GET",
      `/v1/control/investigations/${resourceType}/${resourceId}/attestation`,
      { params: { include_statement: opts.includeStatement } },
    );
  }

  /** Inspect the principal resolved from normal API-key or bearer authentication. */
  whoami(): Promise<Principal> {
    return this._req<Principal>("GET", "/v1/identity/whoami");
  }

  /** Issue an expiring credential. The plaintext `secret` is returned once. */
  createWorkloadCredential(
    request: WorkloadCredentialCreate,
  ): Promise<WorkloadCredentialCreated> {
    return this._req<WorkloadCredentialCreated>(
      "POST",
      "/v1/identity/workload-credentials",
      { json: request },
    );
  }

  /** Compatibility array containing one bounded page; prefer workloadCredentialsPage. */
  workloadCredentials(options: {
    includeRevoked?: boolean;
    includeExpired?: boolean;
    limit?: number;
  } = {}): Promise<WorkloadCredential[]> {
    return this._req<WorkloadCredential[]>(
      "GET",
      "/v1/identity/workload-credentials",
      {
        params: {
          include_revoked: options.includeRevoked,
          include_expired: options.includeExpired,
          limit: options.limit,
        },
      },
    );
  }

  /** Exact workload-credential inventory page with a stable created-at/UUID cursor. */
  workloadCredentialsPage(options: {
    includeRevoked?: boolean;
    includeExpired?: boolean;
    limit?: number;
    beforeCreatedAt?: string;
    beforeId?: string;
  } = {}): Promise<CompatibilityListPage<WorkloadCredential>> {
    return this._req<CompatibilityListPage<WorkloadCredential>>(
      "GET",
      "/v1/identity/workload-credentials",
      {
        params: {
          include_revoked: options.includeRevoked,
          include_expired: options.includeExpired,
          limit: options.limit,
          before_created_at: options.beforeCreatedAt,
          before_id: options.beforeId,
        },
        pageCursorNames: ["before_created_at", "before_id"],
      },
    );
  }

  workloadCredential(credentialId: string): Promise<WorkloadCredential> {
    return this._req<WorkloadCredential>(
      "GET",
      `/v1/identity/workload-credentials/${credentialId}`,
    );
  }

  /** Inspect the durable metering worker and exact backlog counts. */
  meteringInventory(namespace?: string): Promise<MeteringInventory> {
    return this._req<MeteringInventory>("GET", "/v1/admin/billing-metering/status", {
      admin: true,
      params: { namespace },
    });
  }

  /** Traverse the secret-free metering event inventory without hiding a tail. */
  meteringEventsPage(options: {
    status?: MeteringStatus;
    namespace?: string;
    limit?: number;
    beforeUpdatedAt?: string;
    beforeId?: string;
  } = {}): Promise<CompatibilityListPage<MeteringEvent>> {
    return this._req<CompatibilityListPage<MeteringEvent>>(
      "GET",
      "/v1/admin/billing-metering/events",
      {
        admin: true,
        params: {
          status: options.status,
          namespace: options.namespace,
          limit: options.limit,
          before_updated_at: options.beforeUpdatedAt,
          before_id: options.beforeId,
        },
        pageCursorNames: ["before_updated_at", "before_id"],
      },
    );
  }

  replayMeteringEvent(
    eventId: string,
    request: MeteringReplayRequest,
  ): Promise<MeteringEvent> {
    return this._req<MeteringEvent>(
      "POST",
      `/v1/admin/billing-metering/events/${eventId}/replay`,
      { admin: true, json: request },
    );
  }

  /** Inspect exact durable progress for one tenant-version SCIM User snapshot. */
  scimTenantReconciliation(
    tenantId: string,
    jobId: string,
  ): Promise<ScimTenantReconciliation> {
    return this._req<ScimTenantReconciliation>(
      "GET",
      `/v1/admin/enterprise/scim/tenants/${tenantId}/binding-reconciliations/${jobId}`,
      { admin: true },
    );
  }

  retryScimTenantReconciliation(
    tenantId: string,
    jobId: string,
  ): Promise<ScimTenantReconciliation> {
    return this._req<ScimTenantReconciliation>(
      "POST",
      `/v1/admin/enterprise/scim/tenants/${tenantId}/binding-reconciliations/${jobId}/retry`,
      { admin: true },
    );
  }

  /** Lease and advance at most one server-configured reconciliation page. */
  advanceScimTenantReconciliation(
    tenantId: string,
    jobId: string,
  ): Promise<ScimTenantReconciliation> {
    return this._req<ScimTenantReconciliation>(
      "POST",
      `/v1/admin/enterprise/scim/tenants/${tenantId}/binding-reconciliations/${jobId}/advance`,
      { admin: true },
    );
  }

  rotateWorkloadCredential(
    credentialId: string,
    request: WorkloadCredentialRotate,
  ): Promise<WorkloadCredentialCreated> {
    return this._req<WorkloadCredentialCreated>(
      "POST",
      `/v1/identity/workload-credentials/${credentialId}/rotate`,
      { json: request },
    );
  }

  revokeWorkloadCredential(
    credentialId: string,
    expectedVersion: number,
  ): Promise<void> {
    return this._req<void>(
      "DELETE",
      `/v1/identity/workload-credentials/${credentialId}`,
      { params: { expected_version: expectedVersion } },
    );
  }

  /** Read the unauthenticated protocol discovery document. */
  discovery(): Promise<LiansDiscovery> {
    return this._req<LiansDiscovery>("GET", "/.well-known/lians");
  }

  platformCapabilities(): Promise<PlatformCapabilities> {
    return this._req<PlatformCapabilities>("GET", "/v1/platform/capabilities");
  }

  /** Inspect deployment configuration readiness; requires admin scope. */
  platformReadiness(): Promise<PlatformReadiness> {
    return this._req<PlatformReadiness>("GET", "/v1/platform/readiness");
  }

  /** Prioritize recent decisions by evidence and control-plane signals. */
  investigatorQueue(
    opts: { limit?: number; scanLimit?: number } = {},
  ): Promise<InvestigatorQueue> {
    return this._req<InvestigatorQueue>("GET", "/v1/investigator/queue", {
      params: { limit: opts.limit, scan_limit: opts.scanLimit },
    });
  }

  /** Reconstruct evidence, Gate, review, and remediation for one decision. */
  investigateDecision(
    decisionId: string,
    opts: {
      timelineLimit?: number;
      evidenceLimit?: number;
      controlHistoryLimit?: number;
      caseLimit?: number;
      taskLimit?: number;
      closureLimit?: number;
      /** Explicit opt-in; requires admin scope. */
      includeSensitive?: boolean;
      verifyAudit?: boolean;
    } = {},
  ): Promise<DecisionInvestigationReport> {
    return this._req<DecisionInvestigationReport>(
      "GET",
      `/v1/investigator/decisions/${decisionId}`,
      {
        params: {
          timeline_limit: opts.timelineLimit,
          evidence_limit: opts.evidenceLimit,
          control_history_limit: opts.controlHistoryLimit,
          case_limit: opts.caseLimit,
          task_limit: opts.taskLimit,
          closure_limit: opts.closureLimit,
          include_sensitive: opts.includeSensitive,
          verify_audit: opts.verifyAudit,
        },
      },
    );
  }

  // ── Compliance / Erasure ──────────────────────────────────────────────────

  /**
   * GDPR Art. 17 / CCPA crypto-shred.
   * Destroys the data subject's per-subject DEK so all their memories become
   * permanently unreadable. The audit trail (hashes, timestamps) is preserved.
   */
  eraseSubject(req: EraseRequest, idempotencyKey?: string): Promise<EraseResult> {
    return this._req<EraseResult>("POST", "/v1/erase", {
      json: req,
      headers: idempotencyKey ? { "Idempotency-Key": idempotencyKey } : undefined,
    });
  }

  subjectErasureStatus(jobId: string): Promise<EraseResult> {
    return this._req<EraseResult>("GET", `/v1/erase/jobs/${jobId}`);
  }

  retrySubjectErasure(jobId: string): Promise<EraseResult> {
    return this._req<EraseResult>("POST", `/v1/erase/jobs/${jobId}/retry`);
  }

  // ── Conflicts ─────────────────────────────────────────────────────────────

  /** List detected contradictions between memories. */
  listConflicts(
    opts: {
      status?: string;
      limit?: number;
      afterDetectedAt?: string;
      afterId?: string;
    } = {},
  ): Promise<ConflictListResult> {
    return this._req<ConflictListResult>("GET", "/v1/conflicts", {
      params: {
        status: opts.status,
        limit: opts.limit,
        after_detected_at: opts.afterDetectedAt,
        after_id: opts.afterId,
      },
    });
  }

  /** Resolve a conflict by accepting one side or dismissing the flag. */
  resolveConflict(
    conflictId: string,
    req: ConflictResolveRequest,
  ): Promise<ConflictResolveResult> {
    return this._req<ConflictResolveResult>(
      "POST",
      `/v1/conflicts/${conflictId}/resolve`,
      { json: req },
    );
  }

  // ── Supersession review ───────────────────────────────────────────────────

  /**
   * Return supersession events whose confidence is below `threshold`.
   * Financial firms should poll this to surface uncertain supersessions for
   * human review before treating the old fact as stale.
   */
  reviewSupersessions(
    opts: { threshold?: number; limit?: number; beforeChainPosition?: number } = {},
  ): Promise<SupersessionReviewResult> {
    return this._req<SupersessionReviewResult>("GET", "/v1/supersessions/review", {
      params: {
        threshold: opts.threshold,
        limit: opts.limit,
        before_chain_position: opts.beforeChainPosition,
      },
    });
  }

  /**
   * Confirm a supersession — the engine was correct.
   * Writes an immutable audit event; the superseded memory remains closed.
   * Copy `expected_superseded_by` exactly from the reviewed queue item.
   */
  confirmSupersession(
    memoryId: string,
    request: SupersessionActionRequest,
  ): Promise<SupersessionActionResult> {
    return this._req<SupersessionActionResult>("PATCH", `/v1/supersessions/${memoryId}`, {
      json: { ...request, action: "confirm" },
    });
  }

  /**
   * Reject a supersession — the engine was wrong.
   * Restores the old memory as currently valid (valid_to = NULL) and writes an
   * immutable audit event. Both memories are now treated as additive.
   * Copy `expected_superseded_by` exactly from the reviewed queue item.
   */
  rejectSupersession(
    memoryId: string,
    request: SupersessionActionRequest,
  ): Promise<SupersessionActionResult> {
    return this._req<SupersessionActionResult>("PATCH", `/v1/supersessions/${memoryId}`, {
      json: { ...request, action: "reject" },
    });
  }

  // ── Compliance ────────────────────────────────────────────────────────────

  /**
   * Generate a compliance report for the caller's namespace.
   * Covers: memory counts, audit chain status, erasures, open conflicts,
   * supersession statistics, and retention policy snapshot.
   *
   * @param from   - Window start (ISO-8601 UTC). Omit for all-time.
   * @param to     - Window end (ISO-8601 UTC). Omit for now.
   * @param verify - Run hash-chain verification (adds ~50ms per 10k events).
   */
  complianceReport(
    opts: { from?: string; to?: string; verify?: boolean; subjectIdLimit?: number } = {},
  ): Promise<ComplianceReport> {
    return this._req<ComplianceReport>("GET", "/v1/compliance/report", {
      params: {
        from: opts.from,
        to: opts.to,
        verify: opts.verify,
        subject_id_limit: opts.subjectIdLimit,
      },
    });
  }

  // ── Webhooks ──────────────────────────────────────────────────────────────

  /**
   * Register a webhook endpoint.
   * The returned `secret` is shown exactly once — store it to verify signatures.
   * Every delivery is HMAC-SHA256-signed: `X-Lians-Signature: sha256=<hex>`
   */
  registerWebhook(req: WebhookRegisterRequest): Promise<WebhookRegisterResult> {
    return this._req<WebhookRegisterResult>("POST", "/v1/webhooks", { json: req });
  }

  /** List all webhook endpoints registered for the caller's namespace. */
  listWebhooks(): Promise<WebhookEndpoint[]> {
    return this._req<WebhookEndpoint[]>("GET", "/v1/webhooks");
  }

  /** Update an endpoint's enabled state, subscribed events, or description. */
  updateWebhook(endpointId: string, req: WebhookUpdateRequest): Promise<WebhookEndpoint> {
    return this._req<WebhookEndpoint>("PATCH", `/v1/webhooks/${endpointId}`, { json: req });
  }

  /** Remove the exact webhook version identified by its last `updated_at`. */
  deleteWebhook(endpointId: string, expectedUpdatedAt: string): Promise<void> {
    return this._req<void>("DELETE", `/v1/webhooks/${endpointId}`, {
      params: { expected_updated_at: expectedUpdatedAt },
    });
  }

  /** Return one stable keyset page of webhook delivery attempts. */
  webhookDeliveries(
    endpointId: string,
    limit = 50,
    afterCreatedAt?: string,
    afterId?: string,
  ): Promise<WebhookDeliveryListResult> {
    if ((afterCreatedAt === undefined) !== (afterId === undefined)) {
      throw new Error("afterCreatedAt and afterId must be supplied together");
    }
    return this._req<WebhookDeliveryListResult>(
      "GET", `/v1/webhooks/${endpointId}/deliveries`,
      {
        params: {
          limit,
          ...(afterCreatedAt !== undefined
            ? { after_created_at: afterCreatedAt, after_id: afterId }
            : {}),
        },
      },
    );
  }

  // ── Fact history ──────────────────────────────────────────────────────────

  /**
   * Return all recorded versions of a structured fact, ordered by event_time.
   *
   * Unlike `getLineage` (which requires a memory_id), this queries by what
   * analysts already know: the ticker and metric.  Superseded versions are
   * included so you can see how a fact evolved over time.
   *
   * Entity normalization is automatic — 'Apple Inc.', 'US0378331005' (ISIN),
   * '037833100' (CUSIP), and 'AAPL' all resolve to the same fact series.
   *
   * @example
   * const history = await client.factHistory({ agent_id: "equity-desk", ticker: "AAPL", metric: "eps" });
   */
  factHistory(opts: {
    agent_id: string;
    ticker: string;
    metric: string;
    limit?: number;
  }): Promise<FactHistoryResult> {
    return this._req<FactHistoryResult>("GET", "/v1/facts/history", {
      params: {
        agent_id: opts.agent_id,
        ticker: opts.ticker,
        metric: opts.metric,
        limit: opts.limit,
      },
    });
  }

  // ── Snapshot (audit reconstruction) ──────────────────────────────────────

  /**
   * Read an unranked, deterministic page of the knowledge state at `as_of`.
   * `total` is exact. Only `complete: true` means this response contains the
   * whole snapshot; otherwise continue with the returned keyset cursor and
   * retain its `recorded_as_of` transaction-time watermark.
   *
   * @param opts.agent_id - Agent whose knowledge state to reconstruct
   * @param opts.as_of    - ISO-8601 UTC checkpoint timestamp
   * @param opts.limit    - Max memories returned (default 1000)
   * @param opts.recorded_as_of - Watermark returned by the first page
   */
  snapshot(opts: {
    agent_id: string;
    as_of: string;
    limit?: number;
    after_event_time?: string;
    after_id?: string;
    recorded_as_of?: string;
  }): Promise<KnowledgeSnapshot> {
    return this._req<KnowledgeSnapshot>("GET", "/v1/snapshot", {
      params: {
        agent_id: opts.agent_id,
        as_of: opts.as_of,
        limit: opts.limit,
        after_event_time: opts.after_event_time,
        after_id: opts.after_id,
        recorded_as_of: opts.recorded_as_of,
      },
    });
  }

  // ── Backtest contamination ────────────────────────────────────────────────

  /**
   * Detect lookahead bias in a backtest simulation.
   *
   * Counts every visible recorded contaminant and returns one bounded flag page.
   * Two contamination types:
   *   - `future_event`  — event_time > simulation_as_of (clear lookahead)
   *   - `late_revision` — ingestion_time > simulation_as_of (subtle: the revised
   *     figure hadn't landed yet, even though the event is historical)
   *
   * `is_clean: true` means no recorded memory visible in the authenticated
   * namespace/barrier violates the cutoff. It does not attest to unrecorded
   * external inputs.
   *
   * @param opts.agent_id          - Agent to inspect
   * @param opts.simulation_as_of  - ISO-8601 UTC simulation checkpoint
   */
  backtestCheck(opts: {
    agent_id: string;
    simulation_as_of: string;
    flag_limit?: number;
    after_event_time?: string;
    after_id?: string;
  }): Promise<ContaminationReport> {
    return this._req<ContaminationReport>("POST", "/v1/backtest/check", {
      json: {
        agent_id: opts.agent_id,
        simulation_as_of: opts.simulation_as_of,
        flag_limit: opts.flag_limit,
        after_event_time: opts.after_event_time,
        after_id: opts.after_id,
      },
    });
  }

  // ── Erasure certificate ───────────────────────────────────────────────────

  /**
   * Retrieve the cryptographic proof-of-erasure certificate for a data subject.
   *
   * The certificate proves: (1) N memories had their encrypted content permanently
   * destroyed; (2) SHA-256 content_hashes are preserved — auditable but
   * unrecoverable; (3) the audit chain remained intact after erasure.
   *
   * Returns 404 if no erasure has been recorded for this subject.
   * Requires admin scope.
   */
  erasureCertificate(
    subjectId: string,
    opts: { limit?: number; afterMemoryId?: string } = {},
  ): Promise<ErasureCertificate> {
    return this._req<ErasureCertificate>("GET", `/v1/erase/${subjectId}/certificate`, {
      params: { limit: opts.limit, after_memory_id: opts.afterMemoryId },
    });
  }

  erasureCertificateByJob(
    jobId: string,
    opts: { limit?: number; afterMemoryId?: string } = {},
  ): Promise<ErasureCertificate> {
    return this._req<ErasureCertificate>("GET", `/v1/erase/jobs/${jobId}/certificate`, {
      params: { limit: opts.limit, after_memory_id: opts.afterMemoryId },
    });
  }

  // ── Admin / Audit chain ───────────────────────────────────────────────────

  /**
   * Export one exact-count, keyset-paginated audit-log page.
   * `total_rows` is exact before the cursor. Follow `next_chain_position` while
   * `has_more` is true; only an uncursored result with `complete: true` contains
   * the full filtered collection. Retain `snapshot_max_chain_position` as
   * `through_chain_position` across continuations. `verify: true` adds bounded
   * chain verification.
   * Requires `adminSecret` to be set on the client.
   */
  auditExport(opts: {
    namespace: string;
    from?: string;
    to?: string;
    limit?: number;
    verify?: boolean;
    after_chain_position?: number;
    through_chain_position?: number;
  }): Promise<AuditExportResult> {
    return this._req<AuditExportResult>("GET", "/v1/admin/audit/export", {
      params: {
        namespace: opts.namespace,
        from: opts.from,
        to: opts.to,
        limit: opts.limit,
        verify: opts.verify,
        after_chain_position: opts.after_chain_position,
        through_chain_position: opts.through_chain_position,
      },
      admin: true,
    });
  }

  /**
   * Verify the SEC 17a-4 tamper-evidence hash chain for a namespace.
   * Returns an `ok`, `partial`, or `tampered` status with explicit truncation.
   * Requires `adminSecret` to be set on the client.
   */
  verifyChain(namespace: string): Promise<AuditChainVerifyResult> {
    return this._req<AuditChainVerifyResult>("GET", "/v1/admin/audit/verify", {
      params: { namespace },
      admin: true,
    });
  }
}

function responseRetryAfterMs(response: Response): number | undefined {
  try {
    const value = response.headers?.get("Retry-After")?.trim();
    if (!value) return undefined;
    if (/^\d+$/.test(value)) {
      const seconds = Number(value);
      return Number.isSafeInteger(seconds)
        ? Math.min(Number.MAX_SAFE_INTEGER, seconds * 1_000)
        : undefined;
    }
    const timestamp = Date.parse(value);
    return Number.isFinite(timestamp) ? Math.max(0, timestamp - Date.now()) : undefined;
  } catch {
    return undefined;
  }
}
