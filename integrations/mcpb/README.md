# Lians Agent Memory MCPB

This directory packages the published `lians-sdk` MCP server as a local MCP
bundle. The host-managed UV runtime installs the pinned package and starts the
same `lians-mcp` implementation documented in the repository root.

The MCPB is intentionally released after the registry SDK it embeds. Its bundle
version is independent of the lock-step platform release, and its lockfile must
continue to reference the last verified public `lians-sdk` until the new SDK is
actually available from PyPI. The current 0.4.1 bundle therefore remains pinned
to SDK 0.4.1 while the 0.5.0 registry artifacts are being prepared; do not create
an unresolvable 0.5.0 lockfile ahead of publication.

The default configuration stores data in `~/.lians/mcp.db`. It requires no API
key, hosted service, or Docker process.

Build from this directory:

```bash
npx -y @anthropic-ai/mcpb pack
```

Publish to Smithery:

```bash
smithery mcp publish ./lians-agent-memory.mcpb \
  -n info-2zyf/lians-agent-memory
```
