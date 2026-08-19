# Lians quickstart

Give an AI coding tool one project fact, open a fresh chat, and confirm that the
fact is recalled. The local setup requires no Lians account or provider API key.

## 1. Choose a client

### Codex

Install [`uv`](https://docs.astral.sh/uv/getting-started/installation/), then run:

```bash
codex mcp add lians --env LIANS_MCP_ENABLED_TOOLS=remember,recall,list_memories,correct_memory,forget_memory -- uvx --from "lians-sdk[mcp]" lians-mcp
```

Restart Codex. Run `codex mcp list` or type `/mcp` in the terminal UI to confirm
that `lians` is connected.

### Claude Code

Run these commands inside Claude Code:

```text
/plugin marketplace add Lians-ai/Lians
/plugin install lians@lians-plugins
```

Restart Claude Code after installation.

### Cursor

Use the [one-click Cursor installer](../integrations/cursor), review the local
MCP configuration, and approve it in Cursor.

For another MCP client, follow the [generic MCP setup](install.md#existing-ai-client-use-mcp).

## 2. Save one safe project fact

In a project chat, say:

```text
Remember that this project uses Python 3.12 and pytest.
```

Approve the `remember` tool if your client asks. Do not use passwords, API keys,
personal data, or other secrets as test values.

## 3. Recall it in a fresh chat

Open a new chat in the same project and ask:

```text
What Python version and test runner does this project use?
```

Approve the `recall` tool if prompted. The answer should mention Python 3.12
and pytest without requiring the previous transcript.

## 4. Correct the memory

Say:

```text
Correct that memory: this project now uses Python 3.13 and pytest.
```

Open another fresh chat and ask the same question. Current-state recall should
use Python 3.13 rather than presenting Python 3.12 as current.

## 5. Inspect or delete saved memory

Ask your AI tool to list the project's Lians memories. To remove the test fact,
ask it to permanently forget that memory and confirm the deletion when prompted.

Deletion is intentionally explicit. Confirm that the correct memory reference
is selected before approving it.

## Where local data is stored

The basic MCP setup stores local memory in `~/.lians/mcp.db`. Set
`LIANS_LOCAL_DB` in the MCP server environment to choose another location.

Lians does not ask for your Claude, Cursor, or Codex password or provider API
key. The first use may download a local semantic model.

## Troubleshooting

- Confirm that `uvx` runs in a terminal.
- Restart the AI client after changing its MCP configuration.
- Confirm that the `lians` or `lians-memory` server is connected in the client.
- Allow extra time during the first run while the local model initializes.
- If a write times out during initialization, retry it after the server is ready;
  the timed-out write is not queued.

For client-specific details, see the [Codex](../integrations/codex),
[Claude Code](../integrations/lians-plugin), and
[Cursor](../integrations/cursor) guides.
