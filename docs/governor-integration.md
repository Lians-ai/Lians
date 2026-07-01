# Lian Memory Governor — Integration Plan

Last reviewed: 2026-07-01.

This document is the alignment artifact for folding the **Lian Memory Governor**
(`github.com/Ds6826/lian-memory-governor`) into the Lians product. It is written
so both maintainers can react to one page before any code moves.

The goal is a single decision: **the Governor becomes the governance/review
*surface* of Lians, backed by the Lians engine — not a second product with a
second engine.**

## TL;DR

- The Governor is a local-first MVP that reimplements ~80% of what the Lians
  engine already does (admission, supersession, point-in-time recall, audit
  chain), on SQLite, rule-based, no auth/crypto/tenancy.
- Its one genuinely differentiated asset is the **"Memory PR" governance model**:
  every durable-fact change is a *typed, explainable proposal*
  (`ADD / CONFIRM / SUPERSEDE / REFINE / QUARANTINE / IGNORE`) that a human or
  agent can review before it commits.
- We do **not** ship two engines or two go-to-markets. The Governor becomes a
  governance/review layer + portable JSON contract in front of the Lians engine.
  The SQLite engine survives as the zero-infra dev/demo tier and OSS on-ramp,
  not as a separately sold product.
- The unlock is a one-refactor change: extract a `MemoryStore` protocol from the
  Governor's `SQLiteStore`, then add a `LiansStore` that delegates to the
  production engine.

This is the concrete mechanism for the "Lian integration" already named in the
Governor's own `PLANS.md` (Phase 4) and `docs/roadmap.md`.

## Why not two engines / two products

The Governor and the Lians engine do substantially the same job:

| Capability | Governor (MVP) | Lians engine | Verdict |
|---|---|---|---|
| Admission / quarantine | `governor.decide()` + `policy.py` (rule-based) | `admission.py` + `admission_service.py` review queue (PII/PHI/MNPI/injection, blocked sources, audit-logged) | Lians ahead |
| Supersession / conflict | validity windows in `memory.commit_proposals()` | `supersession.py` — `CONFIRMS / SUPERSEDES / CONTRADICTS_SAME_TIME` + LLM adjudication | Lians ahead |
| Point-in-time recall | `recall_at()` | `recall_at` (MCP tool, cache key, audit reconstruction) | Both have it |
| Audit chain | SHA-256 append-only chain (`audit.py`) | `audit_chain.py` + `merkle_audit.py` | Lians ahead |
| Storage | SQLite | Postgres/pgvector, RLS, KMS/DEK encryption | Lians ahead |
| Matching | Jaccard token overlap (`similarity()`) | embeddings + ranking/MMR + LLM adjudication | Lians ahead |
| Surfaces | HTTP, CLI, MCP stdio, thin TS SDK | HTTP, MCP, Python/TS/Java/C/Go SDKs | Lians ahead |

Two consequences:

1. **Selling the Governor as a standalone engine is a downgrade** (no auth,
   no encryption, no tenant isolation, rule-based only) and duplicates
   maintenance.
2. **A two-person team cannot run two products or two go-to-markets.** Lians'
   positioning is "regulated AI memory" — institutional, examiner-grade. A
   human-in-the-loop review workflow for what enters agent memory *amplifies*
   that story; a developer-facing embeddable SDK is a different buyer and a
   distraction. So we lead with the governance surface and keep local-first as
   top-of-funnel, not a SKU.

## Target architecture

One governance layer, one contract, a pluggable store with two backends:

```
        Governor governance layer
   decide() proposals · audit · JSON contract · HTTP/CLI/MCP/SDK · review UI
                          │
                 ┌────────┴────────┐
           SQLiteStore         LiansStore  (new)
           local-first tier    delegates to the production Lians engine:
           zero infra          Postgres+pgvector · RLS · KMS · merkle audit
           dev / demo / OSS     supersession classifier · recall_at
```

- **Local-first tier** — today's SQLite engine. Zero infra, runs on a laptop or
  at the edge. Role: dev/demo/test harness and OSS on-ramp that lands users
  before they graduate to hosted Lians.
- **Governed production tier** — same Governor API, contract, and review
  workflow; `LiansStore` delegates storage, crypto, tenancy, supersession, and
  recall to the Lians engine. A project graduates local → production without
  changing a line of Governor-facing code.

