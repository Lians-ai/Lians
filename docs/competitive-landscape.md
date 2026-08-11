# Lians Competitive Landscape

Last reviewed: 2026-08-01 against current official product documentation.

This document compares Lians with the current agent-memory landscape. It is not a
feature-by-feature takedown. The goal is to keep positioning honest: where others
are stronger, where Lians is stronger, and which market category Lians should
avoid fighting directly.

## Short version

The memory market is splitting into five lanes:

| Lane | Representative products | Primary buyer/job | Lians posture |
|---|---|---|---|
| Developer memory APIs | mem0, Zep, Supermemory, Hindsight, Honcho | Add long-term memory to agents quickly | Compete only when audit, erasure, barriers, and point-in-time correctness matter |
| Temporal / graph memory | Zep/Graphiti, Hindsight, Supermemory, MemoryLake | Better long-horizon recall and relationship reasoning | Respect their graph/context strengths; win on regulated evidence and deterministic controls |
| Personal memory passport | MemoryLake, Supermemory personal app, OpenMemory-style tools | One user memory across many AIs | Mostly not Lians' lane |
| Self-improving agents | Letta, Honcho, Hindsight | Agents that learn, reflect, dream, and adapt | Integrate or coexist; do not position Lians as an agent identity product |
| Answerable AI decision systems | Lians | Teams that must record, govern, verify, and improve consequential AI decisions | Differentiation target; lead with the complete proof loop, not another infrastructure layer |

The simplest message:

> Temporal memory is now table stakes. Lians is differentiated when one system
> can show what AI knew, why it acted, who authorized it, and what changed next,
> then bind that answer to a receipt another team can verify offline.

Do not position ordinary temporal memory, provenance, or audit logging as unique.
Graphiti is bitemporal, Mem0 documents temporal/as-of reasoning and history,
Hindsight documents query-time temporal recall plus audit and OpenTelemetry, and
Supermemory documents versioning and a temporal graph.

## Comparison matrix

| Product | What they are best at | Where they pressure Lians | Where Lians should win |
|---|---|---|---|
| **Hindsight** | Structured agent memory with retain/recall/reflect, memory banks, observations, temporal/entity-aware retrieval, broad integrations | Strongest technical pressure on memory quality and reasoning; has temporal, graph, keyword, and semantic retrieval plus observation consolidation | Lians is more procurement/compliance-native: audit hash chain, crypto-shred certificates, RLS barriers, backtest contamination checks, institutional deployment story |
| **MemoryLake** | Personal memory passport across many AIs, multimodal memory, open data sources, conflict detection, versioning/provenance claims | Strong broad-market story: user-owned portable memory, multimodal ingestion, built-in datasets, security claims, polished consumer/enterprise framing | Lians should avoid consumer-passport competition and win on private regulated deployments, deterministic supersession, DB-layer barriers, and examiner-grade audit reconstruction |
| **Letta** | Persistent agents whose identity, skills, context, and capabilities evolve; MemGPT lineage; sleep-time compute and git-tracked context | Stronger agent-product and continual-learning narrative; users buy an agent, not just memory infrastructure | Lians should be infrastructure under/alongside agents, not a digital-person product; stronger for regulated memory custody and evidentiary controls |
| **Supermemory** | Context cloud: memory, RAG, profiles, connectors, extractors, memory graph, personal app, low-latency retrieval, self-host/air-gap options | Strongest commercial/product pressure: broad connectors, RAG + memory bundle, graph, consumer plugins, enterprise deployment, SOC/HIPAA claims | Lians must be narrower and deeper: point-in-time correctness, audit survival, crypto erasure proof, RLS barriers, domain adapters, institutional proof kits |
| **Honcho** | Memory that reasons, continual learning, token savings, peer/session modeling, background dreaming, fast curated context | Strong reasoning-memory UX and benchmark posture; could win stateful app developers quickly | Lians wins where automatic reasoning must be controlled, audited, isolated, and reconstructed |
| **mem0** | Managed memory layer, fast setup, user/agent/session memory, broad integrations, production platform | Better developer onboarding, community, hosted convenience, benchmark/cost claims | Lians wins on stale-fact suppression, bitemporality, erasure certificates, audit-chain verification, and information barriers |
| **Zep / Graphiti** | Temporal knowledge graph and governed Context Lake serving prompt-ready context; strong graph-memory architecture | Strongest graph-memory incumbent; Zep has enterprise-scale hosted posture | Lians wins when self-hosted compliance controls, cryptographic erasure, audit hash chains, and deterministic supersession are required |

