# Changelog

All notable changes to Lians. Versions follow semver; SDKs are released in lock-step.

## 0.5.0 — 2026-08-02

The decision-evidence infrastructure release. Lians now records, verifies,
controls, reconstructs, and remediates consequential AI actions across model
providers and agent runtimes. This is a pre-1.0 minor release with additive API
surfaces and deliberate response-contract improvements described below.

### Added

- **Open Decision Receipt v0.1.** JSON Schema, canonical SHA-256 document,
  Ed25519 signing, offline verifier CLI, independent fixtures/conformance runner,
  trust-key registry, immutable key rotation/revocation, and pinned OpenTelemetry
  GenAI, MCP, and A2A mapping artifacts.
- **Normalized evidence graph.** First-class source, policy, model, tool,
  permission, instruction, input, and output artifacts; persisted per-kind
  coverage; explicit legacy gaps; transaction-time-safe reconstruction; indexed
  direct and reachable dependency impact.
- **Exhaustive autonomous blast radius.** Frozen decision/link snapshots,
  durable keyset cursors, idempotent match pages, multi-replica `SKIP LOCKED`
  leasing, bounded retries, poison-job termination, caller-driven compatibility,
  worker readiness, metrics, and alerts.
- **Universal Recorder v0.1.** Provider-neutral native/OTLP/MCP/A2A envelopes,
  authenticated producer attribution, immutable event hashes, privacy-safe
  capture modes, automatic correlation, first-receipt readiness, and a bounded
  retry-safe SDK sink with explicit loss disclosure.
- **Native Recorder hooks.** Public lifecycle integrations for OpenAI Agents,
  LangChain/LangGraph, CrewAI, Anthropic SDK middleware, Google ADK plugins, and
  Vercel AI SDK callbacks, each with documented observable and unobservable
  boundaries.
- **Lians Gate.** Immutable versioned policies, trusted-receipt verification,
  identity/scope/barrier checks, source and policy currency, injection signals,
  role-bound encrypted approval attestations, short-lived single-use permits,
  and a separately deployable enforcement mediator that binds the canonical
  downstream request before side effects.
- **Lians Investigator.** Prioritized decision queue, deterministic
  reconstruction reports, evidence/control timelines, review-chain validation,
  cases, owned remediation tasks, and encrypted human-attested closure. Report
  v1.1 exposes exact per-collection coverage/truncation metadata so a bounded
  packet cannot be mistaken for a complete history.
- **Enterprise identity and governance.** OIDC/JWKS authentication, SCIM 2.0,
  RBAC, information barriers, expiring workload credentials, immutable namespace
  policy revisions, server-owned residency, capture policy, and atomic daily
  quotas for Recorder events, decisions, protected actions, writes, recalls,
  and estimated ingest bytes.
- **Bounded provisioning and auth bootstrap.** SCIM reconciliation now enforces
  complete 1,000-by-1,000 Group/User membership bounds plus a 50-scope effective
  union in both service and serialized PostgreSQL boundaries. API-key and OIDC
  binding bootstrap use exact PUBLIC-revoked SECURITY DEFINER lookups, while
  direct auth-table access is namespace/barrier RLS-constrained.
- **DecisionRecord authorization evidence.** Hash v3 binds the authenticated
  principal type, optional role, and complete effective-scope snapshot to each
  new decision. Database constraints require a verified credential and 1-50
  unique valid scopes containing `write`; v1/v2 remain unchanged and explicitly
  disclose that no historical authorization snapshot exists.
- **Protected-unit economics.** One durable `lians_authoritative_decision` fact
  per committed decision and one `lians_protected_action` fact per successfully
  consumed Gate permit, transactionally bound to source mutation and audit
  evidence. Replays, quota denials, mismatches, expiry, and rejections do not
  bill.
- **Production operations.** Exact migration/image readiness, transactional
  idempotency, optimistic concurrency, remote receipt/KMS key isolation, online
  key rotation fences, integration and metering outboxes, bounded-cardinality
  durable metrics, SLO alerts, Grafana views, backup/WORM guidance, SBOM,
  provenance, signing, and digest-pinned deployment examples.
