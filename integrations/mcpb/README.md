# Lians Agent Memory MCPB

This directory packages the published `lians-sdk` MCP server as a local MCP
bundle. The host-managed UV runtime installs the pinned package and starts the
same `lians-mcp` implementation documented in the repository root.

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
