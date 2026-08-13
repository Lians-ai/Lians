# Lians - Guide for AI Coding Assistants

This file gives coding assistants the context needed to work in this
repository.

## What Lians is

Lians is a local-first, provider-neutral memory tool for AI agents. Its core
product loop is deliberately small:

1. `remember` one durable fact, preference, constraint, or decision; and
2. `recall` a bounded set of relevant current memories in a later session.

MCP is the default integration surface for existing AI clients. The Python
local client is the default application surface. Both can run with SQLite and
no Lians account or API key.

Lians also has advanced temporal, audit, erasure, isolation, and reconstruction
capabilities. Preserve those capabilities, but do not make them a prerequisite
for the basic memory experience or the first explanation of the product.

## Product principles

- Keep the default setup local and model-provider neutral.
- Make `remember` and `recall` work before exposing advanced tools.
- Return small, task-relevant context instead of replaying full conversations.
- Exclude superseded facts from current recall.
- Require explicit confirmation whenever an integration exposes irreversible
  deletion.
- Keep sources, timestamps, and lineage available for users who need them.

## Repository layout

```text
agentmem/                   Core service and SDKs
  src/lians/                FastAPI service and memory engine
  tests/                    Pytest suite
  alembic/versions/         Database migrations
  sdk/python/lians/         Python SDK, local client, and MCP server
  sdk/typescript/src/       TypeScript SDK
integrations/               Agent and framework integrations
plugins/                    Installable agent plugins
docs/                       Setup, architecture, security, and operations
```

## How to run tests

```bash
python -m pip install -e ".[dev]"
python scripts/test_all.py
```

Focused server tests can also run directly:

```bash
python -m pytest agentmem/tests/test_mcp_local.py -q
python -m pytest agentmem/tests/test_published_release_status.py -q
```

No external model API key is required for the default test suite.

## Key environment variables

See `agentmem/.env.example` for the complete reference.

| Variable | Default | Purpose |
|---|---|---|
| `LIANS_LOCAL_DB` | `~/.lians/mcp.db` | Local MCP SQLite store |
| `LIANS_MCP_ENABLED_TOOLS` | all tools | Optional MCP tool allowlist |
| `LIANS_URL` | unset | Hosted or self-hosted Lians service |
| `LIANS_API_KEY` | unset | Credential for a remote service |
| `LIANS_AGENT_ID` | `mcp-agent` | Memory namespace for an agent |
| `EMBEDDING_PROVIDER` | `local` in tests | Embedding implementation |

## Architecture decisions to know

1. **Local mode is a real product path.** `LocalLiansClient` uses SQLite and the
   same service-layer behavior without requiring the HTTP service.
2. **MCP is the universal adapter.** `lians-mcp` exposes memory tools to any
   compatible host. Keep its starter schema and errors easy to understand.
3. **Supersession protects current recall.** Metadata overlap and deterministic
   rules identify revisions; an optional model stage can adjudicate paraphrases.
4. **Embeddings are provider-agnostic.** Add a provider through the factory in
   `agentmem/src/lians/embeddings.py`.
5. **The audit chain is append-only.** Never update or delete `event_log` rows.
6. **Information barriers are enforced in PostgreSQL.** Production deployments
   must use a non-superuser role for row-level security to be effective.
7. **Erasure destroys per-subject keys.** Content becomes unrecoverable while
   non-content audit structure can remain verifiable.

## Common tasks

**Change the basic memory behavior:**

- Start in `agentmem/src/lians/memory_service.py`.
- Add a focused regression test in `agentmem/tests/`.
- Verify both local-client and HTTP behavior when the contract is shared.

**Change an MCP tool:**

- Edit `agentmem/sdk/python/lians/mcp_server.py`.
- Keep `remember` and `recall` backward compatible.
- Run `agentmem/tests/test_mcp_local.py`.

**Add a framework or agent integration:**

- Create or extend `integrations/<framework>/`.
- Keep the first-run surface to `remember` and `recall` where possible.
- Document the smallest working configuration before advanced options.

**Change the schema:**

- Add an Alembic migration in `agentmem/alembic/versions/`.
- Never modify an already-published migration.

## Testing invariants

The detailed invariants live in `docs/testing.md`. The most important product
guarantees are:

- superseded facts do not appear in present recall;
- point-in-time recall returns the state known at that time;
- erased subjects return no readable content;
- barrier groups cannot read each other's memory; and
- the append-only audit chain detects tampering.
