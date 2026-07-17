# Lians plugin for Claude Code

This plugin gives Claude Code commands and an agent for working with Lians memory.

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
