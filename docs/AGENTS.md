# Lians - Guide for AI Coding Assistants

This file gives coding assistants the context needed to work in this
repository.

## What Lians is

Lians Check is a local-first, provider-neutral proof-of-done check for AI coding
agents. Its core product loop is deliberately small:

1. discover and authorize a short set of real project checks;
2. run those checks through a Lians-owned local verifier;
3. reject evidence that no longer matches the workspace; and
4. show `NO PROOF`, `NEEDS WORK`, or `READY TO REVIEW`.

Free local `remember` and `recall` remain the recovery and distribution wedge.
MCP is the default integration surface for existing AI clients. The Python local
client is the default application surface. Both can run with SQLite and no Lians
account or API key.

Lians also has broader memory, temporal, audit, erasure, isolation, research,
and reconstruction capabilities. Preserve working capabilities, but do not lead
the product, onboarding, or marketing with them.

## Product principles

- Keep the default setup local and model-provider neutral.
- Make recovery work before exposing advanced tools.
- Return small, task-relevant context instead of replaying full conversations.
- Exclude superseded facts from current recall.
- Treat agent prose and touched files as untrusted activity, not completion.
- Only measured local, measured CI, or human-confirmed evidence can satisfy a
  completion criterion.
- Use `ready_for_human_review` as the strongest automated readiness state.
- Never imply that the gate proves correctness, approval, merge safety, or
  deployment safety.
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
3. **Task evidence is typed.** Agent-attested and inferred activity are useful
   recovery context but cannot open the human-review gate.
4. **Workspace state is part of the checkpoint.** Bind task evidence to local
   Git identity, commit, dirty state, and changed-path digest when available.
5. **Supersession protects current recall.** Metadata overlap and deterministic
   rules identify revisions; an optional model stage can adjudicate paraphrases.
6. **Embeddings are provider-agnostic.** Add a provider through the factory in
   `agentmem/src/lians/embeddings.py`.
7. **The audit chain is append-only.** Never update or delete `event_log` rows.
8. **Information barriers are enforced in PostgreSQL.** Production deployments
   must use a non-superuser role for row-level security to be effective.
9. **Erasure destroys per-subject keys.** Content becomes unrecoverable while
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

**Change the Guard task contract:**

- Start in `packages/lians-easy/lians_easy/task_contract.py`.
- Preserve the five evidence trust classes, trusted-issuer provenance, and the
  human-review boundary. Agent-facing callers must not promote their own
  evidence into a satisfying trust class.
- Run the task-contract, session-capture, bridge, MCP, and verification tests.

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