## The linchpin: a pluggable `MemoryStore`

Today `store.py` just re-exports `SQLiteStore`, and `Memory.__init__` defaults to
it. But `Memory` already accepts an injected `store` and touches it only through
a fixed method surface. Extracting that surface into a protocol makes the engine
backend-agnostic.

Methods `Memory` currently calls on the store:

- `save_episode(episode)`
- `list_memories(namespace, user_id, agent_id)`
- `save_proposal(proposal)` / `get_proposal(id)` / `update_proposal(proposal)`
- `get_memory(id)` / `update_memory(memory)` / `save_memory(memory)`
- `connection.execute(...)` — used once in `_episode_event_time`

**One cleanup required:** `Memory._episode_event_time` reaches into
`store.connection.execute(...)`, which leaks SQLite specifics into the engine.
Promote it to a real store method (e.g. `get_episode_event_time(episode_id)`) so
the protocol is DB-agnostic. After that, `SQLiteStore` implements the protocol
unchanged and `LiansStore` implements the same methods against the Lians engine.

## Feature reconciliation

When `LiansStore` is active, the Governor's weaker internals defer to the Lians
engine rather than duplicating them:

| Governor concept | Maps to Lians | Action |
|---|---|---|
| `similarity()` (Jaccard) | embeddings + ranking/MMR | Route matching through Lians on the Lians backend |
| `decide()` ADD/CONFIRM/SUPERSEDE | `supersession` relations `CONFIRMS`/`SUPERSEDES`/`CONTRADICTS_SAME_TIME` | Align action vocabulary so proposals ↔ relations are interoperable |
| `REFINE` (narrowing) | *(gap — Lians has no explicit narrowing relation)* | Harvest into Lians supersession as a first-class relation |
| `IGNORE` (too vague) | *(pre-admission filter Lians may lack)* | Add vagueness pre-filter as an admission reason |
| SHA-256 chain | `audit_chain` + `merkle_audit` | Governor emits proposal events into the Lians audit chain |
| Quarantine policy | `admission.py` enforce mode | Governor quarantine defers to Lians admission on the Lians backend |

Net new to Lians from this work: the **REFINE relation**, the **vagueness
pre-filter**, and — the real prize — the **Memory-PR review surface**.

## Plan of record

**Phase 0 — align (this doc).** Agree the Governor is a layer on Lians, not a
second product. Confirm the primary surface is the governance/review console.

**Phase 1 — pluggable store (first PR).** Extract the `MemoryStore` protocol;
fix the `_episode_event_time` leak; `SQLiteStore` conforms unchanged; full test
suite stays green. Low-risk, reversible, unblocks everything.

**Phase 2 — `LiansStore` bridge.** Implement the protocol against the Lians
engine. Stand up the Governor's existing HTTP/MCP surface against real Lians
data as a demo. This is the artifact to react to together.

**Phase 3 — vocabulary + matching alignment.** Map proposal actions to Lians
supersession relations; add `REFINE`; on the Lians backend route `similarity()`
through embeddings + adjudication instead of Jaccard.

**Phase 4 — the Memory-PR review console.** The differentiated product surface,
and the compliance-native selling point. Neither codebase has a UI today.

**Phase 5 — harden the service API.** Auth on the HTTP surface (even local
shouldn't ship unauthenticated), contract versioning, make the JSON contract the
public shape for both tiers.

## Open decisions for the partner

1. **Repo shape:** does `lian-memory-governor` move into this monorepo
   (`agentmem/`) as the governance package, or stay a separate repo that
   depends on the Lians engine as a library? (Recommend: monorepo, to stop
   drift.)
2. **Naming:** "Governor" vs "Memory PR" vs a Lians-branded surface name. The
   contract and UI should carry one name.
3. **OSS boundary:** how much of the local-first tier is open source as an
   on-ramp, and where the hosted/regulated line sits.
4. **Primary surface confirmation:** enterprise governance/review console
   (recommended lead) vs local-first embeddable SDK (top-of-funnel only).

## Risks

- **Drift** if both engines keep their own storage — the whole plan exists to
  kill this; do not let the SQLite engine grow production features in parallel.
- **Contract skew** between tiers — one versioned JSON contract, tested against
  both backends, is mandatory.
- **Scope creep** into a second go-to-market — local-first stays top-of-funnel,
  not a sold product, until the governance surface is proven.