## Named competitors

### Hindsight

Hindsight is one of the closest conceptual competitors. It frames memory around
three operations: `retain`, `recall`, and `reflect`. Its docs describe memory
banks, world facts, experience facts, mental models, observations, and four-way
retrieval across semantic, keyword/BM25, graph, and temporal strategies.

Hindsight is strong where the buyer wants agents that can reason over long-lived
memory, consolidate raw facts into observations, and use memory as a reasoning
substrate. Its published paper reports large gains on LongMemEval and LoCoMo, so
Lians should not hand-wave its memory-quality story away.

**Lians response:** do not claim "we have memory and they do not." Say Hindsight
is an agent-memory reasoning system; Lians is the regulated memory control plane.
The winning comparison is not recall quality alone, but whether the memory layer
can produce an examiner-ready reconstruction, prove erasure, enforce barriers
below the application, and show why a stale fact could not influence a decision.

Source: Hindsight overview and paper link at `https://hindsight.vectorize.io/`.

### MemoryLake

MemoryLake positions itself as a "memory passport" that follows a user across
ChatGPT, Claude, Qwen, OpenClaw, agents, and sessions. Its site emphasizes
portable encrypted memory, many memory types, multimodal files, conflict
detection, provenance, versioning, open data sources, and broad personal/enterprise
use cases.

MemoryLake is dangerous because it talks about many things Lians also wants to
talk about: conflicts, traceability, security, version history, audit trails,
finance data, clinical trials, patents, and healthcare/legal/finance use cases.
But the center of gravity is different: MemoryLake is a cross-AI personal memory
passport and broad context substrate.

**Lians response:** do not compete on "one memory across every AI" or consumer
memory ownership. Compete on regulated deployment depth: RLS barriers, per-subject
crypto-shred certificates, audit-chain verification, deterministic point-in-time
recall, and institutional proof artifacts.

Source: MemoryLake homepage at `https://www.memorylake.ai/en`.

### Letta

Letta is best understood as a persistent-agent company, not merely a memory API.
Its site describes "digital people" and agents whose memory, identity, and
capabilities improve with experience. Letta Code emphasizes persistent custom
agents, sleep-time compute, git-tracked context, channels, scheduled tasks, and
multi-model memory transfer.

Letta is strong where the buyer wants an agent that learns and evolves. Its
MemGPT lineage gives it research credibility.

**Lians response:** Letta can be a consumer of Lians, not only a competitor. If a
hospital, bank, or law firm wants self-improving agents, they still need a
regulated memory substrate that can prove custody, erasure, access, and
point-in-time correctness. Lians should avoid "digital people" positioning.

Source: Letta homepage and Letta Code page at `https://www.letta.com/`.

### Supermemory

Supermemory calls itself a context cloud for agents. It bundles memory, RAG,
profiles, connectors, extractors, a memory graph, personal app, MCP/plugins, and
enterprise deployment options. Its site claims sub-300ms retrieval, broad
connectors, self-hosting/air-gap, SOC 2, HIPAA/BAA options, and strong benchmark
positioning.

This is one of the strongest commercial competitors because it has a broader
product surface than Lians: connectors, document extraction, personal memory,
developer API, plugins, RAG, graph, and enterprise packaging.

**Lians response:** Lians should not try to match every connector immediately.
The sharper wedge is regulated correctness: no stale/future facts in decisions,
audit-chain verification, erasure certificates, RLS barriers, and domain-specific
adapters for finance/healthcare/legal. Supermemory is broad context
infrastructure; Lians is evidence-grade memory infrastructure.

Source: Supermemory homepage at `https://supermemory.ai/`.

### Honcho

