# Lians Skills

Cross-tool agent skills for [Lians](https://github.com/Lians-ai/Lians), the
financial-grade memory layer. These follow the **skills standard**, so they work
in Claude Code, Codex, Cursor, and any compatible host.

| Skill | Type | What it does |
|-------|------|--------------|
| [`lians`](./lians/SKILL.md) | reference | Teaches the agent to store and recall facts correctly through the Lians SDK and harness — bitemporal, compliance-safe. |
| [`lians-integrate`](./lians-integrate/SKILL.md) | pipeline | Wires Lians into an existing agent codebase, test-first and minimal-diff. |

## Install

```bash
npx skills add https://github.com/Lians-ai/Lians --skill lians
npx skills add https://github.com/Lians-ai/Lians --skill lians-integrate
```

## Tool-specific packaging

- **Claude Code** — a full plugin (commands + compliance subagent) lives in
  [`../integrations/lians-plugin`](../integrations/lians-plugin), registered via
  the root [`.claude-plugin/marketplace.json`](../.claude-plugin/marketplace.json).
- **Codex** — drop-in `AGENTS.md` and MCP config in
  [`../integrations/codex`](../integrations/codex).
- **Any MCP host** — Lians is on the
  [official MCP Registry](https://registry.modelcontextprotocol.io/v0/servers/io.github.ebeirne%2Flians/versions/latest).

## Why these exist

Agents in finance, healthcare, and legal accumulate facts that change over time.
A plain vector store returns every stale revision with equal rank. Lians excludes
superseded facts at the database layer and reconstructs exactly what the agent
knew at any past date — see the [mem0 comparison](../docs/compare-mem0.md).