- **Versioned contracts and SDK parity.** Public/admin OpenAPI snapshots;
  canonical Python and TypeScript decision/control clients; typed compatibility
  Python models; one-time-secret redaction; bounded retry semantics; and explicit
  stale-write preconditions.

### Changed

- The public category and primary product vocabulary are now **decision evidence
  infrastructure**, **Decision Receipt**, **reconstruct**, and **protected
  decision/action**. Bitemporal memory remains a core evidence primitive and a
  backward-compatible product surface.
- Investigator reports move to contract version 1.1 and add required coverage
  disclosures. Consumers that deserialize reports into closed structs must add
  the new fields before upgrading.
- Production startup requires the autonomous impact worker and an exact database
  schema match. Apply the single packaged Alembic head before rolling API pods.
- The final auth-table contract is a fenced cutover: apply the exact lookup
  expand and concurrent pending-admission index while old pods remain live,
  then drain every old direct-table authentication caller before `0056b`.
- The 0.4.2-to-0.5.0 database transition is now an explicit expand/backfill/
  contract sequence. Large historical backfills use committed, resumable pages;
  established-table indexes build concurrently with invalid-index recovery; and
  data-bearing revisions refuse misleading offline SQL generation.
- Mixed-version writes remain safe during the documented rolling window: legacy
  audit inserts are database-canonicalized, legacy decision/Recorder provenance
  stays explicitly unverified, and old/new idempotency paths coexist without
  weakening replay conflict detection.
- Production continuously verifies its PostgreSQL identity: the API login and
  fixed `lians_runtime` capability must remain non-owner, least-privilege,
  RLS-bound roles that cannot assume an application owner. Raw Kubernetes now
  isolates runtime, migration, and break-glass Secrets as separate workloads.
- ValidMind inventories, compliance reports, receipt trust lists, and remediation
  queues now aggregate and page in SQL with deterministic bounds, exact
  completeness disclosure, and dedicated scale indexes.
- ValidMind model resources are now separated by an opaque information-barrier
  scope (`metadata.lians_scope_id`), so a model's 0.5 resource ID can differ
  from its namespace-wide 0.4.2 ID. A legacy ID remains readable and writable
  only while it resolves to one scope; uniquely resolvable legacy/scoped link
  rows are mirrored during the rolling window, while ambiguous old IDs return
  `409` instead of guessing a protected scope.
- Go, Java, and C transports now enforce validated base URLs, redirect blocking,
  operation-wide deadlines, bounded responses, safe-only retries, stable
  idempotency keys, and sanitized errors; SDK runtime versions are part of the
  lock-step release contract.

### Truth boundaries

- A receipt proves the integrity and provenance of the **recorded** boundary; it
  does not claim deterministic reproduction of nondeterministic model behavior,
  hidden reasoning, unrecorded context, source correctness, or causal certainty.
- Native framework hooks observe only public callbacks/middleware emitted by the
  configured runtime. Every adapter documents gaps such as disabled tracing,
  independently created runners, remote execution, and client-tool work outside
  the observed boundary.
- The SDK Recorder sink is bounded and retry-safe but in-memory. Deploy a durable
  outbox/collector when process-crash loss is unacceptable.

## 0.4.0 — 2026-07-06

The memory-lifecycle release: flush, resurface, decay, degrade, export.

### Added
- **Pre-compaction memory flush (SDK).** `LiansMemoryHarness.flush_before_compaction()`
  persists durable facts into governed memory before the host framework
  summarizes them away — explicit facts, an extract callable over the
  transcript, or the assistant-message fallback. `CompactionGuard` tracks
  estimated token usage and fires the flush once per window at a threshold.
  Ships as a LangGraph `create_flush_node()` and as an `agentmem_flush` /
  `flush_memory` tool for the OpenAI Agents SDK, CrewAI, and AutoGen. Every
  flush is audit-tagged `_flush: "pre_compaction"`.
