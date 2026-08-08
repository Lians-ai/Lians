<p align="center">
  <a href="https://github.com/Lians-ai/Lians">
    <img src="docs/assets/logo-blue.png" width="420" alt="Lians">
  </a>
</p>

<p align="center">
  <a href="https://www.lians.ai/">Website</a>
  -
  <a href="https://github.com/Lians-ai/Lians/tree/master/docs">Docs</a>
  -
  <a href="docs/install.md">Install</a>
  -
  <a href="https://github.com/Lians-ai/Lians#self-hosted-quickstart">Quickstart</a>
  -
  <a href="https://github.com/Lians-ai/Lians/stargazers"><strong>Star Lians</strong></a>
</p>

<p align="center">
  <a href="https://pypi.org/project/lians-sdk">
    <img src="https://img.shields.io/pypi/v/lians-sdk?color=%2334D058&label=pypi%20package" alt="PyPI version">
  </a>
  <a href="https://pypi.org/project/lians-sdk">
    <img src="https://img.shields.io/pypi/dm/lians-sdk?label=pypi%20downloads" alt="PyPI downloads">
  </a>
  <a href="https://github.com/Lians-ai/Lians">
    <img src="https://img.shields.io/github/commit-activity/m/Lians-ai/Lians/master?style=flat-square" alt="GitHub commit activity">
  </a>
  <a href="https://www.npmjs.com/package/@lians-ai/lians">
    <img src="https://img.shields.io/npm/v/%40lians-ai%2Flians?label=npm" alt="npm version">
  </a>
  <a href="https://registry.modelcontextprotocol.io/v0/servers/io.github.ebeirne%2Flians/versions/latest">
    <img src="https://img.shields.io/badge/MCP-Official%20Registry-blueviolet" alt="MCP Official Registry">
  </a>
  <a href="LICENSE">
    <img src="https://img.shields.io/badge/License-Apache%202.0-blue.svg" alt="License: Apache 2.0">
  </a>
</p>

<p align="center">
  <a href="docs/benchmark.md"><strong>Benchmark: 0 stale facts in top-5 vs mem0-style recall's 4/4, plus 100% supersession accuracy</strong></a>
  <br />
  <a href="docs/benchmarks/riad-1.md"><strong>RIAD-1: decision reconstruction benchmark</strong></a>
  · <a href="https://github.com/Lians-ai/Lians/actions/workflows/riad-1.yml"><strong>CI receipts</strong></a>
</p>

---

