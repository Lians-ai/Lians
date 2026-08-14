# Lians Memory for Cursor

Give Cursor a durable local memory without changing your model or editor. The
starter profile remembers important project facts and recalls a small relevant
slice in a later chat.

## Install for one project

Use Cursor's official one-click MCP installer:

[<img src="https://cursor.com/deeplink/mcp-install-dark.svg" alt="Install Lians in Cursor">](https://cursor.com/en/install-mcp?name=lians-memory&config=eyJjb21tYW5kIjoidXZ4IiwiYXJncyI6WyItLWZyb20iLCJsaWFucy1lYXN5IEAgaHR0cHM6Ly9naXRodWIuY29tL0xpYW5zLWFpL0xpYW5zL2FyY2hpdmUvZGM5NGQ2YmZiODk0ZTVlMjViZjU1OTY5NzM4NGI1OGNlZWRkNDM0Yi56aXAjc3ViZGlyZWN0b3J5PXBhY2thZ2VzL2xpYW5zLWVhc3kiLCJsaWFucy1lYXN5IiwibWNwIl19)

The button passes the same local server configuration shown below to Cursor.
Review it in Cursor before approving the install.

This directory is also a native Cursor plugin package, registered by the
repository's [Cursor marketplace manifest](../../.cursor-plugin/marketplace.json).
It bundles the same MCP server plus memory-use guidance and explicit inspect,
correct, and forget controls. The package is ready for repository validation;
its presence here does not mean Cursor has approved or listed it in the public
Marketplace yet.

The native package runs the dependency-free Lians Easy MCP runtime from an
immutable GitHub source archive. It requires `uv`, but it does not require Git,
Python, a Lians account, or an API key.

For manual setup:

1. Install [`uv`](https://docs.astral.sh/uv/).
2. Copy [`mcp.example.json`](mcp.example.json) to `.cursor/mcp.json` in your
   project. If that file already exists, merge the `lians-memory` server into its
   `mcpServers` object.
3. Open Cursor's MCP settings, confirm that `lians-memory` is connected, and
   leave tool approval enabled while testing.

To make Lians available in every Cursor project, merge the same server into
`~/.cursor/mcp.json` instead.

Local mode needs no Lians account or API key. Lians Easy uses one OS-native
database for its default `personal` profile:

- Windows: `%LOCALAPPDATA%\Lians\memory.sqlite3`
- macOS: `~/Library/Application Support/Lians/memory.sqlite3`
- Linux: `${XDG_DATA_HOME:-~/.local/share}/Lians/memory.sqlite3`

Point every supported AI client at Lians Easy to share that profile across
clients on the same machine. Lians Easy stores memory content as local SQLite
plaintext, so rely on OS account and disk protection and do not save secrets.
Use the full encrypted Lians deployment path for regulated or sensitive data.

## Test it in two chats

In one Cursor chat, ask:

```text
Remember that this project's release color is amber.
```

Open a new chat in the same project and ask:

```text
What is this project's release color?
```

Approve the `remember` or `recall` tool when Cursor asks. The package exposes
five understandable controls: remember, recall, list, correct, and confirmed
permanent forget.

## Optional managed connection

The native consumer package is local-only. For a hosted or self-hosted Lians
deployment, use the [full SDK MCP setup](../../README.md#free-local-setup-add-memory-through-mcp),
then supply `LIANS_URL`, `LIANS_API_KEY`, and optionally `LIANS_AGENT_ID` in a
user-level configuration or secret manager. Never commit those credentials.

Lians Cloud and enterprise packages add hosted continuity across supported
clients and devices, shared/team memory, administration, higher managed limits,
evidence operations, and support. The exact public/paid boundary is documented
in [`docs/community-cloud-boundary.md`](../../docs/community-cloud-boundary.md).

## Scope

Lians can reduce repeated input context when a smaller relevant recall replaces
material that would otherwise be resent. It does not enlarge Cursor's context
window, change model quotas, or guarantee lower token use on every task.
