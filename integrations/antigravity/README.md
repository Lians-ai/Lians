# Lians Memory for Antigravity CLI

Antigravity CLI is Google's supported consumer successor to Gemini CLI. Lians
adds durable local memory through MCP, so useful context can survive a new
conversation or a switch to another Lians-enabled client.

> **Verified route:** Antigravity CLI 1.1.13 exposed and invoked Lians tools
> when Lians was packaged as an Antigravity plugin. The global
> `~/.gemini/config/mcp_config.json` route discovered schemas but did not expose
> a usable tool in a fresh agent session, matching Google's open
> [MCP invocation issue #71](https://github.com/google-antigravity/antigravity-cli/issues/71).
> Lians Easy therefore installs the plugin route.

## Install with Lians Easy

Select **Antigravity CLI** in Lians Easy, or run:

```bash
LiansMemory install --clients antigravity --yes --json
```

Restart Antigravity CLI, then run `agy plugin list`. The `lians-memory` entry
should report `mcpServers` in its components. The installer backs up and safely
merges these files:

- `~/.gemini/config/plugins/lians-memory/plugin.json`
- `~/.gemini/config/plugins/lians-memory/mcp_config.json`
- `~/.gemini/config/plugins.json`

## Manual setup

1. Install [`uv`](https://docs.astral.sh/uv/).
2. Create `~/.gemini/config/plugins/lians-memory/`.
3. Copy [`plugin.example.json`](plugin.example.json) to `plugin.json` and
   [`mcp_config.example.json`](mcp_config.example.json) to `mcp_config.json`
   inside that directory.
4. Add the absolute `~/.gemini/config/plugins` path to
   `~/.gemini/config/plugins.json`, with `lians-memory` in `include_only`.
   Antigravity rejects `~` in this registry path; it must be absolute.
5. Restart Antigravity CLI and run `agy plugin list`.

The starter exposes the memory lifecycle through `remember`, `recall`,
`list_memories`, `correct_memory`, and explicitly confirmed `forget_memory`.
Local mode needs no Lians account or API key. The same local database can be
used by other MCP clients configured through Lians Easy.

## Gemini CLI users

Google ended consumer Google-login access to Gemini CLI on June 18, 2026. Use
Antigravity CLI for a consumer Google account. Gemini CLI remains a separate
Lians target for supported Standard or Enterprise subscriptions, API keys, and
Vertex AI configurations. See Google's
[consumer-account deprecation notice](https://developers.google.com/gemini-code-assist/docs/deprecations/code-assist-individuals)
for the current boundary.

## Claim boundary

Lians can reduce repeated input context when a workflow repeatedly needs facts
that fit in a smaller recall result. It does not enlarge a model's context
window, increase Google quotas, or guarantee lower token use on every task.
The verified lifecycle and host-token measurements are recorded in
[`docs/benchmarks/antigravity-cli-2026-08-14.md`](../../docs/benchmarks/antigravity-cli-2026-08-14.md).
