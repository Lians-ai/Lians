# Lians Agent Memory MCPB

This directory packages Lians - the memory tool for any AI agent - as a local MCP
bundle. It gives a compatible host durable remember, recall, inspect, correct,
and confirmed-forget tools.

For a normal desktop user, the [guided Lians Easy installer](../../docs/easy-install.md)
is the preferred path. This bundle is the host-managed package path.

The default configuration stores data in `~/.lians/mcp.db`. It requires no API
key, hosted service, or Docker process.

Build from this directory:

```bash
uv lock
npx -y @anthropic-ai/mcpb pack
```

The lockfile is generated only after the exact `lians-sdk` version pinned in
`pyproject.toml` is available on PyPI. This prevents a new bundle version from
silently packaging an older SDK while a release is still in progress.

Publish to Smithery:

```bash
smithery mcp publish ./lians-agent-memory.mcpb \
  -n info-2zyf/lians-agent-memory
```
