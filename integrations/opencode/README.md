# Lians Memory for OpenCode

Give OpenCode durable local memory through its native MCP support. The same
Lians store can support other compatible agents, so your memory is not tied to
OpenCode or a model provider.

## Install

1. Install [`uv`](https://docs.astral.sh/uv/).
2. Merge [`opencode.example.json`](opencode.example.json) into your OpenCode
   configuration.
3. Restart OpenCode and run:

   ```bash
   opencode mcp list
   ```

   Confirm that `lians` is connected.

4. Try these prompts in separate chats:

   ```text
   Remember that this project uses Python 3.12 and pytest.
   ```

   ```text
   What Python version and test runner does this project use? Use Lians memory.
   ```

Local mode needs no Lians account or API key and stores memory in
`~/.lians/mcp.db`.

## What OpenCode can use

The starter profile exposes five tools:

- `remember` stores a durable fact, preference, constraint, or decision.
- `recall` retrieves a small set of relevant, current memories.
- `list_memories` lets you inspect what Lians currently knows.
- `correct_memory` replaces stale information without hiding its history.
- `forget_memory` permanently erases one memory after explicit confirmation.

Remove `LIANS_MCP_ENABLED_TOOLS` from the configuration when you want the
advanced temporal and audit tools too.

OpenCode documents local MCP servers in its
[MCP server guide](https://opencode.ai/docs/mcp-servers/). Lians follows that
documented `type`, `command`, `enabled`, and `environment` format.

Repository: https://github.com/Lians-ai/Lians
