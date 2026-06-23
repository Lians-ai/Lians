# Lian

**Financial-grade agent memory.** Bitemporal model, compliance audit chain, and GDPR crypto-shred — built for regulated environments.

```bash
pip install lian-sdk[local]   # zero-setup local mode (SQLite, no Docker)
```

```python
from lian import LocalLianClient
from datetime import datetime, timezone

mem = LocalLianClient()

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

Switch to the hosted server with one line change: `from lian import LianClient as LocalLianClient`

---

## Why Lian

Financial AI agents accumulate facts that **change over time**: rate decisions supersede prior ones, guidance gets revised, price targets change. Systems like mem0 return all versions with equal rank — your LLM gets contaminated context.

Lian fixes this with a bitemporal model:
- **event_time** — when the fact happened (business time)
- **valid_from / valid_to** — when it was known (system time)

Superseded facts are excluded at the database layer. Every write is recorded in a tamper-evident SHA-256 hash chain (SEC 17a-4). Per-subject keys can be destroyed for GDPR erasure while the audit trail survives. Information barriers are enforced at PostgreSQL RLS, not the application layer.

| What | Lian | mem0 | Graphiti/Zep |
|------|------|------|--------------|
| Stale facts in top-5 (5-revision chain) | **0 / 4** | 4 / 4 | N/T |
| Supersession accuracy (22-pair benchmark) | **100%** | N/A | No benchmark |
| Point-in-time recall (4 quarterly queries) | **4 / 4** | 0 / 4 | Partial |
| SEC 17a-4 audit hash chain | ✓ | ✗ | ✗ |
| GDPR crypto-shred with audit survival | ✓ | ✗ | ✗ |
| Information barriers (DB-layer RLS) | ✓ | ✗ | ✗ |

→ Full benchmark numbers: [docs/benchmark.md](docs/benchmark.md)

---

## Installation

| Mode | Command | Use case |
|------|---------|----------|
| Local (SQLite) | `pip install lian-sdk[local]` | Prototyping, CI, zero setup |
| Self-hosted server | `docker compose up --build` | Production, compliance |
| Cloud | `pip install lian-sdk` + API key | Managed hosting |

---

## Framework integrations

| Framework | Install | Import |
|-----------|---------|--------|
| **LangChain** | `pip install lian-sdk[langchain]` | `from lian.langchain_integration import LianChatHistory, build_tools` |
| **LangGraph** | `pip install lian-sdk[langgraph]` | `from lian.langgraph_integration import create_recall_node, create_remember_node` |
| **CrewAI** | `pip install lian-sdk[crewai]` | `from lian.crewai_integration import build_crewai_tools` |
| **OpenAI Agents SDK** | `pip install lian-sdk[openai-agents]` | `from lian.openai_agents_integration import build_openai_agent_tools` |
| **AutoGen v0.4** | `pip install lian-sdk[autogen]` | `from lian.autogen_integration import build_autogen_tools` |
| **TypeScript / Node** | `npm install lian` | `import { LianClient } from "lian"` |

---

## Self-hosted quickstart

```bash
git clone https://github.com/ebeirne/Lian.git && cd Lian/agentmem
cp .env.demo .env
docker compose up --build -d
python scripts/seed_demo.py   # prints a demo API key; open demo/index.html
```

Deploy to Fly.io, Kubernetes, or bare Docker: [docs/deploy.md](docs/deploy.md)

---

## SDK reference

```python
# All three clients share the same API surface
from lian import LianClient          # sync, connects to hosted/self-hosted server
from lian import AsyncLianClient     # async, for FastAPI / async frameworks
from lian import LocalLianClient     # local SQLite, no server needed

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
               │        Lian API        │   FastAPI · rate-limit · OTEL
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
| `STRIPE_API_KEY` | — | Enables per-namespace usage metering |

Full reference: [agentmem/.env.example](agentmem/.env.example)

---

## Key endpoints

| Method | Path | Description |
|--------|------|-------------|
| `POST` | `/v1/memories` | Add a memory (triggers supersession check) |
| `POST` | `/v1/memories/batch` | Batch ingest |
| `POST` | `/v1/recall` | Hybrid BM25+cosine recall; optional `as_of` |
| `POST` | `/v1/erase` | GDPR crypto-shred by `subject_id` |
| `GET`  | `/v1/audit/reconstruct` | Reconstruct agent state at any past date |
| `GET`  | `/v1/admin/audit/verify` | Verify SHA-256 hash chain integrity |
| `GET`  | `/v1/admin/audit/export` | Export audit log (SEC/FINRA/CFTC) |
| `GET`  | `/health` | Deep health check (DB + Redis) |

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

## Compliance

| Requirement | Feature |
|-------------|---------|
| SEC 17a-4 tamper-evidence | SHA-256 hash chain on every audit row |
| FINRA 4511 recordkeeping | Append-only `event_log` |
| GDPR Art. 17 erasure | AES-256-GCM per-subject keys; crypto-shred |
| MiFID II point-in-time | Bitemporal: `event_time` + `valid_from/valid_to` |
| Information barriers | `barrier_group` column; PostgreSQL RLS |
| HIPAA §164.312 | Per-subject encryption, audit controls, transmission security |

Full documentation: [docs/compliance.md](docs/compliance.md) · [docs/hipaa.md](docs/hipaa.md)

---

## License

Apache 2.0 — see [LICENSE](LICENSE).
