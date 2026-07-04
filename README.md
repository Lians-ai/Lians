<p align="center">
  <a href="https://github.com/Lians-ai/Lians">
    <img src="docs/images/banner.png" width="800px" alt="Lians - Regulated AI Memory Infrastructure">
  </a>
</p>

<p align="center">
  <a href="https://github.com/Lians-ai/Lians">Learn more</a>
  -
  <a href="https://github.com/Lians-ai/Lians/tree/main/docs">Docs</a>
  -
  <a href="docs/install.md">Install</a>
  -
  <a href="https://github.com/Lians-ai/Lians#self-hosted-quickstart">Quickstart</a>
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
  <a href="https://registry.modelcontextprotocol.io/servers/io.github.ebeirne/lians">
    <img src="https://img.shields.io/badge/MCP-Official%20Registry-blueviolet" alt="MCP Official Registry">
  </a>
  <a href="LICENSE">
    <img src="https://img.shields.io/badge/License-Apache%202.0-blue.svg" alt="License: Apache 2.0">
  </a>
</p>

<p align="center">
  <a href="docs/benchmark.md"><strong>Benchmark: 0 stale facts in top-5 vs mem0-style recall's 4/4, plus 100% supersession accuracy</strong></a>
</p>

---

[Lians](https://github.com/Lians-ai/Lians) is **regulated AI memory infrastructure**: a memory control plane for agents that operate on time-sensitive, audited, confidential data.

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

## The institutional memory problem

Lians should be understood as a memory control plane for agents that operate on
time-sensitive, audited, confidential data.

Most memory layers help an agent remember. Lians is built for institutions that
must also prove what the agent knew, when it knew it, where the fact came from,
who was allowed to see it, whether stale facts were excluded, and whether erased
content is cryptographically unrecoverable while the audit trail survives.

That is the gap between useful memory and deployable memory in financial,
medical, and legal environments.

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

> mem0 remembers. Zep connects. Lians proves what the agent knew, when it knew it,
> who could see it, and whether that memory was allowed to influence a regulated
> decision.

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

Lians is listed on the [official MCP Registry](https://registry.modelcontextprotocol.io/servers/io.github.ebeirne/lians). Any MCP-compatible host - Claude Desktop, Cursor, VS Code, Windsurf, and others - can connect to your Lians server as a native tool with a one-time config. No SDK code, no custom adapter, no wrapper.

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
      "args": ["--from", "lians-sdk[mcp]", "lians-mcp"],
      "env": {
        "LIANS_URL": "https://your-lians-server.internal",
        "LIANS_API_KEY": "lians_...",
        "LIANS_AGENT_ID": "trading-desk-1"
      }
    }
  }
}
```

Restart your client and Lians memory tools appear immediately — no install step for your users beyond setting the three env vars.

### Any other MCP host

```bash
uvx --from 'lians-sdk[mcp]' lians-mcp
```

Set `LIANS_URL`, `LIANS_API_KEY`, and optionally `LIANS_AGENT_ID` in the environment.

---

## Quickstart

```bash
pip install lians-sdk[local]   # zero-setup local mode (SQLite, no Docker)
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

Superseded facts are excluded at the database layer. Every write is recorded in a tamper-evident SHA-256 hash chain (SEC 17a-4). Per-subject keys can be destroyed for GDPR erasure while the audit trail survives. Information barriers are enforced at PostgreSQL RLS, not the application layer.

### How Lians compares

The two leading open memory layers each solve part of the problem; Lians is built
for the regulated case where correctness, access, and auditability are all required
at once.

| | Lians | mem0 | Zep / Graphiti |
|---|---|---|---|
| **Temporal model** | Bitemporal facts **+ edges** (`event_time`, `valid_from/valid_to`) | ADD-only (v3) — versions coexist | Bitemporal graph edges (`valid_at`/`invalid_at`) |
| **Stale-fact handling** | Excluded at the DB layer (**0/4** stale in top-5) | Accumulated (**4/4** stale) | Edge invalidation (LLM-driven) |
| **Supersession** | Deterministic, keyed (**100%** on 22-pair benchmark) | None | LLM-extracted |
| **Point-in-time recall** | `recall_at` + exhaustive `snapshot` (**4/4**) | ✗ | Partial (graph query) |
| **Relationship graph** | ✓ bitemporal edges, N-hop, COI/related-party `path` | ✗ | ✓ (its core) |
| **Graph-proximity rerank** | ✓ `recall_near` (node-distance) | ✗ | ✓ |
| **SEC 17a-4 audit hash chain** | ✓ `verify_chain` | ✗ | ✗ |
| **GDPR/HIPAA crypto-shred** (audit survives) | ✓ + erasure certificate | ✗ | ✗ |
| **Information barriers** (DB-layer RLS) | ✓ on facts **and** edges | ✗ (`user_id` filter) | ✗ (cloud-only) |
| **Conflict review queue** | ✓ detect + human-resolve + webhook | ✗ | ✗ |
| **Backtest lookahead-bias proof** | ✓ `backtest_check` | ✗ | ✗ |
| **Datastore** | Postgres + pgvector (one store) | vector DB | graph DB (Neo4j/FalkorDB) |
| **Determinism** | Reproducible | extraction-dependent | extraction-dependent |

**vs mem0** — mem0's v3 is ADD-only, so revised facts (guidance, rates, doses,
damages) pile up and contaminate recall; it has no documented encryption-at-rest,
RBAC, or audit. Lians excludes stale versions deterministically and adds the
compliance spine. → [docs/compare-mem0.md](docs/compare-mem0.md)

**vs Zep / Graphiti** — Graphiti's knowledge graph is excellent, and Lians now has
one too (built on Postgres, no graph DB) — but Graphiti by its own docs has *no
access control, multi-tenancy, audit, or compliance*; Zep only adds those in the
closed cloud. Lians keeps the graph **and** the open compliance spine.
→ [docs/compare-zep.md](docs/compare-zep.md)

→ Full benchmark numbers: [docs/benchmark.md](docs/benchmark.md)
→ Regulated-eval head-to-head (five compliance invariants, Lians **5.0** / Zep **2.0** / mem0 **1.0**): [docs/regulated-eval-results.md](docs/regulated-eval-results.md) — Lians executed live; competitors scored from their public API surface via runnable adapters you can re-run with keys.

---

## Language SDKs

Lians ships native SDKs across **five languages** — the widest coverage of any open
agent-memory layer. mem0 is Python/TypeScript; Zep adds Go. Lians matches all of
those **and** adds **Java and C**, which neither competitor offers — putting the
full compliance memory layer where regulated systems actually run: JVM risk
platforms, and native/low-latency C in trading, market-data, and on-prem
healthcare/legal stacks.

| Language | Install | Client | Docs |
|----------|---------|--------|------|
| **Python** | `pip install lians-sdk` | `from lians import LiansClient` | [sdk/python](agentmem/sdk/python) |
| **TypeScript / Node** | `npm install @lians-ai/lians` | `import { LiansClient } from "@lians-ai/lians"` | [sdk/typescript](agentmem/sdk/typescript) |
| **Go** | `go get github.com/Lians-ai/Lians/agentmem/sdk/go` | `lians.NewClient(url, key)` | [sdk/go](agentmem/sdk/go) |
| **Java** (JVM 11+) | `dev.lians:lians-sdk:0.3.0` (Maven) | `new LiansClient(opts)` | [sdk/java](agentmem/sdk/java) |
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
