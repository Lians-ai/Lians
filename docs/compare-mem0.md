# Lians vs mem0 — quality & access for regulated work

mem0 is a strong, popular general-purpose memory layer for AI agents. Lians is
built for a narrower, harder problem: memory in **regulated environments** —
financial institutions, healthcare, and legal firms — where a stale fact is not a
UX papercut but a compliance event, and where "what did the agent know, and when"
must be answerable to a regulator.

This document compares the two honestly on the two axes that matter for that
audience: **quality** (does the memory return correct, current, defensible facts?)
and **access** (who can read what, and is it provably controlled?).

> Scope note: mem0's surface evolves quickly. Claims below reflect mem0's public
> README/docs as of June 2026 (Apache-2.0 OSS core; v3 single-pass extraction;
> hosted platform at app.mem0.ai). Where mem0's managed platform adds a feature
> the OSS core lacks, we say so. Corrections welcome via PR.

---

## TL;DR

| Dimension | mem0 | Lians |
|---|---|---|
| Stale-fact handling | ADD-only accumulation (v3) — versions coexist | Bitemporal supersession — stale versions excluded at the DB layer |
| "What did we know on date X?" | No temporal reconstruction | `recall_at` / `snapshot` — exhaustive point-in-time state |
| Tamper-evidence | Not documented | SHA-256 hash chain (SEC 17a-4), `verify_chain()` |
| Right-to-erasure | Delete API; no audit-preserving proof | Per-subject AES-256-GCM crypto-shred; audit trail survives; erasure certificate |
| Access control | `user_id` filtering | Scoped keys + **RBAC roles** + PostgreSQL RLS barriers (DB-layer, CI-proven against a non-superuser role) |
| Lookahead-bias proof | None | `backtest_check` contamination report |
| Reranking | Hybrid search + reranker | Hybrid (BM25 + cosine + recency) + opt-in **MMR diversity** rerank |
| Context assembly | Returns a memory list | `/v1/context` — token-budgeted, ready-to-inject block (point-in-time + MMR aware) |
| Production hardening | Managed platform | **Idempotency keys** (exactly-once writes), SDK retry/backoff, `/livez`+`/readyz`, per-key rate limiting, **SIEM audit streaming** |
| Evaluation | Published LoCoMo/LongMemEval scores | Bundled judge-free harness (`answer_recall@k`) + the supersession invariant |
| Regulatory export | None documented | `compliance_report`, audit export (SEC/FINRA/CFTC) |
| Domain modeling | Generic | Finance / healthcare / legal adapters (entity normalization) |
| Language SDKs | Python, TypeScript (2) | Python, TypeScript, **Go, Java, C** (5) |

Where mem0 leads: breadth of out-of-the-box *framework* integrations (LangChain,
CrewAI, …), a polished hosted onboarding, a browser extension, and *published*
general-chat benchmark scores (LoCoMo / LongMemEval). Lians ships the eval harness
to run those benchmarks (`agentmem/benchmarks/memory_eval.py`) but doesn't lead with
a single recall number — its thesis is correct, current, auditable recall, not
recall at any cost. If you are building a consumer assistant, mem0's breadth is real
value. If you are building for a bank, hospital, or law firm, the rows above are the
ones that get you through procurement and audit.

On *language* reach, though, Lians is ahead: mem0 ships Python and TypeScript;
Lians ships **Python, TypeScript, Go, Java, and C** — the two of those that matter
most to regulated buyers (the JVM that runs bank/insurer risk systems, and native
C for low-latency and embedded) are exactly the ones mem0 lacks.

---

## 1. Quality — correctness of recalled context

### 1.1 The stale-fact problem

mem0 v3 uses an **ADD-only** accumulation model: new observations are appended,
and prior observations are not overwritten or deleted. This is excellent for
preserving conversational nuance, but in a domain where facts *revise* — earnings
guidance, a Fed rate decision, a medication dose, a damages estimate — it means
the store holds every version, and semantic recall can surface an outdated one
ranked alongside the current one. The LLM then reasons over contaminated context.

Lians models the revision explicitly. Each fact carries:

- `event_time` — when the fact became true (business time)
- `valid_from` / `valid_to` — the window during which the system believed it

When a new fact supersedes an old one (same keyed entity + metric), the old fact's
`valid_to` is closed. Present-time `recall` filters on `valid_to IS NULL`, so the
**stale version never reaches the model**. The benchmark in
[`benchmark.md`](./benchmark.md) measures this directly: across a 5-revision chain,
Lians returned **0 stale facts in the top-5**; an ADD-only baseline returned 4/4.

### 1.2 Point-in-time reconstruction

Because mem0 has no validity interval per fact, it cannot answer "what did the
agent know on 2025-03-14, ignoring everything learned since?" Lians answers it two
ways:

- `recall_at(query, as_of=T)` — ranked, point-in-time relevant recall
- `snapshot(agent_id, as_of=T)` — **exhaustive**: every fact valid at T, no
  relevance filter, ordered by event time. This is what an examiner actually asks
  for — the complete state, not the top 5.

### 1.3 Conflicts as first-class objects

