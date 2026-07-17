# Lians Agent Memory

## Tagline

Local-first, bitemporal memory for agents that need to know what was known when.

## Description

Lians is an open-source MCP memory server for agents that work with facts that
change over time. It stores valid time and system time separately, retrieves
knowledge as of a requested point in time, records supersession without erasing
history, exposes conflict and lineage evidence, and checks backtests for future
information leakage. The default local mode uses SQLite and requires no hosted
account or API key.

## Setup Requirements

- `uv` is required so the `uvx` command is available: https://docs.astral.sh/uv/getting-started/installation/
- No environment variables, credentials, or hosted Lians account are required for local mode.
- `LIANS_LOCAL_DB` is optional and sets an explicit SQLite database path.

## Category

AI & ML

## Features

- Store and retrieve persistent agent memories
- Retrieve facts as they were known at a requested point in time
- Supersede outdated facts while retaining full history
- Reconstruct the complete knowledge state at a historical time
- Inspect conflicts and supporting evidence
- Trace memory lineage and fact history
- Detect lookahead leakage in time-sensitive agent backtests
- Run locally with SQLite and no API key
- Connect through any stdio-compatible MCP client

## Getting Started

- "Remember that Acme's credit rating changed from A to BBB on July 1."
- "What did we know about Acme's credit rating on June 30?"
- "Show the history and lineage for Acme's credit rating."
- "Check whether this backtest used information before it became available."
- Tool: `remember`, stores a fact with temporal and evidence metadata
- Tool: `recall`, retrieves current knowledge
- Tool: `recall_at`, retrieves knowledge at a point in time
- Tool: `reconstruct`, rebuilds a historical knowledge state
- Tool: `list_conflicts`, exposes unresolved contradictions
- Tool: `memory_lineage`, traces source and supersession relationships
- Tool: `fact_history`, returns a fact's recorded history
- Tool: `backtest_check`, checks for future information leakage

## Tags

agent-memory, memory, mcp, bitemporal, temporal, point-in-time, audit, lineage, supersession, local-first, sqlite, backtesting, compliance

## Documentation URL

https://github.com/Lians-ai/Lians#readme
