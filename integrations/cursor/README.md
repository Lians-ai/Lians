# Lians Memory for Cursor

Give Cursor a durable local memory without changing your model or editor. The
starter profile remembers important project facts and recalls a small relevant
slice in a later chat.

## Install for one project

Use Cursor's official one-click MCP installer:

[<img src="https://cursor.com/deeplink/mcp-install-dark.svg" alt="Install Lians in Cursor">](https://cursor.com/en/install-mcp?name=Lians&config=eyJjb21tYW5kIjoidXZ4IiwiYXJncyI6WyItLWZyb20iLCJsaWFucy1zZGtbbWNwXSIsImxpYW5zLW1jcCJdLCJlbnYiOnsiTElBTlNfTUNQX0VOQUJMRURfVE9PTFMiOiJyZW1lbWJlcixyZWNhbGwsbGlzdF9tZW1vcmllcyxjb3JyZWN0X21lbW9yeSxmb3JnZXRfbWVtb3J5In19)

The button passes the same local server configuration shown below to Cursor.
Review it in Cursor before approving the install.

For manual setup:

1. Install [`uv`](https://docs.astral.sh/uv/).
2. Copy [`mcp.example.json`](mcp.example.json) to `.cursor/mcp.json` in your
   project. If that file already exists, merge the `lians` server into its
   `mcpServers` object.
3. Open Cursor's MCP settings, confirm that `lians` is connected, and leave tool
   approval enabled while testing.

To make Lians available in every Cursor project, merge the same server into
`~/.cursor/mcp.json` instead.

Local mode needs no Lians account or API key. Memories persist in
`~/.lians/mcp.db` by default.

## Test it in two chats

In one Cursor chat, ask:

```text
Remember that this project's release color is amber.
```

Open a new chat in the same project and ask:

```text
What is this project's release color?
```

Approve the `remember` or `recall` tool when Cursor asks. The starter profile
exposes only those two tools so the first-run surface stays understandable and
bounded.

## Optional managed connection

The example defaults to a local SQLite store. A hosted or self-hosted Lians
server can be selected by adding `LIANS_URL`, `LIANS_API_KEY`, and optionally
`LIANS_AGENT_ID` to the server's `env` object. Keep credentials in a user-level
configuration or secret manager; never commit them to the repository.

Lians Cloud and enterprise packages add hosted continuity across supported
clients and devices, shared/team memory, administration, higher managed limits,
evidence operations, and support. The exact public/paid boundary is documented
in [`docs/community-cloud-boundary.md`](../../docs/community-cloud-boundary.md).

## Scope

Lians can reduce repeated input context when a smaller relevant recall replaces
material that would otherwise be resent. It does not enlarge Cursor's context
window, change model quotas, or guarantee lower token use on every task.