When two sources disagree at the same event time (an ISIN-tagged fact vs a
ticker-tagged fact; two clinicians' notes), Lians does not silently pick one. It
raises a **conflict** that can be listed and resolved by a human, and emits a
`memory.conflict` webhook. mem0's accumulation model has no equivalent
adjudication surface.

### 1.4 Domain-aware normalization

Lians ships finance/healthcare/legal adapters so supersession keys on the *same
real-world entity* even when the surface form differs:

- finance — `Apple Inc.`, ISIN `US0378331005`, CUSIP `037833100`, and `AAPL` all
  resolve to one series
- healthcare — ICD-10, NPI, and medication-name normalization
- legal — matter-ID, jurisdiction, and claim-type normalization

A generic embedding store treats these as different strings and fails to supersede.

---

## 2. Access — who can read what, provably

### 2.1 Multi-tenancy vs. information barriers

mem0 separates data by `user_id` (and session/agent ids) — a filter applied in the
query path. That is fine for "keep Alice's memories out of Bob's chat." It is not
an **information barrier**: a Chinese wall between a bank's M&A and trading desks,
a hospital's care teams, or a law firm's matter teams, where the requirement is
that one side *cannot* read the other's data even by mistake or by a bug in the
application layer.

Lians enforces isolation at **PostgreSQL Row-Level Security**. Each request sets a
transaction-scoped `app.current_namespace`, and the `barrier_group` policy is
evaluated by the database, not the application. A coding error in the API layer
cannot leak across the wall, because the wall is below the API layer. This maps
directly to:

- **Finance** — SEC/FINRA information-barrier requirements between desks
- **Legal** — ABA Model Rules 1.7 / 1.9 conflict walls per matter
- **Healthcare** — HIPAA §164.312(a)(1) access control per care team

### 2.2 Scoped credentials

Lians API keys carry explicit scopes (`read`, `write`, admin) checked on every
request (`AuthContext.require(scope)`), and keys are stored only as SHA-256 hashes
and individually revocable. mem0's OSS auth is comparatively coarse (API key /
session) without documented per-scope enforcement or RLS-backed tenancy.

### 2.3 Encryption and erasure as access controls

The strongest access control is cryptographic. Lians encrypts PII content with a
**per-subject AES-256-GCM key**. "Erasing" a data subject destroys that one key —
the content becomes unrecoverable for everyone, instantly, while the SHA-256
content hashes remain in the audit chain so the erasure itself is provable. The
SDK returns an **erasure certificate** (stable id + preserved hashes) you can file
with a supervisory authority.

mem0 documents deletion of memories but not encryption at rest, per-subject keys,
or an audit-preserving erasure proof. "We deleted the row" and "we can prove the
content is cryptographically unrecoverable while the audit trail is intact" are
different assurances, and only the second survives a GDPR/HIPAA review.

---

## 3. By vertical

### Finance
- **Need:** SEC 17a-4 tamper-evident records, FINRA 4511 retention, MiFID II
  point-in-time, desk-level information barriers, backtests provably free of
  lookahead bias.
- **Lians:** hash chain + `verify_chain`, append-only `event_log`, `recall_at`,
  RLS barriers, `backtest_check`. Ticker/ISIN/CUSIP normalization.
- **mem0 gap:** no temporal model, no tamper-evidence, no barrier enforcement, no
  contamination check.

### Healthcare
- **Need:** HIPAA §164.312 safeguards — encryption, integrity, access control,
  transmission security; subject-level erasure; care-team segregation.
- **Lians:** per-subject AES-256-GCM, hash-chain integrity, RLS care-team
  barriers, crypto-shred keyed on `patient_id`, air-gap mode. ICD-10/NPI
  normalization. (A BAA is required before processing real PHI.)
- **mem0 gap:** no documented encryption-at-rest, no per-subject key model, no
  HIPAA safeguard mapping.

### Legal
- **Need:** FRCP Rule 34 eDiscovery (reproduce knowledge before a privilege
  cutoff), ABA conflict walls, chain-of-custody, client-matter destruction.
- **Lians:** `recall_at(as_of=privilege_date)`, RLS matter-team walls, hash-chain
  custody, crypto-shred keyed on `matter_id`, expert-witness contamination check.
- **mem0 gap:** no privilege-cutoff reconstruction, no matter-level walls, no
  custody proof.

---

## 4. Where mem0 is the better choice

Be fair about it. Pick mem0 if you want the widest set of turnkey framework
integrations and a browser extension today, you are optimizing general-assistant
recall quality on conversational benchmarks, your data is not regulated, and you
value a zero-ops hosted platform with a large community over compliance depth.

Pick Lians if a wrong-because-stale answer is a compliance event, an auditor or
examiner will ask "what did the agent know on date X," you must prove records
weren't altered and that erased data is unrecoverable, or you need barriers
enforced below the application layer.

---

## 5. Migrating

If you are already on mem0, see [migrate-from-mem0.md](./migrate-from-mem0.md).
The mental model shift is small: keep calling `add` / `recall`, but start passing
`event_time` (the business time a fact became true) so the bitemporal guarantees
switch on. Everything in §1–§2 follows from that one field.
