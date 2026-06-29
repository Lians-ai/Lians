# Lians for Codex

Persistent, financial-grade memory for the [Codex](https://github.com/openai/codex)
agent — with the compliance guarantees regulated teams need (bitemporal recall,
SEC 17a-4 audit chain, GDPR/HIPAA crypto-shred, information barriers).

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
`~/.codex/config.toml`. Codex gains eight native memory tools (`remember`,
`recall`, `recall_at`, `reconstruct`, `list_conflicts`, `memory_lineage`,
`fact_history`, `backtest_check`) with no SDK code in your project.

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
