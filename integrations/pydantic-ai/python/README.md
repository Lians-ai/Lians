# Lians memory for Pydantic AI

This example gives a Pydantic AI agent durable local memory without changing
its model. It demonstrates a correction that should affect present-time recall
while preserving the earlier fact for an explicit point-in-time query.

```text
Friday estimate -- corrected to Monday -- current recall: Monday
        |
        +----------------------------- recall at noon: Friday
```

The example uses two ordinary Pydantic AI function tools backed by
`LocalLiansClient`:

- `recall_current_estimate` retrieves the currently valid fact.
- `recall_estimate_before_revision` reconstructs memory before the correction.

Pydantic AI's deterministic `TestModel` keeps the example reproducible and
free of provider credentials. Replace it with the model used by your
application; the Lians tool functions do not change.

## Run it

From this directory:

```bash
uv sync
uv run python main.py
```

Expected memory results:

```text
Current recall: ["Order 1842 shipping estimate changed to Monday"]
Historical recall: ["Order 1842 shipping estimate is Friday"]
```

Run the focused verification:

```bash
uv run pytest
uv run ruff check .
```

No Lians account, hosted service, or model-provider API key is required. The
demo stores SQLite data under `.data/`.

## Production note

The example explicitly selects Lians' deterministic hash embedding so tests
run without a model download. That provider is for examples and tests, not
semantic production retrieval. In an application, omit `embedding_provider`
to use the local sentence-transformer installed by `lians-sdk[local]`, or
configure another supported embedding provider.

For a simpler `remember` and `recall` tool surface that works with any MCP host,
see the repository's [MCP setup](../../../README.md#developer-setup-add-memory-through-mcp).
