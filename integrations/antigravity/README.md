# Lians Memory for Antigravity CLI

Antigravity CLI is Google's supported consumer successor to Gemini CLI. Lians
adds durable local memory through MCP, so useful context can survive a new
conversation or a switch to another Lians-enabled client.

## Install with Lians Easy

Select **Antigravity CLI** in Lians Easy, or run:

```bash
LiansMemory install --clients antigravity --yes --json
```

Restart Antigravity CLI and run `/mcp` to confirm that `lians` is available.
The installer backs up and safely merges the global configuration at
`~/.gemini/config/mcp_config.json`.

## Manual setup

1. Install [`uv`](https://docs.astral.sh/uv/).
2. Merge [`mcp_config.example.json`](mcp_config.example.json) into
   `~/.gemini/config/mcp_config.json`.
3. Restart Antigravity CLI and run `/mcp`.

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
