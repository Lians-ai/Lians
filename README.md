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
  <a href="OPEN_CORE.md">Open core</a>
  -
  <a href="COMMERCIAL.md">Commercial</a>
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
  <a href="https://registry.modelcontextprotocol.io/?q=io.github.ebeirne%2Flians">
    <img src="https://img.shields.io/badge/MCP-Official%20Registry-blueviolet" alt="MCP Official Registry">
  </a>
  <a href="LICENSE">
    <img src="https://img.shields.io/badge/License-Apache%202.0-blue.svg" alt="License: Apache 2.0">
  </a>
</p>

<p align="center">
  <a href="docs/benchmarks/README.md"><strong>Reproducible benchmark evidence and offline quality gates</strong></a>
  <br />
  <a href="docs/benchmarks/riad-1.md"><strong>RIAD-1: decision reconstruction benchmark</strong></a>
  · <a href="https://github.com/Lians-ai/Lians/actions/workflows/riad-1.yml"><strong>CI receipts</strong></a>
</p>

---

[Lians Community](https://github.com/Lians-ai/Lians) is the open foundation for
the **cross-platform decision evidence and reconstruction layer for regulated
AI**. It gives
compliance, model-risk, and operational-risk teams one record of what an agent
knew, what it retrieved, which policy governed it, which tools ran, who
reviewed it, and what changed later.

The durable moat is neutrality. A firm can run agents across Bedrock, Azure
OpenAI, Anthropic direct, and open-source runtimes while keeping one portable
evidence record outside every provider.

Every write is preserved as a governed temporal record and compiled into a
typed memory artifact. Every recall can run in `fast`, `deep`, or `reconstruct`
mode and returns a content-addressed receipt that can bind automatically to a
Decision Envelope. See
[decision evidence and reconstruction](docs/decision-evidence.md), the
[normative completeness grades](docs/completeness-grades.md),
[Evidence Pack signing key custody](docs/evidence-signing-key-custody.md), the
[governed memory engine](docs/memory-engine.md) and
[reproducible evidence gates](docs/benchmarks/README.md).

The platform exposes one evidence workflow:

- **Capture**: open a Decision Envelope and bind memory, traces, policy
  decisions, prompts, tools, and human review as the action happens.
- **Reconstruct**: reproduce the point-in-time knowledge and execution path even
  when exact deterministic replay is impossible.
- **Verify**: grade every decision as Recorded, Reconstructable, Verifiable, or
  Replayable, with every missing requirement named.
- **Monitor**: when a source, policy, or model changes, identify every exposed
  decision and emit a blast-radius alert.

Memory remains a core evidence source and performance primitive. It is not the
commercial category by itself.

| | Library | Self-Hosted Community | Lians Platform |
|---|---|---|---|
| **Best for** | Testing and prototyping | Teams operating their own evidence service | Organizations buying an operated and supported system |
| **Setup** | `pip install lians-sdk[local]` | `docker compose up --build` | Contracted managed or private deployment |
| **Database** | SQLite | Postgres 16 + pgvector | Managed by Lians or jointly operated |
| **Evidence formats and verification** | Apache 2.0 | Apache 2.0 | Included plus commercial operations |
| **Operations and support** | Community | Customer-owned | Lians-owned scope and service commitments |
| **Private control plane** | No | No | Available by contracted release |

## Community software and the full Lians experience

The public repository is a useful, production-capable foundation, but it is not
the whole company. Every file published here remains under its stated open
source license. Lians Platform adds commercial software and delivery that is
not distributed in this repository, including managed fleet operations,
private control-plane capabilities, enterprise integrations, deployment
automation, evidence operations, support, and service-level commitments.

We do not retroactively restrict released Apache-licensed code. Instead, new
work is placed on the correct side of a documented boundary. Read
[OPEN_CORE.md](OPEN_CORE.md) for the exact source boundary and
[COMMERCIAL.md](COMMERCIAL.md) for current paid offers.

---

## Agent memory should improve without losing the record

Lians gives agents a durable memory loop across facts, context, decisions, outcomes,
and reviewed lessons. The Memory product keeps context current and useful; the
Records product captures behavior and oversight in an open, verifiable event format.

Most memory layers stop at storage and retrieval. Lians is built for teams that
also need to know what the agent knew, when it knew it, where the fact came from,
which outcomes followed, who was allowed to see it, and whether stale or erased
content was kept out of future context.

That is the gap between a memory demo and a memory system teams can trust in
production, especially in financial, medical, and legal environments.

### What regulated memory must prove

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

> Runtime vendors explain their own cloud. Lians preserves portable decision
> evidence across all of them.

### Built for regulated verticals

| Vertical | What Lians proves | Product primitives |
|---|---|---|
| **Financial institutions** | No stale or future facts influenced a decision; desk barriers held; audit state is reconstructable | Bitemporal recall, backtest contamination checks, SEC/FINRA audit export, RLS information barriers, related-party graph paths |
| **Healthcare organizations** | PHI access is scoped; care-team memory is reconstructable; patient erasure is provable | Per-subject encryption, crypto-shred certificates, HIPAA safeguard mapping, care-network graph, air-gap mode |
| **Legal institutions** | Matter walls held; privilege cutoffs are reproducible; chain-of-custody survives erasure | Matter-level barriers, `recall_at` for privilege dates, audit reconstruction, conflict-of-interest graph paths |

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

Lians is listed on the [official MCP Registry](https://registry.modelcontextprotocol.io/?q=io.github.ebeirne%2Flians). Any MCP-compatible host - Claude Desktop, Cursor, VS Code, Windsurf, and others - can use local persistent memory immediately or connect to a hosted Lians server. No SDK code, custom adapter, Docker service, URL, or API key is required for local mode.

Your agents get eight tools automatically:

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

# Deeper multi-facet recall for planning and research
results = mem.recall(
    agent_id="analyst-1",
    query="What changed in the guidance and why?",
    mode="deep",
)

# Point-in-time: what did we know on March 1? (compliance-grade answer)
results = mem.recall_at(
    agent_id="analyst-1",
    query="NVDA revenue guidance",
    as_of=datetime(2025, 3, 1, tzinfo=timezone.utc),
)

# Every result includes receipt_sha256, provenance_coverage, and the
# resolved serving mode and latency budget.
```

Switch to the hosted server with one line: `from lians import LiansClient as LocalLiansClient`

### Decision evidence quickstart

```python
from datetime import datetime, timezone
from lians import AsyncLiansClient

async with AsyncLiansClient(base_url=LIANS_URL, api_key=LIANS_API_KEY) as lians:
    envelope = await lians.open_decision_envelope(
        agent_id="underwriter-1",
        decision_type="credit_application",
        regime="ECOA_REG_B",
        completeness_profile="regulated_recordkeeping",
        knowledge_as_of=datetime.now(timezone.utc),
    )

    context = await lians.recall(
        agent_id="underwriter-1",
        query="verified applicant income",
        decision_envelope_id=envelope["id"],
    )

    sealed = await lians.seal_decision_envelope(
        envelope["id"],
        outcome="manual_review",
        decided_at=datetime.now(timezone.utc),
        input_hash=INPUT_SHA256,
        output_hash=OUTPUT_SHA256,
    )

    # No overclaiming: every missing requirement names the grade it blocks.
    print(sealed["completeness"])
```

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

Superseded facts are excluded at the database layer. Every write is recorded in a tamper-evident SHA-256 hash chain; physical immutability and SEC 17a-4 deployment claims require separately configured WORM storage and policy controls. Per-subject keys can be destroyed for governed erasure while the audit trail survives. Information barriers are enforced at PostgreSQL RLS, not only at the application layer.

### How Lians compares

Temporal memory is no longer unique: Graphiti documents a bitemporal knowledge
graph, Mem0 documents temporal reasoning and history, Hindsight documents
query-time temporal recall and audit controls, and Supermemory documents content
versioning and a temporal graph. Lians should be evaluated on the compound
decision-evidence boundary it implements:

- reconstruct a named decision at both event-time and knowledge-time cutoffs;
- enumerate the source versions included and excluded at those cutoffs;
- detect post-cutoff leakage before a result is accepted;
- emit a content-addressed Evidence Pack that can be verified offline; and
- preserve the surrounding chain when subject content is crypto-erased.

The repository's regulated-memory harness is useful product evidence, not an
independent general-product leaderboard. Current leadership language remains
gated on production load, isolation, restore, failure-injection, public benchmark,
and independent-reproduction evidence. See [docs/competitive-landscape.md](docs/competitive-landscape.md)
and the runnable claim policy in
[`agentmem/benchmarks/release_claims.py`](agentmem/benchmarks/release_claims.py).

→ **Lookahead-bias demo** — the same agent backtest with naive vs point-in-time retrieval (Sharpe 4.6 vs −0.6, every leak logged): [ebeirne/lookahead-bias-demo](https://github.com/ebeirne/lookahead-bias-demo) · [in-repo](demo/lookahead-bias/README.md)
→ Full benchmark numbers: [docs/benchmark.md](docs/benchmark.md)
→ Regulated-eval head-to-head (five compliance invariants, Lians **5.0** / Zep–Graphiti **2.0** / mem0 **0.5**): [docs/regulated-eval-results.md](docs/regulated-eval-results.md) — Lians, Graphiti OSS, and mem0 OSS all **executed live** in their default configurations (per-cell evidence in the appendix); remaining columns scored from their public API surface via runnable adapters you can re-run with keys.

---

## Language SDKs

Lians maintains client implementations across **five languages**. Public package
versions currently differ by ecosystem; use the explicit coordinates below and
verify the machine-readable [published release status](docs/published-release-status.json).

| Language | Install | Client | Docs |
|----------|---------|--------|------|
| **Python 0.4.2** | `pip install lians-sdk==0.4.2` | `from lians import LiansClient` | [sdk/python](agentmem/sdk/python) |
| **TypeScript / Node 0.4.0** | `npm install @lians-ai/lians@0.4.0` | `import { LiansClient } from "@lians-ai/lians"` | [sdk/typescript](agentmem/sdk/typescript) |
| **Go 0.4.1** | `go get github.com/Lians-ai/Lians/agentmem/sdk/go@v0.4.1` | `lians.NewClient(url, key)` | [sdk/go](agentmem/sdk/go) |
| **Java 0.4.1** (JVM 11+) | `ai.lians:lians-sdk:0.4.1` (Maven Central) | `new LiansClient(opts)` | [sdk/java](agentmem/sdk/java) |
| **C 0.4.1** (C99 + libcurl) | build from the `v0.4.1` source tag | `lians_client_new(...)` | [sdk/c](agentmem/sdk/c) |

→ **One-page install + 30-second quickstart for every language: [docs/install.md](docs/install.md)**

All five cover core memory operations. Python and TypeScript currently expose a
broader advanced surface than Go, Java, and C; verify the client you plan to use
against the OpenAPI contract before a pilot.

---

## Framework integrations

| Framework | Install | Import |
|-----------|---------|--------|
| **LangChain** | `pip install lians-sdk[langchain]` | `from lians.langchain_integration import LiansChatHistory, build_tools` |
| **LangGraph** | `pip install lians-sdk[langgraph]` | `from lians.langgraph_integration import create_recall_node, create_remember_node` |
| **CrewAI** | `pip install lians-sdk[crewai]` | `from lians.crewai_integration import build_crewai_tools` |
| **OpenAI Agents SDK** | `pip install lians-sdk[openai-agents]` | `from lians.openai_agents_integration import build_openai_agent_tools` |
| **AutoGen v0.4** | `pip install lians-sdk[autogen]` | `from lians.autogen_integration import build_autogen_tools` |
| **TypeScript / Node** | `npm install @lians-ai/lians` | `import { LiansClient } from "@lians-ai/lians"` |

---

## Self-hosted quickstart

```bash
git clone https://github.com/Lians-ai/Lians.git && cd Lians/agentmem
cp .env.demo .env
docker compose up --build -d
python scripts/seed_demo.py   # prints a demo API key; open demo/index.html
```

Deploy to Fly.io, Kubernetes, or bare Docker: [docs/deploy.md](docs/deploy.md)

---

## SDK reference

```python
# All three clients share the same API surface
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
| `MASTER_ENCRYPTION_KEY` | — | Base64 32-byte key; blank disables PII encryption |
| `KMS_PROVIDER` | `env` | `env` · `aws` · `azure` · `vault` |
| `ADMIN_SECRET` | — | Protects `/v1/admin/*` — **change in production** |
| `SUPERSESSION_LLM_STAGE` | `false` | Enables Stage 3 LLM adjudication (Claude Haiku) |
| `AIRGAP_MODE` | `false` | Hard-fails at startup if any config would send data externally |
| `ADMISSION_MODE` | `monitor` | Admission control: `off` · `monitor` (tag+audit) · `enforce` (reject injection/blocked source, hold PII/PHI/MNPI for review) |
| `SIEM_URL` | — | Stream every audit event to a SIEM collector (Splunk HEC / Datadog / Elastic) |
| `WORM_MODE` | `false` | Attest write-once-read-many storage for SEC 17a-4 (object-locked audit, no UPDATE/DELETE on `event_log`) |
| `STRIPE_API_KEY` | — | Enables per-namespace usage metering |

Full reference: [agentmem/.env.example](agentmem/.env.example)

---

## Key endpoints

| Method | Path | Description |
|--------|------|-------------|
| `POST` | `/v1/memories` | Add a memory (admission control; supersession check; `Idempotency-Key` for exactly-once retries) |
| `GET`/`POST` | `/v1/admissions` · `/{id}/resolve` | Review queue for held writes (PII/PHI/MNPI) — approve / reject |
| `POST` | `/v1/memories/batch` | Batch ingest |
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
pip install -e ".[dev]"
python scripts/test_all.py

# Benchmarks only (no API keys required)
PYTHONPATH=agentmem/src python -m pytest \
  agentmem/tests/test_supersession_benchmark.py \
  agentmem/tests/test_recall_quality.py -v
```

See [docs/testing.md](docs/testing.md) for the six named invariants (temporal soundness, audit immutability, erasure, etc.).

---

## Production & operations

Built to run in a regulated production environment, not just to demo:

- **Exactly-once writes** — `Idempotency-Key` on `POST /v1/memories`; the SDKs send a stable key automatically, so a retried write never duplicates.
- **Resilient clients** — built-in retry with exponential backoff on transport errors / 5xx / 429.
- **Kubernetes probes** — cheap `/livez` (liveness) and deep `/readyz` (readiness), so a dependency blip doesn't restart healthy pods.
- **Rate limiting** — per-API-key sliding window (Redis), fails open.
- **Access control** — namespace-scoped keys, `read`/`write`/`admin` scopes, **RBAC roles** (`owner`/`analyst`/`compliance`/`readonly`), and SSO via gateway forward-auth.
- **DB-layer information barriers** — `RESTRICTIVE` PostgreSQL RLS, **proven in CI** against a non-superuser role. *Run the app as a non-superuser DB role* — superusers bypass RLS.
- **Memory admission control** — govern what's *allowed into* memory: PII/PHI/MNPI detection, source-trust, prompt-injection quarantine, and a high-risk review queue (`ADMISSION_MODE`). No other memory layer does this.
- **SIEM streaming** — every audit event forwarded to Splunk HEC / Datadog / Elastic (`SIEM_URL`), fire-and-forget.
- **Observability** — Prometheus metrics + Grafana, OpenTelemetry traces, JSON access logs with a request ID.
- **Evaluation** — a judge-free memory-eval harness (`agentmem/benchmarks/memory_eval.py`) in the LoCoMo/LongMemEval shape.

Security & procurement docs: [security-whitepaper.md](docs/security-whitepaper.md) · [threat-model.md](docs/threat-model.md) · [soc2-hipaa-readiness.md](docs/soc2-hipaa-readiness.md) · [sso.md](docs/sso.md) · [publishing.md](docs/publishing.md)

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

Full documentation: [compliance.md](docs/compliance.md) · [hipaa.md](docs/hipaa.md) · [security-whitepaper.md](docs/security-whitepaper.md) · [threat-model.md](docs/threat-model.md) · [soc2-hipaa-readiness.md](docs/soc2-hipaa-readiness.md) · [sso.md](docs/sso.md) · [worm-storage.md](docs/worm-storage.md)

Access control: namespace-scoped API keys with `read`/`write`/`admin` scopes and RBAC roles (`owner`/`analyst`/`compliance`/`readonly`); SSO via gateway forward-auth (any OIDC/SAML IdP).

---

## Packaging & Pricing

Lians uses an open-core model. Community provides the portable formats, SDKs,
local and self-hosted evidence engine, verifiers, integrations, tests, and
benchmarks published in this repository under Apache 2.0. The commercial Lians
experience adds private product capabilities and accountable delivery around
that foundation.

| Package | Best for | Deployment | Commercial model |
|---|---|---|---|
| **Community** | Local prototypes, integrations, independent verification, and customer-operated deployments | Local library or self-hosted server | Free under Apache 2.0 |
| **Diagnostic** | One evidence request blocking one consequential workflow | Five-business-day engagement | $2,500 fixed |
| **Proof Sprint** | Production-representative validation of one qualified workflow | Customer or Lians-supported environment | $7,500 fixed |
| **Design Partnership** | One protected workflow with a named sponsor and decision date | Contracted deployment boundary | $20,000 fixed |
| **Annual Infrastructure** | Operated or supported consequential workflows | Managed, customer cloud, private VPC, on-prem, or air-gapped | Target $60,000 to $75,000 ACV |

Healthcare customers require an executed BAA before PHI is processed in a
managed environment. Financial and legal customers may require customer-managed
keys, private networking, regional residency, dedicated environments, or
air-gapped deployment.

Full packaging documentation: [COMMERCIAL.md](COMMERCIAL.md) and
[docs/pricing-tiers.md](docs/pricing-tiers.md)

**Switching from another system?** [Migrate from mem0](docs/migrate-from-mem0.md) or [Migrate from Zep CE](docs/migrate-from-zep.md)

---

## License

Source in this repository is Apache 2.0 unless a file says otherwise. See
[LICENSE](LICENSE), [NOTICE](NOTICE), and [OPEN_CORE.md](OPEN_CORE.md).
The software license does not grant rights to Lians names, logos, or the Lotus
design; see [TRADEMARKS.md](TRADEMARKS.md).

<!-- mcp-name: io.github.ebeirne/lians -->
