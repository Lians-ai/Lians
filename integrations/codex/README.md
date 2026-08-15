# Lians Memory for Codex

Give the Codex app, CLI, and IDE extension durable local memory. They share the
same MCP configuration, so you only need to add Lians once.

## Install in one command

1. Install [`uv`](https://docs.astral.sh/uv/getting-started/installation/).
2. Run this command in a terminal:

```bash
codex mcp add lians -- uvx --from "lians-sdk[mcp]" lians-memory-mcp
```

3. Restart Codex. Run `codex mcp list` to confirm that `lians` is configured,
   or type `/mcp` in the Codex terminal UI to see the connected server.

This follows Codex's official
[MCP CLI configuration](https://learn.chatgpt.com/docs/extend/mcp?surface=cli).
Local mode needs no Lians account or API key. Memories persist in
`~/.lians/mcp.db` by default.

## Test it in two chats

In one Codex chat, ask:

```text
Remember that this project's release color is amber.
```

Open a new chat in the same project and ask:

```text
What is this project's release color?
```

Approve the `remember` or `recall` tool if Codex asks. The published starter
wrapper exposes exactly those two tools.

## Optional managed connection

The command defaults to a local SQLite store. If you prefer a managed private
workspace and setup support, [Lians Personal](https://www.lians.ai/upgrade?plan=starter&utm_source=github&utm_medium=integration_guide&utm_campaign=codex_setup)
is $10/month. The free local version remains available without an account.

## Optional tuning

- Set `LIANS_LOCAL_DB` if you want the local database somewhere other than
  `~/.lians/mcp.db`.
- Copy the example [`AGENTS.md`](AGENTS.md) into a project to tell Codex what is
  worth remembering and what should never be stored.
- Replace `lians-memory-mcp` with `lians-mcp` in the MCP configuration to expose
  point-in-time recall, reconstruction, conflicts, lineage, feedback, and
  backtest checks.
- See the packaged [`lians-memory`](../../plugins/lians-memory) plugin for the
  full Codex workflow.
