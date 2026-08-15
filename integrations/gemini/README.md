# Lians Memory for Gemini CLI

This starter profile gives Gemini CLI durable local memory through MCP. It is
provider-neutral: the same local Lians store can support other MCP clients, so
memory is not locked to Gemini or its model provider.

> **Consumer account notice:** Google ended consumer Google-login access to
> Gemini CLI on June 18, 2026. Consumer users should follow the
> [Antigravity CLI integration](../antigravity/). This Gemini CLI path remains
> applicable to supported Standard or Enterprise subscriptions, Gemini API
> keys, and Vertex AI configurations. See Google's
> [deprecation notice](https://developers.google.com/gemini-code-assist/docs/deprecations/code-assist-individuals).

## Install

1. Install [`uv`](https://docs.astral.sh/uv/).
2. Install the extension:

   ```bash
   gemini extensions install https://github.com/Lians-ai/Lians
   ```

3. Restart Gemini CLI, then run `/mcp list` to confirm that `lians` is connected.

For a manual setup, merge [`settings.example.json`](settings.example.json)
into `~/.gemini/settings.json`.

### Workspace trust

Gemini CLI intentionally does not start user-level stdio MCP servers from an
untrusted folder. If `/mcp list` or `gemini mcp list` shows `lians` as disabled
or disconnected after installation, review the current folder and run
`gemini trust` to trust that workspace interactively, then check the list again.

For a single session or a disposable test environment, Gemini also supports
`--skip-trust` and `GEMINI_CLI_TRUST_WORKSPACE=true`. These options bypass the
folder trust check, so do not use them as a blanket replacement for reviewing
and trusting the intended workspace. See Gemini CLI's
[MCP server documentation](https://geminicli.com/docs/tools/mcp-server/#listing-servers-gemini-mcp-list)
and [configuration reference](https://github.com/google-gemini/gemini-cli/blob/main/docs/reference/configuration.md#environment-variables-and-env-files).

The Community starter profile exposes only two tools:

- `remember` stores durable information that should survive future sessions.
- `recall` retrieves a small, relevant slice instead of resending an entire
  conversation or knowledge dump.

`trust` is disabled so Gemini asks before using the tools. Local mode needs no
Lians account or API key and persists to `~/.lians/mcp.db`.

## What this does and does not promise

Lians can reduce repeated input context when a workflow repeatedly needs facts
that fit in a smaller recall result. It does not enlarge Gemini's context
window, increase Google quotas, or guarantee lower token use on every task.

Lians Cloud and enterprise packages add the managed service boundary: continuity
across clients and devices, shared/team memory, higher managed limits,
administration, evidence operations, and support. The public/paid boundary is
documented in [`docs/community-cloud-boundary.md`](../../docs/community-cloud-boundary.md).

Repository: https://github.com/Lians-ai/Lians

Website: https://www.lians.ai
