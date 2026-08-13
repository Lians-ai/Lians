# Install Lians

For a normal user, prefer the standalone **LiansMemory** app from GitHub
Releases. It detects supported AI clients, backs up their configuration, and
installs local memory without Python, `uv`, an account, or a model download.

The instructions below are the full-engine path for developers.

Lians runs as a local stdio MCP server. The default configuration uses SQLite,
requires no API key, and stores data on the local machine.

## Prerequisite

Install `uv` so the `uvx` command is available:

```text
https://docs.astral.sh/uv/getting-started/installation/
```

## MCP configuration

Add this server entry to the MCP configuration used by your client:

```json
{
  "mcpServers": {
    "lians": {
      "command": "uvx",
      "args": ["--from", "lians-sdk[mcp]", "lians-mcp"],
      "env": {
        "LIANS_MCP_ENABLED_TOOLS": "remember,recall,list_memories,correct_memory,forget_memory"
      }
    }
  }
}
```

Restart the client after saving the configuration. The server should expose
the five core memory tools. The allowlist hides advanced audit tools from the
starter surface.

## Direct verification

To start the stdio server directly:

```bash
uvx --from 'lians-sdk[mcp]' lians-mcp
```

The process waits for MCP messages on standard input. This is expected. Stop it
with `Ctrl+C` when testing from a terminal.

## Persistent local data

By default, Lians stores its SQLite database in the user's local data directory.
Set `LIANS_LOCAL_DB` only when a specific database path is needed:

```json
{
  "mcpServers": {
    "lians": {
      "command": "uvx",
      "args": ["--from", "lians-sdk[mcp]", "lians-mcp"],
      "env": {
        "LIANS_LOCAL_DB": "/absolute/path/to/lians-mcp.db"
      }
    }
  }
}
```

Use an absolute path appropriate for the operating system. The explicit path is
optional and no hosted Lians account is required.
