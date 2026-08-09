# Lians for Gemini CLI

Lians gives Gemini CLI persistent, provider-neutral memory through its native
Model Context Protocol (MCP) client. The default profile exposes only
`remember`, `recall`, and `recall_at` so ordinary turns do not carry the schemas
for five audit-only tools.

## Managed setup

1. Install Gemini CLI and `uv`/`uvx`.
2. Copy `settings.example.json` into your project as `.gemini/settings.json`, or
   merge its `mcpServers.lians` object into `~/.gemini/settings.json` for every
   project.
3. Replace `https://your-lians.example` with an operator-supplied deployed
   HTTPS endpoint, then set `LIANS_API_KEY` in your environment. Do not paste
   the key into the JSON file. This repository does not currently claim a live
   public managed endpoint. The profile derives an isolated agent and namespace
   from the project working directory.
4. Copy or merge this directory's `GEMINI.md` into the project-root `GEMINI.md`.
5. Restart Gemini CLI, then run `gemini mcp list`. In an interactive session,
   `/tools` should show `mcp_lians_remember`, `mcp_lians_recall`, and
   `mcp_lians_recall_at`.

Gemini CLI expands `$VARIABLE`, `${VARIABLE}`, and `%VARIABLE%` in settings
files. It does not implement shell-style `${VARIABLE:-default}` expansion, so
the example keeps non-secret defaults as explicit JSON strings and expands only
the required API key. To configure non-secret values through the environment,
replace an explicit value with one of Gemini's supported variable forms and set
that variable before starting Gemini CLI. The server alias is `lians` (without
underscores) because Gemini's MCP policy engine parses the alias from fully
qualified tool names.

## Local setup

For a private prototype with no Lians API key, use
`settings.local.example.json`. It stores data in `~/.lians/mcp.db` by default.
Both examples set `cwd` and `LIANS_MCP_PROJECT_ROOT` to `.`, so the updated SDK
hashes the project root into default agent and namespace values. To explicitly
share memory across Gemini, Claude, and Codex, use the same absolute
`LIANS_LOCAL_DB`, `LIANS_NAMESPACE`, and `LIANS_AGENT_ID` values in every host;
that cross-project sharing is opt-in.

Public SDK 0.5.0 does not derive the project hash. When using that package, set
project-unique `LIANS_AGENT_ID` and `LIANS_NAMESPACE` values; their empty
expansions otherwise make legacy calls fail rather than sharing a global scope.
The local example also clears `LIANS_URL` and `LIANS_API_KEY` explicitly so an
inherited shell environment cannot silently switch it to remote mode.

Local SQLite mode is appropriate for personal evaluation, not production
secrets or multi-process workloads. Use managed Lians or a self-hosted server
for a production credential and encryption boundary.

The first local start may download and load the embedding model. The five-minute
MCP timeout covers that cold start. A warm server should be used for latency
measurements.

## Low-context policy

The included `GEMINI.md` is intentionally short. With the updated SDK it makes a
single top-50 recall bounded to 2,650 estimated tokens the default, narrows by
metadata when possible, and skips Lians for self-contained prompts. Public SDK
0.5.0 does not implement that cap. The cap was chosen near the recorded
top-50 LOCOMO mean; the recorded 90.0% quality result used all 50 memories, so
the exact production renderer and cap remain an unvalidated quality profile.

The product target is 85% more same-budget usage, or 1.85x comparable tasks. It
requires at least 45.95% lower measured per-task cost after protected quality
passes; it does not mean an 85% token reduction. Separately, the recorded
top-50 LOCOMO run used 85.4% less answer context on average. Neither result
quality-validates the exact capped Gemini profile or guarantees Gemini savings.
History length, system/tool prefixes, caching, model, and task all affect the
provider measurement.

Measure the target with paired tasks:

1. Run the same representative task set with full history and without Lians.
2. Run fresh sessions with Gemini's three-tool allowlist and, on a compatible
   updated SDK, bounded top-50 recall.
3. Compare answer correctness first, then input tokens using `/stats model`.
4. Report uncached and cached input separately when API-key or Vertex AI token
   caching is active, plus tool latency and output tokens.
5. Promote the profile only if protected-task quality passes and the measured
   per-task cost is at most 54.05% of baseline for the 1.85x target.

Do not infer Gemini savings from Codex or Claude results; each host has a
different prompt and tool prefix.

The public `lians-sdk` 0.5.0 package predates the bounded MCP response path.
Before the next SDK is published, checkout validation should replace the
`--from` value with a PEP 508 direct reference that retains the MCP extra:

```text
lians-sdk[mcp] @ file:///absolute/path/to/Lians/agentmem/sdk/python
```

## Readiness and troubleshooting

- `gemini mcp list` must report `lians` as connected before benchmarking.
- If discovery times out on first local start, run the configured `uvx` command
  once to populate its cache, then retry.
- Leave `trust` set to `false`. Recall tools are read-only, while `remember`
  persists data and should remain confirmable.
- Gemini project settings override user settings. If a user-level server seems
  absent, inspect `.gemini/settings.json` for a conflicting `lians` entry and
  run `/mcp list` for diagnostics.

Configuration details are based on Google's current
[Gemini CLI MCP documentation](https://geminicli.com/docs/tools/mcp-server/) and
[settings reference](https://geminicli.com/docs/reference/configuration/).
