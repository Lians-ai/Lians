# AgentMem

**Financial-grade agent memory** — the only memory layer designed for regulated environments.

When a financial AI agent accumulates facts over time, those facts **change**. Last quarter's guidance is wrong today. A central bank rate decision supersedes the previous one. A price target gets revised. Systems like mem0 and Zep return all of these with equal rank — your LLM gets contaminated context.

AgentMem solves this with a **bitemporal model**: every fact carries both *when it happened* (business time) and *when it was ingested* (system time). Superseded facts are excluded at the database layer, not the prompt layer. And every write is recorded in a tamper-evident hash chain that satisfies SEC 17a-4.

---

## The number that matters most

| What | AgentMem | mem0 | Zep |
|------|----------|------|-----|
| Stale facts in top-5 (5-revision NVDA chain) | **0 / 4** | 4 / 4 | 4 / 4 |
| Supersession accuracy (22-pair: synthetic + real-world) | **100%** | N/A | ~partial |
| Point-in-time recall (4 quarterly queries)    | **4 / 4** | 0 / 4 | 0 / 4 |
| SEC 17a-4 audit hash chain                    | ✓ | ✗ | ✗ |
| GDPR crypto-shred with audit survival         | ✓ | ✗ | ✗ |
| Information barriers (Chinese walls)          | ✓ | ✗ | ✗ |

→ Full numbers: [BENCHMARK.md](BENCHMARK.md)

---

## Try it in 5 minutes

**Requirements:** Docker Desktop

```bash
git clone https://github.com/ebeirne/AI_Memory_Software_lotus.git
cd AI_Memory_Software_lotus/agentmem

# Copy the zero-credential demo config
cp .env.demo .env

# Start Postgres + Redis + AgentMem (first run builds the image, ~2 min)
docker compose up --build -d

# Seed the demo dataset (NVDA guidance chain, TSLA deliveries, Fed rates)
pip install httpx
python scripts/seed_demo.py
```

The seed script prints a **read-only API key**. Open `demo/index.html` in your browser, paste the key, and click the demo buttons.

---

## What the demo shows

**Stale-fact suppression** — 5 revisions of NVDA FY2026 guidance ($28B → $40B). Present-time query returns only $40B. The "no supersession" panel shows all 5 revisions flooding the context — exactly what mem0 returns.

**Point-in-time recall** — query "NVDA guidance on 2025-03-01" and get $32B (the revision current on that date), not $40B (the revision current today). Change the date to walk through each revision boundary. mem0 and Zep cannot do this at all.

**Audit chain verification** — one API call confirms every event-log row has an unbroken SHA-256 hash chain. Used by SEC/FINRA examiners to verify records haven't been modified.

---

## Quickstart (Python SDK)

```python
import httpx, datetime

BASE = "http://localhost:8000"
KEY  = "agentmem_..."          # from seed_demo.py or provision your own

client = httpx.Client(headers={"X-API-Key": KEY})

# Write a memory
client.post(f"{BASE}/v1/memories", json={
    "agent_id":   "analyst-1",
    "content":    "NVDA FY2026 revenue guidance raised to $40B",
    "event_time": "2025-11-19T16:00:00Z",
    "metadata":   {"ticker": "NVDA", "metric": "revenue_guidance"},
    "importance": 0.9,
})

# Recall (present-time — superseded facts excluded automatically)
r = client.post(f"{BASE}/v1/recall", json={
    "agent_id": "analyst-1",
    "query":    "NVDA revenue guidance",
    "k":        5,
})
for mem in r.json()["memories"]:
    print(mem["content"])

# Point-in-time — what did we know on March 1?
r = client.post(f"{BASE}/v1/recall", json={
    "agent_id": "analyst-1",
    "query":    "NVDA revenue guidance",
    "k":        5,
    "as_of":    "2025-03-01T00:00:00Z",
})
```

---

## Architecture

```
                    ┌──────────────┐
                    │  LLM / Agent │
                    └──────┬───────┘
                           │  REST / MCP
               ┌───────────▼────────────┐
               │      AgentMem API      │  FastAPI · rate-limit · OTEL
               └──┬────────────────┬────┘
          ┌───────▼──────┐  ┌──────▼───────┐
          │   memories    │  │  event_log   │
          │  (encrypted)  │  │ (hash chain) │
          │  bitemporal   │  │  append-only │
          └───────┬───────┘  └──────────────┘
                  │
          ┌───────▼───────┐
          │  subject_keys  │  AES-256-GCM per subject
          │  (crypto-shred)│  crypto-shred = zero the key
          └───────────────┘

  Postgres 16 + pgvector (HNSW)      Redis (recall hot cache)
```

