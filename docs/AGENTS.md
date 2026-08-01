# Lians — Guide for AI Coding Assistants

This file gives AI coding assistants (Claude Code, Cursor, Codex, Windsurf) the context needed to work effectively in this repository.

## What Lians is

Lians is a **financial-grade memory layer for AI agents**. It solves the stale-fact problem: when an agent writes "NVDA guidance is $35B" and later writes "NVDA guidance revised to $40B", naive memory systems return both — contaminating LLM context. Lians uses a bitemporal model to suppress superseded facts at the database layer.

Key capabilities:
- **Bitemporal facts** — `event_time` (when it happened) + `valid_from/valid_to` (when we knew it)
- **Supersession pipeline** — three-stage detection (metadata overlap → deterministic rules → optional LLM)
- **Tamper-evident audit chain** — SHA-256 hash chain on every write; SEC 17a-4 deployments additionally require configured WORM storage and policy controls
- **GDPR crypto-shred** — per-subject AES-256-GCM keys; destroy key = content unrecoverable
- **Information barriers** — PostgreSQL RLS enforced at the DB layer, not application layer
- **Backtest contamination detection** — flags lookahead bias before a backtest runs

## Repository layout

```
agentmem/                   Core server (FastAPI + Postgres + pgvector + Redis)
  src/lians/                Python package — all server logic
    api/                    Route handlers
    adapters/               Domain adapters (finance, healthcare, legal)
    embeddings.py           Provider abstraction (Voyage / OpenAI / local)
    memory_service.py       Core write/recall/supersession logic
    supersession.py         Three-stage supersession pipeline
    audit_chain.py          SHA-256 Merkle audit chain
    crypto.py               AES-256-GCM per-subject encryption
    config.py               Pydantic settings (all env vars documented here)
  tests/                    557+ pytest tests (all run with local embeddings)
  alembic/versions/         DB migrations — read these to understand the schema
  sdk/python/lians/         Full SDK with framework integrations
  sdk/typescript/src/       TypeScript SDK
sdk/python/src/lians/       Thin HTTP client SDK (published as `lians` on PyPI)
integrations/               Per-framework integration packages
docs/                       Documentation
```

## How to run tests

```bash
cd agentmem
pip install -e ".[dev]"
pytest -v                           # all tests, local embeddings
pytest -v -k "not pgvector"         # skip tests requiring real Postgres
pytest tests/test_memory_service.py # a single file
```

No external API keys are required. All tests run with `EMBEDDING_PROVIDER=local`.

## Key environment variables

See `agentmem/.env.example` for the full list. The ones that matter most:

| Variable | Default | Notes |
|---|---|---|
| `DATABASE_URL` | SQLite (tests) | Postgres for server mode |
| `EMBEDDING_PROVIDER` | `local` | `voyage` or `openai` for production |
| `ADMIN_SECRET` | `dev-admin-secret-change-in-production` | Must change in prod |
| `MASTER_ENCRYPTION_KEY` | — | Required for encryption; blank disables it |
| `SUPERSESSION_LLM_STAGE` | `false` | Enables Stage 3 Anthropic LLM adjudication |

## Architecture decisions to know

1. **Supersession is three-stage**: Stage 1 = metadata key overlap; Stage 2 = deterministic rule engine (SUPERSEDES / CONFIRMS / ADDS); Stage 3 = optional LLM (Claude Haiku) for paraphrase detection. Most work happens in `supersession.py`.

2. **Embeddings are provider-agnostic**: `embeddings.py` has a `get_provider()` factory. Add a new provider by implementing `EmbeddingProvider` and registering it in the match block.

3. **All tests use `EMBEDDING_PROVIDER=local`**: The local provider uses a deterministic hash projection — not accurate, but fast and zero-dependency. Never use it in production.

4. **RLS barriers are enforced at Postgres level**: `migration 0011_rls_barriers` applies `FORCE ROW LEVEL SECURITY`. The session variable `app.current_namespace` controls visibility. Admin routes set it to `__admin__` to bypass.

5. **The audit chain is append-only**: `audit_chain.py` — never add an UPDATE or DELETE on `event_log`. The hash chain breaks if you do.

6. **Encryption**: Each subject gets a unique DEK stored in `subject_keys`. The DEK is encrypted under the master key. Destroying the `subject_keys` row is the GDPR crypto-shred — content rows remain but are unrecoverable.

## Common tasks

**Add a new API route:**
- Add handler in `agentmem/src/lians/api/routes_<area>.py`
- Register router in `agentmem/src/lians/main.py`
- Add scope check via `auth.require("<scope>")` for paid-tier features

**Add a new domain adapter:**
- Copy `agentmem/src/lians/adapters/passthrough.py`
- Register in `agentmem/src/lians/adapters/__init__.py`

**Add a new framework integration:**
- Create `integrations/<framework>/python/`
- Implement `remember()` and `recall()` wrappers around `LiansClient`
- Add tests and a README

**Change the schema:**
- Write an Alembic migration in `agentmem/alembic/versions/`
- Follow the `0001_initial.py` pattern
- Never modify existing migrations

## Testing invariants

The six named invariants (from `docs/testing.md`):
- **I1 Temporal soundness** — superseded facts never appear in present recall
- **I2 Audit immutability** — hash chain is tamper-evident
- **I3 Erasure completeness** — erased subjects return no data
- **I4 Barrier isolation** — barrier groups cannot see each other's data
- **I5 Point-in-time correctness** — `recall_at(as_of=T)` returns exactly the facts valid at T
- **I6 Backtest purity** — `backtest_check` rejects any memory written after `simulation_as_of`
