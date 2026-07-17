---
name: lians-memory
description: Use Lians memory from Claude Code for current-state recall, point-in-time reconstruction, lineage inspection, lookahead checks, and explicitly confirmed erasure.
---

# Lians memory

Use the Lians MCP tools when the user wants persistent agent memory or needs to
inspect how a remembered fact changed over time.

## Connection

Prefer local SQLite mode when no hosted connection is configured. It requires no
API key and can be launched with:

```bash
uvx --from "lians-sdk[mcp]" lians-mcp
```

Use `LIANS_URL` and `LIANS_API_KEY` only when the user supplies or configures a
hosted or self-hosted endpoint.

## Operating rules

1. Use current recall for the latest non-superseded state.
2. Use point-in-time recall when the request contains an as-of date or asks what
   was known before a later event.
3. Report timestamps, sources, lineage, and verification results exactly as the
   tools return them.
4. Do not infer that a hash-chain check proves legal or regulatory compliance.
5. Require an explicit request reference and user confirmation before erasure.
6. Do not reconstruct content reported as erased or unreadable.
7. Run the lookahead check before relying on memory in a historical simulation.

## Commands

Use the bundled commands for guided workflows:

- `/lians-remember`
- `/lians-recall`
- `/lians-audit`
- `/lians-integrate`
