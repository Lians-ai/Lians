# Lians for Codex

Provider-neutral, persistent memory for Codex, backed by bitemporal recall,
provenance, exact-token optimization, and the evidence controls consequential
workflows need.

## Two ways to wire it in

### 1. AGENTS.md (recommended)

Copy [`AGENTS.md`](./AGENTS.md) to your project root (or merge it into an existing
`AGENTS.md`). Codex reads it automatically and learns when and how to recall and
remember through the Lians SDK / harness.

```bash
cp integrations/codex/AGENTS.md ./AGENTS.md
pip install lians-sdk          # or lians-sdk[local] for zero-setup SQLite
```

Set `LIANS_URL`, `LIANS_API_KEY`, and `LIANS_AGENT_ID` in your environment (free
key at [api.lians.dev](https://api.lians.dev)). Local mode needs no env vars.

### 2. MCP server (native tools)

Add the block from [`config.example.toml`](./config.example.toml) to a trusted
project's `.codex/config.toml` (or to `~/.codex/config.toml` when you intentionally
want a global server), replace `LIANS_MCP_PROJECT_ROOT`, then restart Codex. The
recommended core profile exposes
three native tools (`remember`, `recall`, `recall_at`) with no SDK code in your
project. The same server also provides five audit tools (`reconstruct`,
`list_conflicts`, `memory_lineage`, `fact_history`, `backtest_check`) that can be
enabled for evidence-heavy tasks.

For managed mode, set `LIANS_URL` and `LIANS_API_KEY` outside TOML; Codex forwards
them through `env_vars`. The project-root hash derives isolated default agent and
namespace values from the empty fail-closed entries. Replacing
`LIANS_AGENT_ID` and `LIANS_NAMESPACE` with explicit names opts into sharing that
memory scope across projects.

Keep `required = true` for the core server. Local mode completes bounded runtime
imports before the MCP handshake and warms the model/query path in the
background; without the readiness gate, a fresh fast-model turn can finish
before Codex has discovered the Lians tools.

The smaller core profile is deliberate: its three canonical MCP schemas are 589
`o200k_base` tokens in the 2026-08-08 checkout, while exposing all eight schemas
would add avoidable context to ordinary turns. Codex's internal prompt framing may
differ, so treat this as schema accounting rather than a per-message billing
measurement. See the [Codex validation report](../../docs/benchmarks/codex-mcp-local-2026-08-08.md).

Current recall considers up to 50 memories and compiles at most 2,650 estimated
tokens. That cap was chosen near the recorded top-50 LOCOMO mean, but the exact
production renderer and cap have not yet been rejudged as a quality run. The
cross-provider target and the signed-in Codex/Claude results are in the
[usage-extension report](../../docs/benchmarks/provider-usage-extension-2026-08-08.md).

The public `lians-sdk` 0.5.0 package predates the bounded MCP response path and
server-side allowlist. Codex's `enabled_tools` still enforces the three-tool host
surface, but checkout validation must replace the `--from` value with a PEP 508
direct reference until the updated SDK is published:

```text
lians-sdk[mcp] @ file:///absolute/path/to/Lians/agentmem/sdk/python
```

## Install via the skills standard

Lians ships cross-tool skills installable with `npx skills add` (works for Codex,
Claude Code, Cursor, and other skills-standard hosts):

```bash
npx skills add https://github.com/Lians-ai/Lians --skill lians
npx skills add https://github.com/Lians-ai/Lians --skill lians-integrate
```

See [`../../skills/`](../../skills) for the skill definitions.

## Why Lians over a plain vector store

Codex agents that touch financial, clinical, or legal facts accumulate data that
**changes over time** — guidance revisions, dosage changes, matter status. A plain
vector store returns every version with equal rank and contaminates your context.
Lians excludes superseded facts at the database layer and can reconstruct exactly
what the agent knew at any past date. See the
[mem0 comparison](../../docs/compare-mem0.md).

Lians can reduce context tokens on measured workflows; it does not guarantee
lower total credits, latency, or higher answer quality for every prompt. Model,
reasoning effort, cached input, tool use, and output length also affect Codex
usage.
