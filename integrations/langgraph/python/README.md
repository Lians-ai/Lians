# Lians memory for LangGraph

This example gives a LangGraph workflow durable local memory without an LLM,
API key, or hosted service. The same compiled graph runs twice: its first
invocation remembers a project fact, and its second invocation recalls that
fact for the same agent.

```text
invoke #1: remember "Project Aurora deploys with Python 3.12."
                              |
                              v
                    local SQLite memory
                              |
                              v
invoke #2: recall "Which Python version does Project Aurora deploy with?"
```

The graph uses Lians' tested `create_remember_node` and `create_recall_node`
factories. A small conditional entry point selects the requested operation for
each invocation, while both nodes share one `LocalLiansClient` and `agent_id`.

## Run it

From this directory:

```bash
uv sync
uv run python main.py
```

Expected output:

```text
Remembered: Project Aurora deploys with Python 3.12.
Recalled later: ['Project Aurora deploys with Python 3.12.']
```

Run the focused offline verification:

```bash
uv run pytest
uv run ruff check .
```

No Lians account, hosted service, model provider, or API key is required. The
demo stores SQLite data under `.data/memory.db`; the test uses its own temporary
SQLite path and does not alter the demo database.

## Production note

The example explicitly selects Lians' deterministic local embedding provider
so the workflow remains credential-free and reproducible. Configure the
embedding provider appropriate for your application's retrieval needs when
moving beyond the example.

For framework-independent `remember` and `recall` tools that work with any MCP
host, see the repository's [MCP setup](../../../README.md#developer-setup-add-memory-through-mcp).
