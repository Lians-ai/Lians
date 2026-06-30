# Lians vs Zep / Graphiti — what to learn from a temporal knowledge graph

[Zep](https://www.getzep.com) is a managed agent-memory platform; its open-source
engine is [Graphiti](https://github.com/getzep/graphiti), a **bitemporal knowledge
graph**. Graphiti and Lians independently arrived at the same core insight —
*facts change over time, so memory must be temporal* — but took different routes:
Graphiti models the world as **entities and relationships** (a graph) and leans on
LLM extraction; Lians models **atomic facts** with deterministic supersession and a
compliance spine (audit chain, crypto-shred, information barriers).

This document compares the two honestly and extracts the parts of Graphiti's
design worth adopting — on our terms, for regulated work.

> Scope note: reflects Graphiti's public repo/docs and Zep's positioning as of
> June 2026. Zep CE (the self-hostable graph) is deprecated; production Zep is
> cloud-only. Graphiti remains Apache-2.0 and self-hostable.

---

## TL;DR

| Dimension | Zep / Graphiti | Lians |
|---|---|---|
| Temporal model | Bitemporal **edges** (`valid_at`/`invalid_at`) | Bitemporal **facts** (`event_time` + `valid_from/valid_to`) |
| Data shape | Entity–relationship **graph** (triplets) | Atomic facts + structured metadata |
| Retrieval | Semantic + BM25 + **graph traversal** + node-distance rerank | Semantic + BM25 + recency + validity gate + graph traversal + node-distance + **MMR** rerank |
| Text → graph | LLM extraction (always) | `/v1/graph/extract` — **rule-based default** (deterministic, auditable), LLM opt-in |
| Context assembly | `memory.context` block | `/v1/context` — token-budgeted, ready-to-inject, point-in-time + MMR aware |
| Fact updates | **LLM-extracted**, dedup, contradiction → invalidate edge | **Deterministic** keyed supersession; LLM only as optional adjudicator |
| Entity model | LLM entity nodes + evolving summaries + communities | Entity *normalization* (ISIN/CUSIP/ICD-10); explicit + extracted edges (no communities/summaries yet) |
| Tamper-evidence | Episode provenance only | SHA-256 hash chain (SEC 17a-4), `verify_chain` |
| Right-to-erasure | Not a feature | Per-subject crypto-shred + erasure certificate |
| Access control | None in Graphiti; Zep Cloud only | Scoped keys + **RBAC roles** + PostgreSQL RLS barriers (DB-layer, CI-proven) |
| Audit egress | — | **SIEM streaming** (Splunk/Datadog/Elastic) + signed-webhook events + export |
| Production | Managed cloud only | Self-host: idempotency keys, SDK retries, `/livez`+`/readyz`, rate limiting, air-gap |
| Backend | Neo4j / FalkorDB / Neptune (graph DB) | Postgres 16 + pgvector (no extra infra) |
| Determinism | Extraction-quality dependent | Reproducible; same input → same supersession |
| Language SDKs | Python, TypeScript, Go (3) | Python, TypeScript, Go, **Java, C** (5) |

**Where Zep/Graphiti leads:** the *depth* of the graph — automatic LLM entity/edge
extraction from messy unstructured text, community detection, and evolving entity
summaries. Lians now has the relationship graph itself (edges, N-hop traversal,
point-in-time `path`, node-distance reranking — see §4), but writes edges
explicitly/deterministically rather than inferring them, and doesn't yet do
communities or auto-summaries.

**Where Lians leads:** everything a regulator asks about — tamper-evident audit,
provable erasure, DB-layer barriers, deterministic and reproducible updates, and
zero extra infrastructure (no graph DB to run, secure, and certify) — **plus
language reach**: Zep ships Python, TypeScript, and Go; Lians ships those three
*and* Java and C, the two that regulated buyers (JVM risk systems, native
low-latency/embedded) most need and that neither Zep nor mem0 offers.

---

## 1. The idea worth stealing: a relationship graph

Lians stores facts as rows. That's perfect for "what is AAPL's current EPS" but
weak for the questions that are *inherently relational* — and several of those are
core compliance requirements, not nice-to-haves:

- **Legal — conflict-of-interest checks.** "Does any attorney on this matter have
  a relationship (prior representation, financial interest, family) to an adverse
  party?" is a graph reachability question. ABA Rules 1.7/1.9 *are* a graph query.
- **Finance — related-party & beneficial-ownership.** "Is this counterparty
  connected, within N hops of ownership or control, to a restricted entity?" drives
  related-party-transaction disclosure (SEC) and AML/KYC beneficial-ownership rules.
- **Healthcare — care networks & referral loops.** "Which providers and facilities
  are in this patient's care graph?" and anti-kickback referral-pattern analysis.

Graphiti answers these natively because it stores **Entity → relationship → Entity**
edges. Lians should too — but built on our existing strengths rather than bolting
on a graph database.

### How we adopt it without a graph DB

We already have the hard parts Graphiti needs and Postgres can express:

- **Bitemporality** — Graphiti's edge `valid_at`/`invalid_at` is exactly our
  `valid_from`/`valid_to`. A `relationships` table inherits our temporal model for
  free, so point-in-time graph queries (`as_of`) and the audit chain extend to edges.
- **N-hop traversal** — PostgreSQL **recursive CTEs** do bounded traversal without
  Neo4j. We avoid running, securing, and compliance-certifying a second datastore.
- **Determinism** — edges can be written *explicitly* (structured ingestion:
  ownership filings, care-team rosters, matter-party lists) so the graph is
  reproducible and audit-grade, with LLM extraction as an *optional* enrichment
  path — the same posture we already take for supersession.
- **Barriers & erasure** — edges carry `namespace` + `barrier_group` and reference
  subjects, so RLS isolation and crypto-shred apply unchanged.

This neutralizes Zep's main differentiator while keeping our compliance posture and
single-datastore simplicity.

---

## 2. Smaller inspirations, ranked

1. **Bitemporal relationship edges (graph layer).** Highest value; see §1. A
   `relationships` table + `/v1/graph/*` endpoints + recursive-CTE traversal +
   point-in-time edge queries. Verticals: COI (legal), related-party (finance),
   care network (healthcare).
2. **Graph-proximity reranking.** Once edges exist, boost recall for facts about
   entities near the query entity in the graph — Graphiti's node-distance rerank.
   Cheap to add to our existing ranker; improves precision on connected topics.
3. **Custom ontology / entity & relationship types.** Extend domain adapters to
   declare *entity types* and *allowed relationship types* (e.g. finance:
   `Issuer`, `Fund`, `Person` with `owns`, `controls`, `advises`). Keeps extraction
   constrained and auditable — closer to Graphiti's "prescribed ontology" than its
   "learned" one, which suits regulated determinism.
4. **Episode grouping with shared provenance.** Graphiti's "episode" = one
   ingestion unit that every derived fact/edge points back to. We have `event_log`;
   adding an `episode_id` that groups the facts/edges from one document or message
   batch strengthens lineage ("which filing produced this ownership edge?").
5. **Evolving entity summaries.** Graphiti keeps a rolling per-entity summary.
   Useful but LLM-dependent and non-deterministic — lower priority for us; if
   added, mark summaries clearly as *derived, non-authoritative* so they never
   contaminate the audit-grade fact store.

Deliberately **not** adopting: a mandatory graph database, fully *learned*
ontology (non-reproducible), and LLM extraction as the *only* write path. These
trade away the determinism and infra-simplicity that make Lians defensible.

---

## 3. What we should keep that Graphiti lacks

Graphiti is an engine, not a product — by its own docs it has **no access control,
no multi-tenancy, no audit logs, and no compliance features** beyond episode
provenance; Zep Cloud supplies those. Lians' differentiators are exactly that gap:
the SEC 17a-4 hash chain, crypto-shred erasure with surviving audit, DB-layer
information barriers, conflict-review queue, backtest-contamination proof, and
deterministic supersession. A graph layer should extend these, never dilute them —
every edge belongs in the audit chain and inside the barrier, just like every fact.

---

## 4. What we shipped (items 1 & 2)

The **bitemporal relationship graph** and **graph-proximity reranking** are now in
the codebase as an additive layer — no new datastore, full compliance spine:

- `relationships` table — same temporal (`valid_from`/`valid_to`/`event_time`),
  audit, barrier, and subject columns as `memories`. Migration `0012_relationships`
  enables the same RLS barrier policy on edges.
- Writes go through the audit hash chain (`op="relate"` / `op="unrelate"`) and fire
  the `relationship.invalidated` webhook. `exclusive=True` gives deterministic
  contradiction-style invalidation (e.g. a person's current employer).
- `POST /v1/graph/relate` · `POST /v1/graph/unrelate` · `GET /v1/graph/neighbors`
  (N-hop) · `GET /v1/graph/path` (the COI / related-party reachability query) —
  **all point-in-time capable via `as_of`**. Traversal runs in-process over the
  namespace's edges (recursive SQL is a later optimization, not an API change).
- **Graph-proximity reranking** (`recall_near` / `_near_entity` filter): recall
  results about entities near the query's anchor entity get a node-distance boost,
  Graphiti-style — without displacing strong semantic matches.
- SDK (`relate`/`unrelate`/`neighbors`/`path`/`recall_near` on the Python clients) +
  harness helpers + `entity normalization` reuse so `Apple Inc.`/`AAPL`/ISIN
  collapse to one node when `normalize=True`.

This gives Lians graph reasoning **and** keeps the compliance spine — the one thing
neither Graphiti nor Zep Cloud offers in the open.

### Also shipped: text → graph extraction
`POST /v1/graph/extract` turns unstructured text into edges — Graphiti's "build
the graph for me" convenience — but **rule-based and deterministic by default**
(auditable, reproducible, no model), with opt-in LLM extraction that falls back to
rules. Every extracted edge lands in the audit chain and inside the barrier. We
deliberately keep determinism the default rather than depending on an LLM per write.

### Still open (lower priority)
Custom entity/relationship *ontology* types, episode grouping with shared
provenance, community detection, and (clearly-marked, non-authoritative) evolving
entity summaries remain future work.