Honcho positions itself as "memory that reasons." It focuses on continual
learning for stateful agents, token savings, background dreaming, peer/session
modeling, and curated `context()` calls. It emphasizes automatic reasoning over
messages and benchmark performance on LongMem, LoCoMo, and BEAM.

Honcho will appeal to developers who want better user modeling and efficient
context without thinking about memory internals.

**Lians response:** automatic reasoning is powerful but can become a compliance
problem when the buyer must know which fact, source, and validity window
influenced a regulated output. Lians should pitch as the controlled memory layer
for environments where reasoning over memory must be traceable and reviewable.

Source: Honcho homepage at `https://honcho.dev/`.

## Adjacent incumbents

### mem0

mem0 is the developer-friendly managed memory platform. Its docs describe a
managed memory layer for AI agents, persistent user/agent/session memories,
hosted vector/reranker infrastructure, MCP, integrations, audit logs, workspace
governance, and production readiness.

**Lians response:** mem0 is often the right choice for general product teams that
want fast setup and personalization. Lians is the right choice when stale facts,
point-in-time reconstruction, erasure proof, and access barriers matter.

Source: mem0 docs at `https://docs.mem0.ai/overview`.

### Zep / Graphiti

Zep is the graph-memory incumbent. Its docs describe agent memory built from chat,
business data, documents, and JSON into a temporal Context Graph, with a Context
Lake serving token-efficient prompt-ready context at sub-200ms retrieval.

**Lians response:** Zep's graph and context-serving story is strong. Lians should
not pretend graph memory is unimportant. Lians should instead claim the regulated
control layer: deterministic supersession, audit-chain verification, erasure
certificates, self-hosted RLS barriers, and compliance reports.

Source: Zep docs at `https://help.getzep.com/`.

## Strategic implications

### What Lians should not chase first

- Consumer memory passport UX.
- Browser extensions and personal memory apps.
- Broad document extraction and connector marketplaces.
- Digital-person identity and agent personality.
- Benchmark wars on general conversational memory alone.

These are real markets, but they dilute Lians' institutional wedge.

### What Lians should build next

1. **Decision Evidence Envelope export.** Bind decision ID, trace/span IDs,
   model/prompt/tool/code versions, both temporal cutoffs, included/excluded
   sources, policy/admission results, leak count, receipt hash, audit root, and
   erasure state. Export it through OTLP/OpenInference to the observability tools
   customers already operate.
2. **Live Evidence Explorer.** Decision → receipt → memory/source navigation,
   then-vs-now replay, excluded future facts, chain verification, approvals, and
   signed JSON/PDF/CSV/DSSE exports.
3. **Independent current benchmark.** Run current Mem0, Graphiti, Hindsight, and
   Supermemory with public fixtures, configs, container digests, latency, token,
   and cost evidence. The existing regulated eval is a dated product harness, not
   independent general-product validation.
4. **Compatibility Lab v2.** Turn the homelab into a one-command customer sample
   proof with redaction/synthetic boundaries, reconstruction, receipt, leak,
   security, and telemetry checks plus a signed pack and SBOM.
5. **Enterprise trust pack.** OIDC/SAML/SCIM, API-key ABAC, BYOK/KMS, externally
   anchored WORM evidence, signature rotation, HA/restore proof, and templates for
   governance and sandbox channels.

### Primary sources for the August 2026 review

- Graphiti bitemporal graph and episode provenance: <https://github.com/getzep/graphiti>
- Zep security posture: <https://help.getzep.com/security-compliance>
- Mem0 temporal reasoning: <https://docs.mem0.ai/platform/features/temporal-reasoning>
- Hindsight recall API and release notes: <https://hindsight.vectorize.io/developer/api/recall>
- Supermemory graph memory and versioning: <https://supermemory.ai/docs/concepts/graph-memory>
- Letta memory blocks: <https://docs.letta.com/guides/core-concepts/memory/memory-blocks>

## Positioning line

Use this in sales and docs:

> Lians is the decision-evidence layer for teams that must reconstruct what an AI
> could know at both cutoffs, prove what was excluded, and verify the resulting
> receipt outside the runtime that produced it.
