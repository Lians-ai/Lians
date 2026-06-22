/**
 * AgentMem TypeScript SDK — async HTTP client for the REST API.
 *
 * AgentMem is a financial-grade AI memory layer that provides:
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
 * const client = new LianClient({
 *   baseUrl: "https://mem.yourfirm.internal",
 *   apiKey:  process.env.AGENTMEM_API_KEY!,
 *   adminSecret: process.env.AGENTMEM_ADMIN_SECRET,
 * });
 * const result = await client.recall({ agent_id: "equity-desk", query: "AAPL price target" });
 */

import type {
  LianClientOptions,
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
  AuditExportResult,
  WebhookEndpoint,
  WebhookRegisterRequest,
  WebhookRegisterResult,
  WebhookUpdateRequest,
  WebhookDeliveryListResult,
  ComplianceReport,
  FactHistoryResult,
  KnowledgeSnapshot,
  ContaminationReport,
} from "./types.js";

// ── Error class ───────────────────────────────────────────────────────────────

/** Thrown by LianClient when the server returns a non-2xx response. */
export class LianError extends Error {
  constructor(
    public readonly status: number,
    public readonly body: string,
    message: string,
  ) {
    super(message);
    this.name = "LianError";
  }
}

// ── Internal request options ──────────────────────────────────────────────────

interface ReqOpts {
  json?: unknown;
  params?: Record<string, string | number | boolean | undefined | null>;
  admin?: boolean;
}

// ── Client ────────────────────────────────────────────────────────────────────

export class LianClient {
  private readonly baseUrl: string;
  private readonly apiKey: string;
  private readonly adminSecret: string | undefined;
  private readonly timeoutMs: number;

  constructor(options: LianClientOptions) {
    this.baseUrl = options.baseUrl.replace(/\/$/, "");
    this.apiKey = options.apiKey;
    this.adminSecret = options.adminSecret;
    this.timeoutMs = options.timeoutMs ?? 30_000;
  }

  // ── Internal ──────────────────────────────────────────────────────────────