**Recall pipeline:** BM25 + cosine (Voyage Finance-2) → recency decay → validity gate → `valid_to IS NULL` (present) or `valid_from ≤ as_of < valid_to` (point-in-time)

**Supersession pipeline:** Stage 1 (metadata key overlap) → Stage 2 (deterministic rules: SUPERSEDES / CONFIRMS / ADDS / CONTRADICTS_SAME_TIME) → Stage 3 (optional LLM adjudication for paraphrase detection)

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

## Deploy

### Docker Compose (local / self-hosted)
```bash
cd agentmem
cp .env.demo .env          # or .env.example for production template
# edit .env: set MASTER_ENCRYPTION_KEY, ADMIN_SECRET, VOYAGE_API_KEY
docker compose up --build -d
python scripts/seed_demo.py
```

### Fly.io
```bash
# Install flyctl, then:
fly auth login
fly launch --no-deploy          # picks up fly.toml
fly secrets set \
  MASTER_ENCRYPTION_KEY="$(python -c 'import secrets,base64; print(base64.b64encode(secrets.token_bytes(32)).decode())')" \
  ADMIN_SECRET="$(python -c 'import secrets; print(secrets.token_urlsafe(32))')" \
  VOYAGE_API_KEY="pa-..."
fly postgres create --name agentmem-db
fly postgres attach agentmem-db
fly deploy
```

### Kubernetes
```bash
# Fill in k8s/secret.yaml values first, then:
kubectl apply -f k8s/namespace.yaml
kubectl apply -f k8s/secret.yaml
kubectl apply -f k8s/migrate-job.yaml
kubectl wait --for=condition=complete job/agentmem-migrate -n agentmem --timeout=120s
kubectl apply -k k8s/
```

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
| `POST` | `/v1/admin/api-keys` | Provision API key |
| `PUT`  | `/v1/admin/retention/{ns}` | Set retention policy + legal hold |
| `POST` | `/v1/admin/barriers` | Assign information barriers |
| `GET`  | `/health` | Deep health check (DB + Redis) |

Interactive docs: `http://localhost:8000/docs`

---

## Compliance features

| Requirement | Feature |
|-------------|---------|
| SEC 17a-4 tamper-evidence | SHA-256 hash chain on every audit row |
| FINRA 4511 recordkeeping | Append-only `event_log`; admin ops logged with `__admin__` identity |
| GDPR Art. 17 (erasure) | AES-256-GCM per-subject keys; crypto-shred nulls key, content hash survives |
| MiFID II point-in-time | Bitemporal model: `event_time` + `valid_from/valid_to` |
| Information barriers | `barrier_group` column; agents only see their own group's memories |
| Retention policies | Per-namespace TTL with legal hold override; automated prune scheduler |
| KMS integration | AWS KMS · Azure Key Vault · HashiCorp Vault · env (dev) |
| Air-gapped deployment | `AIRGAP_MODE=true` enforces sentence-transformers + no LLM stage at startup |

---

## Test suite

```bash
cd agentmem
pip install -e ".[dev]"
python -m pytest -v

# Benchmark tests (no API keys required)
python -m pytest tests/test_supersession_benchmark.py tests/test_recall_quality.py -v
```

557 tests pass. 30 skipped (require `TEST_DATABASE_URL` pointing to a live Postgres + pgvector instance).

---

## Integrations

- **MCP server** — `src/agentmem/mcp_server.py` (stdio transport, 8 tools: remember, recall, recall_at, reconstruct, list_conflicts, memory_lineage, fact_history, backtest_check)
- **LangChain.js** — `sdk/typescript/src/langchain.ts` (`buildAgentMemTools`, `AgentMemChatHistory`)
- **TypeScript SDK** — `sdk/typescript/` (native fetch, no runtime dependencies, Node 18+)
- **OpenTelemetry** — `memory.add` and `memory.recall` spans; plug into Datadog / Grafana Tempo / Honeycomb
