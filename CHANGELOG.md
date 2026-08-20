# Changelog

All notable changes to Lians. Versions follow semver. Source manifests are kept
synchronized for a release candidate, but public registries are verified
independently and may differ when a publisher fails. See
[`docs/published-release-status.json`](docs/published-release-status.json) for the
current distribution truth.

## Unreleased - source manifests at 0.5.0

The `0.5.0` source version is not a published-release claim. Do not advertise or
install `0.5.0` until the release checklist verifies PyPI, npm, Go, Maven, the C
asset, GitHub Releases, GHCR, and the MCP Registry from clean environments.

### Added
- Lians Check, a two-command proof-of-done workflow. `lians init` discovers and
  authorizes a bounded project policy, and `lians check` runs the commands
  locally and returns `NO PROOF`, `NEEDS WORK`, or `READY TO REVIEW`.
- Fail-closed local execution with no implicit shell, bounded and redacted
  output, command timeouts, policy digests, current Git content binding,
  workspace-mutation detection, and signed measured-local receipts.
- Lians Guard task contracts with current-work recovery, workspace fingerprints,
  stale-state reporting, typed evidence, and an explicit
  `READY FOR HUMAN REVIEW` gate.
- Attested GitHub Actions evidence import bound to an exact repository, signer
  workflow, ref, commit, hosted runner, selected passing checks, and an
  interactively authorized criterion mapping.
- Local `lians report` and interactive `lians confirm` commands, Claude
  `PreCompact` and `SessionEnd` capture, cross-platform clean-install CI, and
  native macOS and Windows GUI launch checks.
- Lians Easy, a dependency-free desktop runtime and guided installer that can
  configure supported AI clients, preserve configuration backups, share one
  local profile across clients, and support silent enterprise deployment.
- Core MCP memory controls for listing, correcting, and confirmed forgetting,
  alongside remember and bounded recall.
- Verifiable AI memory controls: human-readable Memory Receipts on recall and
  context responses, list/correct/forget APIs, and Python and TypeScript SDK
  support. Corrections preserve supersession history; record-level forgetting
  requires explicit confirmation and preserves an audit tombstone.
- Decision Envelopes, completeness grades, signed Evidence Packs, and offline
  verification for point-in-time decision reconstruction.
- RIAD-1 reconstruction gates covering historical state, future-leak detection,
  revision handling, and receipt verification.
- Adaptive recall modes, governed feedback learning, correction lineage,
  scheduled maintenance, and memory-intelligence metrics.
- Content-addressed `/version` deployment evidence plus a checked-in public
  release matrix and live registry verifier.

### Changed
- The public product story now leads with Lians Check. Cross-agent memory,
  recovery, graphs, and broader Guard controls remain supporting capabilities.
  Top-level command help shows only the two-command loop and essential local
  app diagnostics while documented advanced commands remain compatible.
- Agent-facing checkpoint calls can no longer grant themselves measured or
  human-confirmed trust. Self-declared trusted labels remain visible but are
  stored as agent attestations until an authorized evidence path accepts them.
- The repository-wide Ruff policy now has an explicit correctness baseline;
  package-specific workflows retain stricter checks where they are actionable.
- Versioned audit payload hashing and stronger production deployment gates.
- Runtime, webhook, artifact, and workflow security hardening.
- Runtime Docker contexts now exclude nested dependency/build trees; the test
  harness accepts an explicit `TEST_REDIS_URL` for isolated local and CI runs.

## 0.4.2 - 2026-07-18 (Python SDK only)

- Published `lians-sdk` with real local semantic embeddings included in the
  `[local]` and `[mcp]` installation paths. Other ecosystems remained on their
  previously published versions.

## 0.4.1 - 2026-07-17

- Corrected the Java group to `ai.lians` and published Java 0.4.1 to Maven
  Central, Go 0.4.1 to the module proxy, C 0.4.1 as a GitHub release asset, and
  MCP manifest/container 0.4.1.
- Shipped lifecycle, recall-quality, local-MCP, packaging, and benchmark fixes.
- npm remained at 0.4.0 after the 0.4.1 publisher authorization failure.

## Unreleased

### Changed

- Recall policy `lians-recall-policy-v3` caps each query facet at 400 candidates
  (at most four facets) and publishes both the per-facet and request-wide upper
  bounds alongside per-memory work limits in its evidence.
