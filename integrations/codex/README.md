# Lians for Codex

Give Codex memory across tasks without changing the model or your workflow.
The default setup runs locally, needs no Lians account or API key, and exposes
only `remember` and `recall`.

## Install

1. Install [`uv`](https://docs.astral.sh/uv/getting-started/installation/).
2. Copy the block from [`config.example.toml`](config.example.toml) into
   `~/.codex/config.toml`.
3. Restart Codex.

Try:

```text
Remember that this repository uses Python 3.12 and pytest.
```

Then open another task and ask:

```text
What Python version and test runner does this repository use?
```

Memory is stored in `~/.lians/mcp.db`. Set `LIANS_LOCAL_DB` if you want a
different location.

## Add project instructions

Copy [`AGENTS.md`](AGENTS.md) to a project root, or merge its memory rules into
an existing `AGENTS.md`. It tells Codex what is worth remembering and what
should never be stored.

## Install the Lians plugin

The repository also includes the [`lians-memory`](../../plugins/lians-memory)
plugin for a packaged Codex experience:

```text
git clone https://github.com/Lians-ai/Lians.git
codex plugin marketplace add /absolute/path/to/Lians
codex plugin add lians-memory@lians
```

## Advanced tools

Replace `lians-memory-mcp` with `lians-mcp` in the MCP configuration to
expose point-in-time recall, reconstruction, conflicts, lineage, fact history,
feedback, and backtest checks.

For the complete product overview, see the [root README](../../README.md).
