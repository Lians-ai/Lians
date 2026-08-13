# Lians Memory for Gemini CLI

This starter profile gives Gemini CLI durable local memory through MCP. It is
provider-neutral: the same local Lians store can support other MCP clients, so
memory is not locked to Gemini or its model provider.

## Install

1. Install [`uv`](https://docs.astral.sh/uv/).
2. Merge [`settings.example.json`](settings.example.json) into
   `~/.gemini/settings.json`.
3. Restart Gemini CLI, then run `/mcp list` to confirm that `lians` is connected.

The Community starter profile exposes only two tools:

- `remember` stores durable information that should survive future sessions.
- `recall` retrieves a small, relevant slice instead of resending an entire
  conversation or knowledge dump.

`trust` is disabled so Gemini asks before using the tools. Local mode needs no
Lians account or API key and persists to `~/.lians/mcp.db`.

## What this does and does not promise

Lians can reduce repeated input context when a workflow repeatedly needs facts
that fit in a smaller recall result. It does not enlarge Gemini's context
window, increase Google quotas, or guarantee lower token use on every task.

Lians Cloud and enterprise packages add the managed service boundary: continuity
across clients and devices, shared/team memory, higher managed limits,
administration, evidence operations, and support. The public/paid boundary is
documented in [`docs/community-cloud-boundary.md`](../../docs/community-cloud-boundary.md).

Repository: https://github.com/Lians-ai/Lians

Website: https://www.lians.ai