[Lians](https://github.com/Lians-ai/Lians) is **provider-neutral memory and
governed improvement infrastructure for production AI agents**. Connect it
through MCP, HTTP, Python/TypeScript SDKs, or OpenTelemetry to systems built on
Codex, Claude, Gemini, and other model or agent runtimes. Lians keeps recalled
facts current, bounds ordinary recall context, supports exact-token context
compilation, measures quality, latency, tokens, cost, and outcomes, and requires
protected-quality gates before an optimized agent version can advance toward
production.

The measurable claim is workflow-specific: Lians can reduce the context an
agent reads while preserving a defined quality threshold. In the published
1,540-question LOCOMO run, top-50 recall scored 90.0% with 2,656 mean context
tokens versus 18,218 for the full-conversation baseline (85.4% fewer). At
top-200, it scored 92.9% with 43.6% fewer context tokens. These are benchmark
results, not a guarantee for every model, prompt, or deployment. See the
[token-efficiency report](agentmem/docs/benchmarks/locomo-token-efficiency-2026-07-10.md)
and the [Codex/PostgreSQL validation](docs/benchmarks/codex-mcp-local-2026-08-08.md).

For the product target of 85% more same-budget usage, the economic threshold is
1.85x comparable tasks, which requires at least 45.95% lower measured per-task
cost after protected quality passes. One signed-in Codex end-to-end repeat
reached 2.10x same-budget usage. A signed-in Claude manual context-isolation
repeat reached 10.11x with tools and MCP disabled, so it is an upper bound on
context savings rather than installed-plugin evidence; both answer pairs were
correct. Gemini's MCP/configuration path is validated but its model A/B remains
pending credentials. See the
[cross-provider usage-extension report](docs/benchmarks/provider-usage-extension-2026-08-08.md).

Decision evidence remains the trust boundary: Lians records what an agent saw,
was permitted to use, and did, then reconstructs that boundary when sources,
policies, permissions, tools, or models change.

The platform closes one control loop on a shared, append-only record layer:

- **Universal Recorder** — normalizes native Lians, OpenTelemetry GenAI, MCP, and A2A events with privacy-safe capture and stable correlation.
- **Decision Receipt** — freezes the declared evidence boundary, completeness disclosure, acting identity, policy, and cryptographic integrity material.
- **Runtime Gate** — verifies trusted receipts and binds the real principal, scopes, information barrier, policy version, and immutable approval quorum before action.
- **Investigator** — reconstructs a decision, separates direct evidence from reachability and estimates, prioritizes blast radius, assigns remediation, and attests closure.
- **Evidence graph and memory** — keeps point-in-time agent knowledge, provenance, supersession, information barriers, and crypto-shred erasure connected to consequential outcomes.

Memory is what an agent knew. Recorder events are what the AI system did. A
Decision Receipt turns that boundary into portable evidence; Gate and Investigator
make the evidence operational before and after an action is disputed.

| | Library | Self-Hosted Server | Cloud |
|---|---|---|---|
| **Best for** | Testing, prototyping | Regulated teams, private deployments | Zero-ops production (early access) |
| **Setup** | `pip install lians-sdk[local]` | `docker compose up --build` | `pip install lians-sdk` + API key |
| **Database** | SQLite (zero setup) | Postgres 16 + pgvector | Managed |
| **Audit chain** | Yes | Yes | Yes |
| **Crypto-shred erasure** | Yes | Yes | Yes |
| **Information barriers** | Local checks | PostgreSQL RLS | Managed policy |
| **Air-gap capable** | No | Yes | No |

---

## Decision Receipt v0.1

Every consequential AI action can produce one independently verifiable receipt. The receipt binds the decision to its agent, model/version, instruction and input/output hashes, cited sources and validity windows, policy evaluation, authorization context, tool results, human-review status, transaction-time boundary, and audit-chain state. Missing evidence is visible in the receipt's grade; it is never silently treated as complete.

- [Open JSON Schema](specs/decision-receipt/v0.1/schema.json)
- [Canonicalization, signing, and trust model](specs/decision-receipt/v0.1/README.md)
- [Portable fixtures and independent conformance runner](specs/decision-receipt/v0.1/conformance/README.md)
- [OpenTelemetry GenAI, MCP, and A2A evidence mappings](specs/decision-receipt/v0.1/mappings/manifest.json)
- [Versioned public and administrative OpenAPI contracts](specs/openapi/README.md)
- [API and SDK compatibility contract](docs/api-compatibility.md)
- `GET /v1/decisions/{decision_id}/receipt` — export
- `POST /v1/receipts/verify` — verify through the API
- `lians-receipt verify receipt.json --require-signature` — verify offline
- `POST /v1/decisions/impact` — record a dependency change and return its direct/reachable decision scope

The receipt proves the integrity and provenance of the recorded evidence boundary. It does not claim deterministic replay of nondeterministic model behavior, unrecorded context, or causal certainty for every reachable decision.

---

## Recorder → Receipt → Gate → Investigator

Lians is provider-neutral at the memory, optimization, and evidence boundaries.
Applications can send the
native Recorder envelope, OTLP/HTTP JSON or protobuf spans using GenAI semantic
attributes, MCP JSON-RPC messages, and A2A task/message/artifact events. Correlated
events update a first-receipt readiness score and automatically back-link to the
normalized evidence graph. The Python SDK also records public lifecycle boundaries
from Anthropic, Google ADK, OpenAI Agents, LangChain/LangGraph, and CrewAI without
patching runtime internals; each adapter documents what its provider surface cannot
observe.

At action time, Gate evaluates an immutable policy version against a trusted signed
receipt and the server-derived authenticated identity. Policies can require exact
scopes, an information-barrier match, current evidence, trusted issuers, risk limits,
and independent role-bound approvals. Approval statements and human review notes are
encrypted; their append-only chains remain independently verifiable. An allow
atomically issues one short-lived, single-use permit bound to a separately
authenticated mediator and the canonical downstream-request digest; deny/review
issues none. Protected providers must grant side-effect credentials only to that
mediator; direct evaluator credentials would remain an external bypass.

After a change or incident, Investigator combines the receipt, evidence graph,
indexed impact, Gate outcomes, review chain, cases, owned remediation tasks, closure
attestations, and audit-chain verification into one deterministic report and priority
queue. Embedded report collections are deterministically bounded and carry explicit
completeness metadata; see [Investigator report completeness](docs/investigator-read-model.md).

- [Universal Recorder + Gate quickstart](docs/quickstart-recorder.md)
- [Universal Recorder v0.2 specification](specs/universal-recorder/v0.2/README.md)
- [Governed agent improvement plane](docs/agent-improvement-plane.md)
- [Native Recorder hooks and exact coverage](docs/recorder-native-hooks.md)
- [Immutable approval and review semantics](docs/immutable-attestations.md)
- [Mediated Gate execution permits](docs/gate-execution-permits.md)
- [First-party Gate enforcement mediator](docs/gate-enforcement-mediator.md)
- [Tenant workload credential lifecycle](docs/workload-credentials.md)
- [Production operations and recovery](docs/production-operations.md)

---

## The regulated AI record problem

Lians is designed to serve as the authoritative record layer for agents that operate
on time-sensitive, audited, confidential data. The Memory product keeps recorded
context current and reconstructable; the Records product captures behavior and
oversight in an open, verifiable event format.

Most memory layers help an agent remember. Lians is built for institutions that
must also establish what the recorded boundary says the agent knew, when Lians
learned it, where the fact came from, who was allowed to see it, whether stale facts
were excluded, and whether subject-encrypted content became unreadable after its key
was destroyed while the audit trail survived.

That is the gap between useful memory and deployable memory in financial,
medical, and legal environments.

### What regulated memory must demonstrate

Generic agent memory optimizes for personalization and recall. Regulated agent
memory has a different job: it must keep the agent's context correct, current,
segregated, reproducible, and defensible under review.

Lians is designed for the failure modes that matter in institutions:

- **Stale fact contamination** - old rates, old guidance, old medication doses,
  old damages estimates, or old client facts must not silently enter context.
- **Point-in-time reconstruction** - an examiner, clinician, partner, or risk
  committee may ask what the agent knew at a specific timestamp.
- **Information barriers** - one desk, care team, or matter team must not read
  another team's memory because of an application-layer bug.
- **Erasure with audit survival** - private content must be removable without
  breaking custody records, audit hashes, or legal retention evidence.
- **Relational compliance checks** - conflicts of interest, related-party
  exposure, and referral networks are graph questions, not plain vector search.

The short competitive frame:

> mem0 remembers. Zep connects. Lians records and reconstructs what the agent was
> shown, when Lians knew it, who was authorized to see it, and whether the recorded
> policy allowed it to influence a consequential decision.

### Built for regulated verticals

| Vertical | What Lians can record and reconstruct | Product primitives |
|---|---|---|
| **Financial institutions** | Whether recorded knowledge crossed an as-of boundary; which barrier and audit controls applied | Bitemporal recall, recorded-data contamination checks, SEC/FINRA control mappings, RLS information barriers, related-party graph paths |
| **Healthcare organizations** | The recorded PHI scope and care-team boundary; whether a subject key was destroyed | Per-subject encryption, crypto-shred certificates, HIPAA safeguard mapping, care-network graph, air-gap mode |
| **Legal institutions** | The recorded matter boundary and privilege cutoff; whether the custody record still verifies after erasure | Matter-level barriers, `recall_at` for privilege dates, audit reconstruction, conflict-of-interest graph paths |

Procurement and technical review materials:

- [Institutional proof kit](docs/institutional-proof-kit.md)
- [Vertical pitch guide](docs/verticals.md)
- [Competitive landscape](docs/competitive-landscape.md)
- [Security whitepaper](docs/security-whitepaper.md)
- [SOC 2 / HIPAA readiness](docs/soc2-hipaa-readiness.md)
- [Threat model](docs/threat-model.md)
- [Production deploy checklist](docs/deploy.md)

---

## MCP - Native tool in any AI client

Lians is listed on the [official MCP Registry](https://registry.modelcontextprotocol.io/v0/servers/io.github.ebeirne%2Flians/versions/latest). Any MCP-compatible host - Claude Desktop, Cursor, VS Code, Windsurf, and others - can use local persistent memory immediately or connect to a hosted Lians server. No SDK code, custom adapter, Docker service, URL, or API key is required for local mode.

The server provides eight tools. For everyday memory use, expose only
`remember`, `recall`, and `recall_at` so the host does not inject five audit-tool
schemas into ordinary turns; enable the full evidence profile when reconstruction
or investigation is in scope.

| Tool | What it does |
|------|-------------|
| `remember` | Store a fact with event time and metadata |
| `recall` | Retrieve current (non-stale) facts by semantic query |
| `recall_at` | Point-in-time recall — what did we know on date X? |
| `reconstruct` | Full audit reconstruction for regulatory submissions |
| `list_conflicts` | Surface facts where two sources disagree |
| `memory_lineage` | Full supersession history of any fact |
| `fact_history` | Time-series view of a ticker+metric (e.g. AAPL EPS) |
| `backtest_check` | Detect lookahead bias before a backtest runs |

### Claude Desktop / Cursor / Windsurf

Add to your `claude_desktop_config.json` (or equivalent MCP config):

```json
{
  "mcpServers": {
    "lians": {
      "command": "uvx",
      "args": ["--from", "lians-sdk[mcp]", "lians-mcp"]
    }
  }
}
```

Restart your client and Lians memory tools appear immediately. Local mode persists to `~/.lians/mcp.db`. To use a hosted deployment instead, set `LIANS_URL`, `LIANS_API_KEY`, and optionally `LIANS_AGENT_ID`.

### Any other MCP host

```bash
uvx --from 'lians-sdk[mcp]' lians-mcp
```

No environment variables are needed for local mode. Set `LIANS_URL`, `LIANS_API_KEY`, and optionally `LIANS_AGENT_ID` to use a remote server.

---

## Quickstart

```bash
pip install lians-sdk[local]   # SQLite plus real local semantic embeddings, no Docker
```

```python
from lians import LocalLiansClient
from datetime import datetime, timezone

mem = LocalLiansClient()

mem.add(
    agent_id="analyst-1",
    content="NVDA FY2026 revenue guidance raised to $40B",
    event_time=datetime(2025, 11, 19, 16, tzinfo=timezone.utc),
    metadata={"ticker": "NVDA", "metric": "revenue_guidance"},
)

# Superseded facts are excluded at the DB layer — never reach the LLM
results = mem.recall(agent_id="analyst-1", query="NVDA revenue guidance")

# Point-in-time: what did we know on March 1? (compliance-grade answer)
results = mem.recall_at(
    agent_id="analyst-1",
    query="NVDA revenue guidance",
    as_of=datetime(2025, 3, 1, tzinfo=timezone.utc),
)
```

Switch to the hosted server with one line: `from lians import LiansClient as LocalLiansClient`

---

## Agent harness — drop-in memory loop

`LiansMemoryHarness` wraps the two operations every memory-augmented agent needs —
recall-before and remember-after — into one object, with the compliance scoping
(subject, source, event-time, information barrier) regulated deployments require.
Works with any sync client (`LiansClient` or `LocalLiansClient`) and any model.

```python
from lians import LiansClient, LiansMemoryHarness

harness = LiansMemoryHarness(mem, agent_id="research-desk", domain="finance")

# One call: recall context, run your model, persist the response.
answer = harness.run_turn(
    "What is NVDA's current revenue guidance?",
    generate=lambda context, query: call_model(f"{context}\n\nUser: {query}"),
)

# Or control each step:
context = harness.recall_context("NVDA revenue guidance")   # ready to inject
harness.remember("Desk note: guidance now $40B")            # write after the turn
```

Regulated scoping ties every write to one data subject and an information barrier:

```python
harness = LiansMemoryHarness(
    mem, agent_id="care-team-3",
    subject_id="MRN-00042",       # per-subject key — the crypto-shred target
    barrier_group="oncology",     # information-barrier tag
    domain="healthcare",
)
```

Runnable end-to-end demo: [`agentmem/examples/harness_demo.py`](agentmem/examples/harness_demo.py).

---

## Relationship graph — compliance questions that are inherently relational

Some compliance checks *are* graph queries. Lians stores **bitemporal relationship
edges** alongside facts — same audit chain, same information barriers, no graph
database — so you can answer them point-in-time:

- **Legal** — conflict-of-interest reachability (ABA 1.7/1.9): is an attorney
  connected to an adverse party?
- **Finance** — related-party / beneficial-ownership (SEC, AML/KYC): is a
  counterparty within N hops of a restricted entity?
- **Healthcare** — care-network / referral-pattern (anti-kickback) analysis.

```python
mem.relate("analyst-1", src_entity="Attorney", rel_type="represented",
           dst_entity="ClientX", event_time=datetime(2026, 1, 1, tzinfo=timezone.utc))
mem.relate("analyst-1", src_entity="ClientX", rel_type="adverse_to",
           dst_entity="PartyY", event_time=datetime(2026, 1, 1, tzinfo=timezone.utc))

# Conflict-of-interest check — is there a connection, and through what?
path = mem.path("analyst-1", src_entity="Attorney", dst_entity="PartyY")
# → {"connected": True, "hops": 2, "path": [...]}

# Point-in-time: who was connected on the day of the trade?
mem.neighbors("analyst-1", entity="FundA", depth=2, as_of=datetime(2025, 6, 1, tzinfo=timezone.utc))

# Graph-proximity reranking — boost recalls about entities near an anchor
mem.recall_near("analyst-1", query="earnings", near_entity="FundA", near_key="ticker")
```

Endpoints: `POST /v1/graph/relate` · `/v1/graph/unrelate` · `/v1/graph/extract` (text → edges, rule-based or opt-in LLM) · `GET /v1/graph/neighbors` · `/v1/graph/path` (all `as_of`-capable). Inspired by [Zep/Graphiti](docs/compare-zep.md), built on our compliance spine.

---

## Agent integrations — Claude Code, Codex, MCP

Give any coding agent persistent, compliance-grade memory:

| Host | How |
|------|-----|
| **Claude Code** | Plugin with slash commands (`/lians-remember`, `/lians-recall`, `/lians-audit`, `/lians-integrate`) and a compliance subagent — [`integrations/lians-plugin`](integrations/lians-plugin) |
| **Codex** | Drop-in `AGENTS.md` + MCP config — [`integrations/codex`](integrations/codex) |
| **Skills standard** | `npx skills add https://github.com/Lians-ai/Lians --skill lians` — works in Claude Code, Codex, Cursor — [`skills/`](skills) |
| **Any MCP host** | One-time config; eight native memory tools — see [MCP section](#mcp--native-tool-in-any-ai-client) above |

---

## Why Lians

Institutional AI agents accumulate facts that **change over time**: rate decisions
supersede prior ones, guidance gets revised, medication doses change, care plans
evolve, damages estimates move, and matter facts are corrected during discovery.
Systems that return every version with equal rank contaminate the LLM context with
stale facts.

Lians fixes this with a bitemporal model:
- **event_time** — when the fact happened (business time)
- **valid_from / valid_to** — when it was known (system time)

Superseded facts are excluded at the database layer. Consequential mutations append
to a tamper-evident SHA-256 audit chain that can support a configured SEC 17a-4
recordkeeping posture; the hash chain alone is not regulatory compliance. Per-subject
keys can be destroyed so encrypted content becomes unreadable while the audit trail
survives. Information barriers are enforced with PostgreSQL RLS as well as
application-layer checks.

### How Lians compares

Lians is built for workflows where correctness, access, and auditability must be
evaluated together. The table below is a version-pinned benchmark snapshot, not
a perpetual claim about current upstream products; rerun the linked adapters and
check primary documentation before relying on any competitor cell.

| | Lians | mem0 | Zep / Graphiti |
|---|---|---|---|
| **Temporal model** | Bitemporal facts **+ edges** (`event_time`, `valid_from/valid_to`) | ADD-only (v3) — versions coexist | Bitemporal graph edges (`valid_at`/`invalid_at`) |
| **Stale-fact handling** | Excluded at the DB layer (**0/4** stale in top-5) | Accumulated (**4/4** stale) | Edge invalidation (LLM-driven) |
| **Supersession** | Deterministic, keyed (**100%** on 22-pair benchmark) | None | LLM-extracted |
| **Point-in-time recall** | `recall_at` + bounded, completeness-reporting `snapshot` (**4/4**) | ✗ | Partial (graph query) |
| **Relationship graph** | ✓ bitemporal edges, N-hop, COI/related-party `path` | ✗ | ✓ (its core) |
| **Graph-proximity rerank** | ✓ `recall_near` (node-distance) | ✗ | ✓ |
| **Tamper-evident audit hash chain** | ✓ `verify_chain` | ✗ | ✗ |
| **Per-subject crypto-shred** (audit survives) | ✓ + erasure certificate | ✗ | ✗ |
| **Information barriers** (DB-layer RLS) | ✓ on facts **and** edges | ✗ (`user_id` filter) | ✗ (cloud-only) |
| **Conflict review queue** | ✓ detect + human-resolve + webhook | ✗ | ✗ |
| **Recorded-data backtest check** | ✓ `backtest_check` with exact counts and capture boundary | ✗ | ✗ |
| **Datastore** | Postgres + pgvector (one store) | vector DB | graph DB (Neo4j/FalkorDB) |
| **Determinism** | Reproducible | extraction-dependent | extraction-dependent |

**vs mem0** — our version-pinned adapter evaluates how an append-oriented
baseline handles revised facts, while Lians applies deterministic validity
windows and its documented control surface. Revalidate current upstream
capabilities before publishing a comparison. → [docs/compare-mem0.md](docs/compare-mem0.md)

**vs Zep / Graphiti** — Lians provides a temporal relationship graph on
PostgreSQL alongside its evidence and control contracts. The comparison document
records a pinned surface assessment; it does not claim that absent adapter
features are absent from every current upstream edition.
→ [docs/compare-zep.md](docs/compare-zep.md)

→ **Lookahead-bias demo** — the same agent backtest with naive vs point-in-time retrieval (Sharpe 4.6 vs −0.6, every leak logged): [ebeirne/lookahead-bias-demo](https://github.com/ebeirne/lookahead-bias-demo) · [in-repo](demo/lookahead-bias/README.md)
→ Full benchmark numbers: [docs/benchmark.md](docs/benchmark.md)
→ Regulated-eval head-to-head (five compliance invariants, Lians **5.0** / Zep–Graphiti **2.0** / mem0 **0.5**): [docs/regulated-eval-results.md](docs/regulated-eval-results.md) — Lians, Graphiti OSS, and mem0 OSS all **executed live** in their default configurations (per-cell evidence in the appendix); remaining columns scored from their public API surface via runnable adapters you can re-run with keys.

---

## Language SDKs

Lians ships maintained SDK surfaces across **five languages**: Python,
TypeScript, Go, Java, and C. That lets the same API contract reach JVM risk
platforms, native/low-latency services, and Python/TypeScript agent stacks. The
typed depth varies by language; newer continuation metadata is exposed as raw
JSON in some systems SDKs until their typed helpers catch up.

| Language | Install | Client | Docs |
|----------|---------|--------|------|
| **Python** | `pip install lians-sdk` | `from lians import LiansClient` | [sdk/python](agentmem/sdk/python) |
| **TypeScript / Node** | `npm install @lians-ai/lians` | `import { LiansClient } from "@lians-ai/lians"` | [sdk/typescript](agentmem/sdk/typescript) |
| **Go** | `go get github.com/Lians-ai/Lians/agentmem/sdk/go` | `lians.NewClient(url, key)` | [sdk/go](agentmem/sdk/go) |
| **Java** (JVM 11+) | `ai.lians:lians-sdk:0.5.0` (verify registry publication) | `new LiansClient(opts)` | [sdk/java](agentmem/sdk/java) |
| **C** (C99 + libcurl) | `cmake --build build` | `lians_client_new(...)` | [sdk/c](agentmem/sdk/c) |

→ **One-page install + 30-second quickstart for every language: [docs/install.md](docs/install.md)**

All five cover the same REST API: recall, point-in-time `recall_at`, snapshot,
backtest, crypto-shred erasure, audit-chain verify, and the relationship graph
(`relate` / `neighbors` / `path`).

---

## Framework integrations

| Framework | Install | Import |
|-----------|---------|--------|
| **LangChain** | `pip install lians-sdk[langchain]` | `from lians.langchain_integration import LiansChatHistory, build_tools` |
| **LangGraph** | `pip install lians-sdk[langgraph]` | `from lians.langgraph_integration import create_recall_node, create_remember_node` |
| **CrewAI** | `pip install lians-sdk[crewai]` | `from lians.crewai_integration import build_crewai_tools` |
| **OpenAI Agents SDK** | `pip install lians-sdk[openai-agents]` | `from lians.openai_agents_integration import build_openai_agent_tools` |
| **AutoGen v0.4** | `pip install lians-sdk[autogen]` | `from lians.autogen_integration import build_autogen_tools` |
| **Anthropic Python SDK Recorder** | `pip install lians-sdk[anthropic]` | `from lians import build_anthropic_recorder_middleware` |
| **Google ADK Recorder** | `pip install lians-sdk[google-adk]` | `from lians import build_google_adk_recorder_plugin` |
| **TypeScript / Node** | `npm install @lians-ai/lians` | `import { LiansClient } from "@lians-ai/lians"` |

---

## Self-hosted quickstart

```bash
git clone https://github.com/Lians-ai/Lians.git && cd Lians/agentmem
cp .env.demo .env
docker compose up --build -d
python scripts/seed_demo.py   # prints a demo API key; open demo/index.html
```

Use Compose for local/staging. The supported fail-closed production path is the
digest-pinned Helm distribution: [docs/deploy.md](docs/deploy.md).

---

## SDK reference

```python
# All three clients share the core memory methods shown below. The hosted HTTP
# clients additionally expose server-only Recorder, Gate, and Investigator APIs.
from lians import LiansClient          # sync, connects to hosted/self-hosted server
from lians import AsyncLiansClient     # async, for FastAPI / async frameworks
from lians import LocalLiansClient     # local SQLite, no server needed

client.add(agent_id, content, event_time, metadata={}, importance=0.5)
client.add_from_messages(agent_id, messages=[{"role": "user", "content": "..."}])
client.recall(agent_id, query, k=5)
client.recall_at(agent_id, query, as_of=datetime(...))   # point-in-time
client.snapshot(agent_id, as_of=datetime(...))           # full state export
client.backtest_check(agent_id, simulation_as_of=...)    # lookahead-bias detection
client.erase(subject_id, request_ref)                    # GDPR crypto-shred
```

---

## Architecture

```
                    ┌──────────────┐
                    │  LLM / Agent │
                    └──────┬───────┘
                           │  REST / MCP
               ┌───────────▼────────────┐
               │        Lians API        │   FastAPI · rate-limit · OTEL
               └──┬────────────────┬────┘
          ┌───────▼──────┐  ┌──────▼───────┐
          │   memories    │  │  event_log   │
          │  (encrypted)  │  │ (hash chain) │
          │  bitemporal   │  │  append-only │
          └───────┬───────┘  └──────────────┘
                  │
          ┌───────▼───────┐
          │  subject_keys  │   AES-256-GCM per subject
          │  (crypto-shred)│   destroy key = content unrecoverable
          └───────────────┘

  Postgres 16 + pgvector (HNSW)      Redis (recall hot cache)
```

**Recall pipeline:** BM25 + cosine (Voyage Finance-2) → recency decay → validity gate (`valid_to IS NULL` for present; `valid_from ≤ as_of < valid_to` for point-in-time)

**Supersession pipeline:** Stage 1 (metadata key overlap) → Stage 2 (deterministic: SUPERSEDES / CONFIRMS / ADDS) → Stage 3 (optional LLM adjudication for paraphrase detection)

---

## Configuration

| Variable | Default | Description |
|----------|---------|-------------|
| `EMBEDDING_PROVIDER` | `local` | `voyage` · `openai` · `sentence-transformers` · `local` |
| `VOYAGE_API_KEY` | — | Required when `EMBEDDING_PROVIDER=voyage` |
| `MASTER_ENCRYPTION_KEY` | — | Development-only base64 key for `KMS_PROVIDER=env`; blank fails closed unless an explicit test bypass is set |
| `MASTER_KEY_ID` | — | Required production-safe version ID embedded in new v2 envelopes; rotate with the offline [dual-key runbook](docs/master-key-rotation.md) |
| `MASTER_KEY_PREVIOUS_ID` | — | Optional bounded predecessor; requires the selected provider's matching previous material and must be removed only after a zero-remaining report |
| `KMS_PROVIDER` | `env` | `env` (development only) · `aws` · `azure` · `vault`; production rejects `env` |
| `ADMIN_SECRET` | — | Protects `/v1/admin/*` — **change in production** |
| `WORKLOAD_CREDENTIAL_MAX_TTL_SECONDS` | `2592000` | Maximum tenant-issued workload credential lifetime (30 days); production validates the bound |
| `SUPERSESSION_LLM_STAGE` | `false` | Enables Stage 3 LLM adjudication (Claude Haiku) |
| `AIRGAP_MODE` | `false` | Rejects/disables known payload-bearing egress; still requires independently enforced deny-by-default networking |
| `ADMISSION_MODE` | `monitor` | Admission control: `off` · `monitor` (tag+audit) · `enforce` (reject injection/blocked source, hold PII/PHI/MNPI for review) |
| `SIEM_URL` | — | Deprecated, lossy compatibility path; production forbids it in favor of a durable namespace-scoped `siem` integration destination |
| `WORM_MODE` | `false` | Operator attestation that the documented logical and provider-backed WORM controls are in place; the flag does not create or certify them |
| `STRIPE_API_KEY` | — | Enables durable per-namespace usage delivery; production accepts only live/restricted live keys |
| `STRIPE_METER_DECISION_EVENT` | `lians_authoritative_decision` | Product-native meter for an authoritative decision committed with its evidence and audit binding |
| `STRIPE_METER_PROTECTED_ACTION_EVENT` | `lians_protected_action` | Product-native meter for successful single-use Gate permit consumption |
| `STRIPE_METER_WRITE_EVENT` | `agentmem_memory_write` | Compatibility meter for memory-product write contracts |
| `STRIPE_METER_RECALL_EVENT` | `agentmem_memory_recall` | Compatibility meter for memory-product recall contracts |
| `STRIPE_METER_ASYNC_ERROR_DESTINATION_CONFIGURED` | `false` | Attests that Stripe asynchronous meter errors reach a durable monitored destination |

Full reference: [agentmem/.env.example](agentmem/.env.example)

---

## Key endpoints

| Method | Path | Description |
|--------|------|-------------|
| `POST` | `/v1/memories` | Add a memory (admission control; supersession check; `Idempotency-Key` for exactly-once retries) |
| `POST` | `/v1/decisions` · `/v1/records/events` | Append body-bound, transactionally idempotent decision and ledger records |
| `POST` | `/v1/recorder/events` · `/batch` | Normalize native, OTLP GenAI, MCP, and A2A evidence events |
| `GET` | `/v1/recorder/runs/{run_id}/readiness` | Inspect correlated first-receipt readiness and declared capture gaps |
| `POST` | `/v1/control/gate/evaluate` | Evaluate a trusted receipt and identity-bound action through runtime policy |
| `POST` | `/v1/control/gate/permits/consume` | Redeem an allow permit once as its exact enforcement mediator |
| `GET` | `/v1/investigator/queue` | Prioritize decisions across evidence, Gate, review, and remediation signals |
| `GET` | `/v1/investigator/decisions/{decision_id}` | Build the flagship cross-control investigation report |
| `GET` | `/.well-known/lians` · `/v1/platform/capabilities` | Discover standards, APIs, privacy posture, and enabled components |
| `GET`/`POST` | `/v1/identity/workload-credentials` | Tenant OIDC admin lifecycle for expiring, least-privilege workload credentials |
| `GET`/`POST` | `/v1/admissions` · `/{id}/resolve` | Review queue for held writes (PII/PHI/MNPI) — approve / reject |
| `POST` | `/v1/memories/batch` | Atomic batch ingest with ordered idempotent replay |
| `POST` | `/v1/recall` | Hybrid BM25+cosine recall; optional `as_of`, MMR rerank (`filters._rerank=mmr`) |
| `POST` | `/v1/context` | Token-budgeted, ready-to-inject context block (point-in-time + MMR aware) |
| `POST` | `/v1/erase` | GDPR crypto-shred by `subject_id` |
| `GET`  | `/v1/audit/reconstruct` | Reconstruct agent state at any past date |
| `GET`  | `/v1/admin/audit/verify` | Verify SHA-256 hash chain integrity |
| `GET`  | `/v1/admin/audit/export` | Export audit log (SEC/FINRA/CFTC) |
| `GET`  | `/livez` | Liveness probe (cheap; process up) |
| `GET`  | `/readyz` · `/health` | Readiness / deep health check (DB + Redis) |

Interactive docs: `http://localhost:8000/docs`

---

## Running tests

```bash
cd agentmem
pip install -e ".[dev]"
pytest -v

# Benchmarks only (no API keys required)
pytest tests/test_supersession_benchmark.py tests/test_recall_quality.py -v
```

See [docs/testing.md](docs/testing.md) for the six named invariants (temporal soundness, audit immutability, erasure, etc.).

---

## Production & operations

Built to run in a regulated production environment, not just to demo:

- **Transactional idempotency** — hashed, request-bound `Idempotency-Key` claims share the authoritative commit for memory, batch, decision, ledger-event, and review writes; changed bodies return `409`. See [the contract](docs/transactional-idempotency.md).
- **Resilient clients** — the Python SDK retries transport errors / 5xx / 429 only for methods with a proven durable replay contract; arbitrary mutations remain non-retrying.
- **Kubernetes probes** — cheap `/livez` (liveness) and deep `/readyz` (readiness), so a dependency blip doesn't restart healthy pods.
- **Rate limiting** — independent network, credential, and admin buckets in Redis, with a configured bounded local or deny posture during backend outages.
- **Access control** — namespace-scoped workload keys plus native OIDC JWT/JWKS verification, SCIM 2.0 provisioning, RBAC roles (`owner`/`analyst`/`compliance`/`readonly`), and PostgreSQL information barriers.
- **DB-layer information barriers** — `RESTRICTIVE` PostgreSQL RLS, **proven in CI** against a non-superuser role. *Run the app as a non-superuser DB role* — superusers bypass RLS.
- **Memory admission control** — govern what's *allowed into* memory: PII/PHI/MNPI detection, source-trust, prompt-injection quarantine, and a high-risk review queue (`ADMISSION_MODE`). No other memory layer does this.
- **Observability** — bearer-protected Prometheus metrics with bounded route templates, OpenTelemetry traces through a persistent Collector queue, JSON access logs, request IDs, and multi-window SLO alert rules.
- **Recovery** — fail-closed logical backup/verification/restore tooling, provider-attested immutable-storage handoff, and operator runbooks with explicit RPO/RTO evidence gates.
- **Evaluation** — a judge-free memory-eval harness (`agentmem/benchmarks/memory_eval.py`) in the LoCoMo/LongMemEval shape.

Security & procurement docs: [security policy](SECURITY.md) · [security-whitepaper.md](docs/security-whitepaper.md) · [threat-model.md](docs/threat-model.md) · [soc2-hipaa-readiness.md](docs/soc2-hipaa-readiness.md) · [sso.md](docs/sso.md) · [workload-credentials.md](docs/workload-credentials.md) · [publishing.md](docs/publishing.md)

---

## Compliance

| Requirement | Feature |
|-------------|---------|
| SEC 17a-4 tamper-evidence | SHA-256 hash chain on every audit row |
| FINRA 4511 recordkeeping | Append-only `event_log` |
| GDPR Art. 17 erasure | AES-256-GCM per-subject keys; crypto-shred |
| MiFID II point-in-time | Bitemporal: `event_time` + `valid_from/valid_to` |
| Information barriers | `barrier_group` column; PostgreSQL RLS |
| HIPAA §164.312 | Per-subject encryption, audit controls, transmission security |

> **Scope of these claims:** Lians provides the *technical controls* mapped
> above — it is software, not a certification. Regulatory compliance is a
> property of your deployment and organization (retention configuration,
> policies, attestations such as SOC 2 or a HIPAA assessment), and several
> controls require operator configuration (WORM object-lock, non-superuser DB
> role, KMS). Every claim links to the doc that says exactly what is and
> isn't covered — start with [soc2-hipaa-readiness.md](docs/soc2-hipaa-readiness.md).

Full documentation: [compliance.md](docs/compliance.md) · [hipaa.md](docs/hipaa.md) · [security-whitepaper.md](docs/security-whitepaper.md) · [threat-model.md](docs/threat-model.md) · [soc2-hipaa-readiness.md](docs/soc2-hipaa-readiness.md) · [sso.md](docs/sso.md) · [workload-credentials.md](docs/workload-credentials.md) · [worm-storage.md](docs/worm-storage.md)

Access control: expiring namespace-scoped workload credentials with `read`/`write`/`admin` scopes and RBAC roles (`owner`/`analyst`/`compliance`/`readonly`); native OIDC JWT/JWKS verification and SCIM 2.0 provisioning, with gateway/SAML compatibility.

---

## Packaging & Pricing

Lians is open-source and fully self-hostable — **the entire feature set,
including every compliance primitive, is in this repository under Apache 2.0.**
Paid packages sell deployment support, hardening review, and evidence
packets around the open core, not license keys. A managed cloud is in early
access for customers whose compliance posture allows hosted processing
(contact us); regulated buyers should choose the package by deployment
boundary and evidence requirements, not by a consumer-style monthly tier.

| Package | Best for | Deployment | Commercial model |
|---|---|---|---|
| **Developer** | Local prototypes, benchmarks, integrations | Local library or single-node server | Free / usage-based |
| **Team** | Internal pilots and non-production agent workflows | Docker or small Kubernetes deployment | Usage-based or team plan |
| **Regulated Production** | Sensitive, audited, time-dependent agent workloads | Customer cloud, private VPC, or on-prem | Annual contract |
| **Enterprise / Air-Gap** | Banks, hospitals, law firms, insurers, government | Private cloud, on-prem, or air-gapped | Custom annual contract |
| **Managed Cloud** | Zero-ops production where hosted processing is approved | Lians-managed environment | Contract or usage-based |

Healthcare customers require an executed BAA before PHI is processed in a
managed environment. Financial and legal customers may require customer-managed
keys, private networking, regional residency, dedicated environments, or
air-gapped deployment.

Full packaging documentation: [docs/pricing-tiers.md](docs/pricing-tiers.md) and [docs/billing.md](docs/billing.md)

**Switching from another system?** [Migrate from mem0](docs/migrate-from-mem0.md) or [Migrate from Zep CE](docs/migrate-from-zep.md)

---

## License

Apache 2.0 — see [LICENSE](LICENSE).

<!-- mcp-name: io.github.ebeirne/lians -->
