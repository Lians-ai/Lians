# Lians for Claude Code

This plugin helps Claude Code reuse useful project context without asking you to
repeat it. It adds simple remember and recall commands while keeping the
advanced memory and evidence tools available when a project needs them.

## Install from GitHub

```text
/plugin marketplace add Lians-ai/Lians
/plugin install lians@lians-plugins
```

Restart Claude Code after installation. The repository is the distribution
source; no copy-and-paste command files are required.

## Capabilities

- Store and recall agent memory
- Suppress superseded facts during current-state recall
- Reconstruct what was known at a requested time
- Inspect lineage and fact history
- Check historical simulations for lookahead contamination
- Request erasure with an explicit reference and confirmation

## Local setup

Local SQLite mode requires no API key:

```bash
uvx --from "lians-sdk[mcp]" lians-mcp
```

The same command is published in the official MCP Registry under
`io.github.ebeirne/lians`.

## Plugin components

- `/lians-remember`
- `/lians-recall`
- `/lians-audit`
- `/lians-integrate`
- `lians-compliance` agent for evidence-oriented memory operations
- `lians-memory` skill for setup and safe operation

Repository: https://github.com/Lians-ai/Lians

License: Apache-2.0

The plugin is self-managed Community software. Hosted continuity across
clients and devices, team memory, higher managed limits, administration,
managed evidence operations, and support are commercial Lians services. See
the [public/paid boundary](../../docs/community-cloud-boundary.md).