- Recall evidence advances to `lians.recall-receipt.v2`, which binds each
  result's final score and complete breakdown alongside policy and reference
  time, plus any attached neighbor's returned content, metadata, temporal
  provenance, source, and information barrier. Compiled context advances to
  `lians.context-receipt.v2`. Cache schema `scoring-v2` isolates older receipt
  payloads. Typed SDK score fields are additive; receipt-JSON consumers should
  branch on `schema`.
- Context neighbors now use bounded indexed lookups with event, ingestion,
  validity, and post-commit information-barrier checks. Retrieval and optional
  cross-encoder scoring use bounded text/token samples; cached scoring packs
  have explicit plaintext and slot ceilings, and timed-out rerankers cannot
  mutate returned evidence.
- Idempotent writes now commit their retry key and durable recall-invalidation
  barrier atomically with the memory. A retry repairs a post-commit cache outage
  and returns the original memory instead of inserting a duplicate.

## 0.4.0 - 2026-07-06

The memory-lifecycle release: flush, resurface, decay, degrade, export.

### Added
- **Pre-compaction memory flush (SDK).** `LiansMemoryHarness.flush_before_compaction()`
  persists durable facts into governed memory before the host framework
  summarizes them away - explicit facts, an extract callable over the
  transcript, or the assistant-message fallback. `CompactionGuard` tracks
  estimated token usage and fires the flush once per window at a threshold.
  Ships as a LangGraph `create_flush_node()` and as an `agentmem_flush` /
  `flush_memory` tool for the OpenAI Agents SDK, CrewAI, and AutoGen. Every
  flush is audit-tagged `_flush: "pre_compaction"`.
- **Signed Markdown memory statement.** `GET /v1/snapshot/markdown` renders an
  agent's exhaustive point-in-time knowledge state as a Markdown document -
  provenance, validity window, and materiality per fact; erased facts appear
  as explicit crypto-shred markers with existence preserved. The document's
  SHA-256 is anchored in the audit chain as an `export_markdown` event, and an
  integrity footer states the hash and the verification procedure. `raw=true`
  returns bare `text/markdown`.
- **Open conflicts resurface until adjudicated.** `/v1/context` pushes the
  agent's open conflicts to the top of every assembled block (oldest first) as
  explicit "X DISAGREES WITH Y" lines. Per-call opt-out via
  `surface_conflicts=false`; bounded by `max_conflicts` with an explicit
  "+N more" overflow line - never a silent drop.
- **Audited degraded retrieval.** An embedding-provider outage no longer takes
  recall down: the query proceeds lexical-only (BM25 + recency + importance)
  and the degradation is explicit everywhere - `retrieval_degraded` on
  `RecallResult` and `ContextResult`, in the recall audit event, and as a
  metric label for alerting. Degraded results are never cached. Keyed lookups
  never embed, so they never degrade.
- **Materiality-weighted retrieval decay.** A fact's retrieval half-life
  scales with `metadata.materiality` - low 7d / standard 30d / high 120d /
  critical 365d. Ranking-only: storage never decays; point-in-time (`as_of`)
  scoring honors the same weights; untagged facts keep the 30-day default.

## 0.3.4 - 2026-07-03

Supersedes 0.3.3, which never reached PyPI: its wheel force-include only
resolved from a repo checkout, and the release pipeline builds the wheel from
the sdist. A custom hatch build hook now resolves the vendored engine from
either location; the sdist ships the engine too, so `pip install` from source
works. (npm 0.3.3 was published before the failure; 0.3.4 restores lock-step.)

## 0.3.3 - 2026-07-03 (not published to PyPI)

Patch release. Fixes the flagship zero-setup path for installed users.

### Fixed
- **`LocalLiansClient` was broken on every installed wheel.** The local mode
  imports the service engine (`src.lians.*`), which only existed in the
  monorepo checkout - `pip install lians-sdk[local]` outside the repo failed
  with `ModuleNotFoundError: No module named 'src'` on first use. The wheel
  now vendors the engine as `lians_engine` (hatchling force-include) and the
  SDK aliases it to `src.lians` at import time; the `[local]` extra gained the
  engine's runtime dependencies (pydantic/-settings, cryptography, pgvector,
  numpy, fastapi, asyncpg). Verified end-to-end from a clean venv: add,
  recall, `recall_at`, `backtest_check`, crypto-shred erase with certificate,
  and audit-chain verify.
