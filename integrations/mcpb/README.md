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

## Claude Desktop core profile

The launcher requests the same low-context profile as the Claude Code plugin:
`remember`, `recall`, and `recall_at`, with up to 50 candidates compiled into a
maximum of 2,650 estimated tokens. Local model/query warmup runs in the
background after bounded runtime imports, so the first recall is colder than
later calls. Claude Desktop's Extensions page and extension logs are the source
of truth for readiness; warm the extension with a harmless recall before a
latency-sensitive workflow.

The currently verified 0.4.1 bundle remains pinned to SDK 0.4.1 and predates the
server-side tool filter and bounded-context variables. The launcher defaults are
forward-compatible, but exact three-tool filtering requires the updated SDK to
be published, pinned here, locked, rebuilt, and verified in a clean Claude
Desktop host. Until that release gate passes, use Claude Desktop's per-tool
toggles to keep only the three core tools enabled and do not describe the 0.4.1
bundle as exact-filtered.

Build from this directory:

```bash
npx -y @anthropic-ai/mcpb pack
```

Publish to Smithery:

```bash
smithery mcp publish ./lians-agent-memory.mcpb \
  -n info-2zyf/lians-agent-memory
```