- **Signed Markdown memory statement.** `GET /v1/snapshot/markdown` renders an
  agent's exhaustive point-in-time knowledge state as a Markdown document —
  provenance, validity window, and materiality per fact; erased facts appear
  as explicit crypto-shred markers with existence preserved. The document's
  SHA-256 is anchored in the audit chain as an `export_markdown` event, and an
  integrity footer states the hash and the verification procedure. `raw=true`
  returns bare `text/markdown`.
- **Open conflicts resurface until adjudicated.** `/v1/context` pushes the
  agent's open conflicts to the top of every assembled block (oldest first) as
  explicit "X DISAGREES WITH Y" lines. Per-call opt-out via
  `surface_conflicts=false`; bounded by `max_conflicts` with an explicit
  "+N more" overflow line — never a silent drop.
- **Audited degraded retrieval.** An embedding-provider outage no longer takes
  recall down: the query proceeds lexical-only (BM25 + recency + importance)
  and the degradation is explicit everywhere — `retrieval_degraded` on
  `RecallResult` and `ContextResult`, in the recall audit event, and as a
  metric label for alerting. Degraded results are never cached. Keyed lookups
  never embed, so they never degrade.
- **Materiality-weighted retrieval decay.** A fact's retrieval half-life
  scales with `metadata.materiality` — low 7d / standard 30d / high 120d /
  critical 365d. Ranking-only: storage never decays; point-in-time (`as_of`)
  scoring honors the same weights; untagged facts keep the 30-day default.

## 0.3.4 — 2026-07-03

Supersedes 0.3.3, which never reached PyPI: its wheel force-include only
resolved from a repo checkout, and the release pipeline builds the wheel from
the sdist. A custom hatch build hook now resolves the vendored engine from
either location; the sdist ships the engine too, so `pip install` from source
works. (npm 0.3.3 was published before the failure; 0.3.4 restores lock-step.)

## 0.3.3 — 2026-07-03 (not published to PyPI)

Patch release. Fixes the flagship zero-setup path for installed users.

### Fixed
- **`LocalLiansClient` was broken on every installed wheel.** The local mode
  imports the service engine (`src.lians.*`), which only existed in the
  monorepo checkout — `pip install lians-sdk[local]` outside the repo failed
  with `ModuleNotFoundError: No module named 'src'` on first use. The wheel
  now vendors the engine as `lians_engine` (hatchling force-include) and the
  SDK aliases it to `src.lians` at import time; the `[local]` extra gained the
  engine's runtime dependencies (pydantic/-settings, cryptography, pgvector,
  numpy, fastapi, asyncpg). Verified end-to-end from a clean venv: add,
  recall, `recall_at`, `backtest_check`, crypto-shred erase with certificate,
  and audit-chain verify.
- C and Go SDK version strings had drifted (still 0.3.0); all versions are
  back in lock-step at 0.3.3.

## 0.3.2 — 2026-07-02

Patch release. Cross-language + packaging validation against a live server found
three more bugs; every SDK, the agent harness, and the MCP server now pass.

### Fixed
- **MCP server: `fact_history` and `list_conflicts` tools were broken.** The GET
  helper passed an empty `params={}`, which httpx uses to *replace* the query
  string — wiping queries baked into the request path, so two of the eight MCP
  tools 422'd against any server. (Ships in `lians-sdk[mcp]`.)
- **Java SDK could not reach the server at all.** Its `HttpClient` defaulted to
  HTTP/2; the cleartext HTTP/1.1 server rejected the h2c upgrade as "Invalid HTTP
  request received". Pinned `HttpClient.Version.HTTP_1_1`.
- Plugin `CLAUDE.md` TypeScript example called a non-existent `mem.add(...)` with
  camelCase keys; corrected to `mem.addMemory({ agent_id, event_time, ... })`.