- C and Go SDK version strings had drifted (still 0.3.0); all versions are
  back in lock-step at 0.3.3.

## 0.3.2 - 2026-07-02

Patch release. Cross-language + packaging validation against a live server found
three more bugs; every SDK, the agent harness, and the MCP server now pass.

### Fixed
- **MCP server: `fact_history` and `list_conflicts` tools were broken.** The GET
  helper passed an empty `params={}`, which httpx uses to *replace* the query
  string - wiping queries baked into the request path, so two of the eight MCP
  tools 422'd against any server. (Ships in `lians-sdk[mcp]`.)
- **Java SDK could not reach the server at all.** Its `HttpClient` defaulted to
  HTTP/2; the cleartext HTTP/1.1 server rejected the h2c upgrade as "Invalid HTTP
  request received". Pinned `HttpClient.Version.HTTP_1_1`.
- Plugin `CLAUDE.md` TypeScript example called a non-existent `mem.add(...)` with
  camelCase keys; corrected to `mem.addMemory({ agent_id, event_time, ... })`.

### Validated (live server)
- All five SDKs - Python (sync + async), TypeScript, Go, Java, C.
- Agent harness (`LiansMemoryHarness`) recall-before / remember-after loop.
- MCP server over stdio - handshake, all 8 tools, remember/recall/fact_history.

## 0.3.1 - 2026-07-01

Patch release. Bug fixes found while limit-testing the live stack, plus the
governance-layer alignment (REFINES relation, vagueness admission filter).

### Fixed (correctness / security)
- **Cross-tenant subject-key isolation.** `subject_keys` was keyed by `subject_id`
  alone, so two namespaces sharing a `subject_id` shared one AES data-encryption
  key - and one tenant's GDPR erase crypto-shredded the other tenant's data.
  Now keyed by `(namespace, subject_id)` (migration 0019); the in-process DEK
  cache is namespace-scoped too.
- **`RATE_LIMIT_PER_MINUTE` is now honored.** The rate-limit middleware was added
  without its argument and silently pinned every deployment to 300/min.
- **`lians-sdk` is importable on a plain install.** `import lians` crashed unless
  the `[local]` extra was installed; `LocalLiansClient` is now imported lazily.
  (This is the reason for the 0.3.1 SDK republish - the 0.3.0 wheel is broken.)
- **`docker compose up` no longer crash-loops** on a stale `src.lian.main` module
  path in the Dockerfile CMD.

### Added
- **REFINES supersession relation** - a new fact that narrows/enriches an existing
  one closes the old validity window like SUPERSEDES but is audited as a narrowing.
  Harvested from the Lian Memory Governor vocabulary.
- **Vagueness admission pre-filter** - too-vague candidates are tagged and rejected
  in enforce mode.
- **`MemoryOut.score`** - recall responses now expose the hybrid relevance score.

## 0.3.0 - 2026-06-29

The production-readiness + competitive release. Everything below is on `master`
with full CI (12 checks across 5 languages + Postgres).

### Added
- **Agent memory harness** (`LiansMemoryHarness`) - drop-in recall-before /
  remember-after loop with compliance scoping.
- **Relationship graph** - `relate` / `unrelate` / `neighbors` / `path` (bitemporal,
  point-in-time), **graph-proximity (node-distance) reranking**, and `POST
  /v1/graph/extract` (rule-based text→edges, opt-in LLM).
- **MMR reranking** and `POST /v1/context` - token-budgeted, ready-to-inject block.
- **Three new SDKs - Go, Java, and C** - now five languages (Python, TypeScript,
  Go, Java, C). npm package renamed to `@lians-ai/lians`.
- **Exactly-once writes** - `Idempotency-Key` on `POST /v1/memories`; SDK
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
  stack of CI environment issues - CI is green for the first time.

> **Deployment note:** run the application as a **non-superuser, non-BYPASSRLS**
> Postgres role, or RLS (namespace + barrier isolation) is silently bypassed.

## 0.2.0 - 2026-06-27

Free tier, cloud pricing, GitHub org migration to `Lians-ai`.
