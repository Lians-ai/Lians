# Changelog

All notable changes to Lians. Versions follow semver; SDKs are released in lock-step.

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
