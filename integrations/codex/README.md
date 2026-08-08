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

Add the block from [`config.example.toml`](./config.example.toml) to
`~/.codex/config.toml`, then restart Codex. The recommended core profile exposes
three native tools (`remember`, `recall`, `recall_at`) with no SDK code in your
project. The same server also provides five audit tools (`reconstruct`,
`list_conflicts`, `memory_lineage`, `fact_history`, `backtest_check`) that can be
enabled for evidence-heavy tasks.

Keep `required = true` for the core server. A local embedding model prewarms
before the MCP handshake; without the readiness gate, a fresh fast-model turn
can finish before Codex has discovered the Lians tools.

The smaller core profile is deliberate: its three canonical MCP schemas are 514
`o200k_base` tokens in the 2026-08-08 checkout, while exposing all eight schemas
would add avoidable context to ordinary turns. Codex's internal prompt framing may
differ, so treat this as schema accounting rather than a per-message billing
measurement. See the [Codex validation report](../../docs/benchmarks/codex-mcp-local-2026-08-08.md).

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