### Validated (live server)
- All five SDKs — Python (sync + async), TypeScript, Go, Java, C.
- Agent harness (`LiansMemoryHarness`) recall-before / remember-after loop.
- MCP server over stdio — handshake, all 8 tools, remember/recall/fact_history.

## 0.3.1 — 2026-07-01

Patch release. Bug fixes found while limit-testing the live stack, plus the
governance-layer alignment (REFINES relation, vagueness admission filter).

### Fixed (correctness / security)
- **Cross-tenant subject-key isolation.** `subject_keys` was keyed by `subject_id`
  alone, so two namespaces sharing a `subject_id` shared one AES data-encryption
  key — and one tenant's GDPR erase crypto-shredded the other tenant's data.
  Now keyed by `(namespace, subject_id)` (migration 0019); the in-process DEK
  cache is namespace-scoped too.
- **`RATE_LIMIT_PER_MINUTE` is now honored.** The rate-limit middleware was added
  without its argument and silently pinned every deployment to 300/min.
- **`lians-sdk` is importable on a plain install.** `import lians` crashed unless
  the `[local]` extra was installed; `LocalLiansClient` is now imported lazily.
  (This is the reason for the 0.3.1 SDK republish — the 0.3.0 wheel is broken.)
- **`docker compose up` no longer crash-loops** on a stale `src.lian.main` module
  path in the Dockerfile CMD.

### Added
- **REFINES supersession relation** — a new fact that narrows/enriches an existing
  one closes the old validity window like SUPERSEDES but is audited as a narrowing.
  Harvested from the Lian Memory Governor vocabulary.
- **Vagueness admission pre-filter** — too-vague candidates are tagged and rejected
  in enforce mode.
- **`MemoryOut.score`** — recall responses now expose the hybrid relevance score.

## 0.3.0 — 2026-06-29

The production-readiness + competitive release. Everything below is on `master`
with full CI (12 checks across 5 languages + Postgres).

### Added
- **Agent memory harness** (`LiansMemoryHarness`) — drop-in recall-before /
  remember-after loop with compliance scoping.
- **Relationship graph** — `relate` / `unrelate` / `neighbors` / `path` (bitemporal,
  point-in-time), **graph-proximity (node-distance) reranking**, and `POST
  /v1/graph/extract` (rule-based text→edges, opt-in LLM).
- **MMR reranking** and `POST /v1/context` — token-budgeted, ready-to-inject block.
- **Three new SDKs — Go, Java, and C** — now five languages (Python, TypeScript,
  Go, Java, C). npm package renamed to `@lians-ai/lians`.
- **Exactly-once writes** — `Idempotency-Key` on `POST /v1/memories`; SDK
  retry/backoff with an auto idempotency key.
- **RBAC roles** (`owner`/`analyst`/`compliance`/`readonly`) on API keys.
- **SIEM audit streaming** (`SIEM_URL`) + `/livez` and `/readyz` probes.
- Memory **evaluation harness** (LoCoMo/LongMemEval shape, judge-free).
- Claude Code plugin, Codex integration, cross-tool skills.
- Docs: security whitepaper, STRIDE threat model, SOC 2/HIPAA readiness, SSO,
  publishing, and mem0 / Zep comparisons.

### Fixed (correctness / security)
- **Information barriers now enforced at the database layer.** Barrier RLS policies
  are `RESTRICTIVE` (migration 0013) and the barrier session var is set per
  request; cross-barrier denial is proven in CI against a non-superuser role.
  Previously isolation was app-layer only.
- Restored `memory_service` functions the API imported but lacked (snapshot,
  lineage, fact-history, conflicts, erasure certificate); wired conflict
  persistence and webhook dispatch.
- Fixed the migration runner (asyncpg multi-statement / parameterized `SET`) and a
  stack of CI environment issues — CI is green for the first time.

> **Deployment note:** run the application as a **non-superuser, non-BYPASSRLS**
> Postgres role, or RLS (namespace + barrier isolation) is silently bypassed.

## 0.2.0 — 2026-06-27

Free tier, cloud pricing, GitHub org migration to `Lians-ai`.