  private async _req<T>(
    method: string,
    path: string,
    opts: ReqOpts = {},
  ): Promise<T> {
    const { json, params, admin } = opts;

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
      "X-API-Key": this.apiKey,
    };
    if (admin && this.adminSecret) {
      headers["X-Admin-Secret"] = this.adminSecret;
    }

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
      throw new LianError(
        res.status,
        body,
        `AgentMem ${method} ${path} → ${res.status}: ${body}`,
      );
    }

    if (res.status === 204) return undefined as unknown as T;
    return res.json() as Promise<T>;
  }

  // ── Write ─────────────────────────────────────────────────────────────────

  /** Store a financial fact, observation, or decision with its event timestamp. */
  addMemory(req: MemoryAdd): Promise<MemoryOut> {
    return this._req<MemoryOut>("POST", "/v1/memories", { json: req });
  }

  /**
   * Add multiple memories in a single request.
   * Items are processed sequentially, so a later item can supersede an earlier
   * one within the same batch (useful when loading a time-series of revisions).
   */
  batchAdd(memories: MemoryAdd[]): Promise<MemoryBatchResult> {
    return this._req<MemoryBatchResult>("POST", "/v1/memories/batch", {
      json: { memories },
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
   * Return the full supersession lineage graph for a memory.
   * Useful for audit and human review: shows every version of a fact and the
   * edges (with confidence + rationale) that link them.
   */
  getLineage(memoryId: string): Promise<MemoryLineageResult> {
    return this._req<MemoryLineageResult>("GET", `/v1/memories/${memoryId}/lineage`);
  }

  // ── Compliance / Erasure ──────────────────────────────────────────────────

  /**
   * GDPR Art. 17 / CCPA crypto-shred.
   * Destroys the data subject's per-subject DEK so all their memories become
   * permanently unreadable. The audit trail (hashes, timestamps) is preserved.
   */
  eraseSubject(req: EraseRequest): Promise<EraseResult> {
    return this._req<EraseResult>("POST", "/v1/erase", { json: req });
  }

  // ── Conflicts ─────────────────────────────────────────────────────────────

  /** List detected contradictions between memories. */
  listConflicts(opts: { status?: string; limit?: number } = {}): Promise<ConflictListResult> {
    return this._req<ConflictListResult>("GET", "/v1/conflicts", {
      params: { status: opts.status, limit: opts.limit },
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
    opts: { threshold?: number; limit?: number } = {},
  ): Promise<SupersessionReviewResult> {
    return this._req<SupersessionReviewResult>("GET", "/v1/supersessions/review", {
      params: { threshold: opts.threshold, limit: opts.limit },
    });
  }

  /**
   * Confirm a supersession — the engine was correct.
   * Writes an immutable audit event; the superseded memory remains closed.
   */
  confirmSupersession(memoryId: string, reviewerNote?: string): Promise<unknown> {
    return this._req<unknown>("PATCH", `/v1/supersessions/${memoryId}`, {
      json: { action: "confirm", reviewer_note: reviewerNote },
    });
  }

  /**
   * Reject a supersession — the engine was wrong.
   * Restores the old memory as currently valid (valid_to = NULL) and writes an
   * immutable audit event. Both memories are now treated as additive.
   */
  rejectSupersession(memoryId: string, reviewerNote?: string): Promise<unknown> {
    return this._req<unknown>("PATCH", `/v1/supersessions/${memoryId}`, {
      json: { action: "reject", reviewer_note: reviewerNote },
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
  complianceReport(opts: { from?: string; to?: string; verify?: boolean } = {}): Promise<ComplianceReport> {
    return this._req<ComplianceReport>("GET", "/v1/compliance/report", {
      params: { from: opts.from, to: opts.to, verify: opts.verify },
    });
  }

  // ── Webhooks ──────────────────────────────────────────────────────────────

  /**
   * Register a webhook endpoint.
   * The returned `secret` is shown exactly once — store it to verify signatures.
   * Every delivery is HMAC-SHA256-signed: `X-Lian-Signature: sha256=<hex>`
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

  /** Remove a webhook endpoint permanently. */
  deleteWebhook(endpointId: string): Promise<void> {
    return this._req<void>("DELETE", `/v1/webhooks/${endpointId}`);
  }

  /** Return recent delivery attempts for a webhook endpoint. */
  webhookDeliveries(endpointId: string, limit = 50): Promise<WebhookDeliveryListResult> {
    return this._req<WebhookDeliveryListResult>(
      "GET", `/v1/webhooks/${endpointId}/deliveries`,
      { params: { limit } },
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
   * Reconstruct the complete knowledge state of an agent at a specific point
   * in time. Returns every fact that was valid at `as_of` — exhaustive, no
   * relevance filter.
   *
   * This is the "audit reconstruction as a product surface" from SCALE.md §4:
   * "Show me the agent's complete knowledge state as of T." One call. The
   * compliance demo that closes deals with risk committees and regulators.
   * mem0 has no temporal model. Graphiti/Zep has temporal graph queries but
   * no tamper-evident hash chain or compliance export API.
   *
   * @param opts.agent_id - Agent whose knowledge state to reconstruct
   * @param opts.as_of    - ISO-8601 UTC checkpoint timestamp
   * @param opts.limit    - Max memories returned (default 1000)
   */
  snapshot(opts: { agent_id: string; as_of: string; limit?: number }): Promise<KnowledgeSnapshot> {
    return this._req<KnowledgeSnapshot>("GET", "/v1/snapshot", {
      params: { agent_id: opts.agent_id, as_of: opts.as_of, limit: opts.limit },
    });
  }

  // ── Backtest contamination ────────────────────────────────────────────────

  /**
   * Detect lookahead bias in a backtest simulation.
   *
   * Scans the agent's memory store and flags every fact the agent couldn't have
   * known at `simulation_as_of`. Returns two contamination types:
   *   - `future_event`  — event_time > simulation_as_of (clear lookahead)
   *   - `late_revision` — ingestion_time > simulation_as_of (subtle: the revised
   *     figure hadn't landed yet, even though the event is historical)
   *
   * `is_clean: true` is the proof a risk committee needs before trusting a
   * backtest result. This is the "thin open-sourceable primitive" from SCALE.md §6
   * that quant engineers can't get from any other memory store.
   *
   * @param opts.agent_id          - Agent to inspect
   * @param opts.simulation_as_of  - ISO-8601 UTC simulation checkpoint
   */
  backtestCheck(opts: {
    agent_id: string;
    simulation_as_of: string;
  }): Promise<ContaminationReport> {
    return this._req<ContaminationReport>("POST", "/v1/backtest/check", {
      json: { agent_id: opts.agent_id, simulation_as_of: opts.simulation_as_of },
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
  erasureCertificate(subjectId: string): Promise<ErasureCertificate> {
    return this._req<ErasureCertificate>("GET", `/v1/erase/${subjectId}/certificate`);
  }

  // ── Admin / Audit chain ───────────────────────────────────────────────────

  /**
   * Export the full audit log for a namespace (SEC/FINRA/CFTC examiners).
   * Pass `verify: true` to include a chain-verification report alongside events.
   * Requires `adminSecret` to be set on the client.
   */
  auditExport(opts: {
    namespace: string;
    from?: string;
    to?: string;
    limit?: number;
    verify?: boolean;
  }): Promise<AuditExportResult> {
    return this._req<AuditExportResult>("GET", "/v1/admin/audit/export", {
      params: {
        namespace: opts.namespace,
        from_: opts.from,
        to: opts.to,
        limit: opts.limit,
        verify_chain: opts.verify,
      },
      admin: true,
    });
  }

  /**
   * Verify the SEC 17a-4 tamper-evidence hash chain for a namespace.
   * Returns `{ status: "ok", rows_checked: N }` or details on every broken link.
   * Requires `adminSecret` to be set on the client.
   */
  verifyChain(namespace: string): Promise<unknown> {
    return this._req<unknown>("GET", "/v1/admin/audit/verify", {
      params: { namespace },
      admin: true,
    });
  }
}
