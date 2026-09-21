# Codex bridge: one mission, one thread

Lians does not replace Codex's model, authentication, tools, or interface. It
adds two open-source layers around Codex:

```text
Lians MCP/plugin           durable state, corrections, and proof tools in Codex
            ↓
Codex app-server           one real Codex thread with streamed turns and usage
            ↓
Lians application          mission, model policy, verifier, and signed receipt
```

## The gap this closes

The previous Lians adapter launched a new `codex exec` process for
every stage and copied the prior model's final message into the next prompt.
That was auditable, but it repeated context and reduced a mission to summaries.

Version 0.11.0 starts one app-server process and one Codex thread for a mission.
Discovery, implementation, and any repair or review are turns in that thread.
The model, reasoning effort, working directory, approval policy, and sandbox
remain explicit per turn. Because Codex owns the thread, its configured MCP
servers, plugins, skills, repository instructions, and local authentication
remain available.

## Compatibility and authority

- App-server is feature-detected. Older Codex installs use `codex exec`.
- `LIANS_CODEX_BRIDGE=exec` or `--codex-bridge exec` forces the compatibility
  path. `app-server` forces the new path and fails clearly if unavailable.
- Lians requests no interactive approvals. An unexpected background
  approval or permission request is declined rather than silently expanding
  authority.
- Discovery receives a read-only sandbox. Implementation and repair receive
  workspace-write access to the selected repository with network disabled.
- One app-server thread persists in Codex history for one mission. Its thread
  identifier is stored only in the local job record, not in the beta export.

Codex currently marks app-server experimental. Lians therefore calls this a
beta bridge, not a stable provider guarantee. The one-shot runner remains the
fallback until the app-server interface is declared stable and the external
proof gate passes.

## Add the open-source Lians layer to Codex

Install the local MCP memory server once:

```bash
codex mcp add lians --env LIANS_MCP_ENABLED_TOOLS=remember,recall,list_memories,correct_memory,forget_memory -- uvx --from "lians-sdk[mcp]" lians-mcp
```

Restart Codex and run `codex mcp list`. The packaged
[`lians-memory`](../../plugins/lians-memory) plugin adds the fuller Codex-native
workflow, including skills and lifecycle hooks. Lians works without
that plugin, but the plugin is how the open-source state layer follows the user
inside the Codex app, CLI, and IDE.

## What the beta must prove

The bridge is technically useful only if it changes the user's outcome. The
proof gate therefore measures matched tasks, verifier results, observed
premium calls, later-day reuse, and willingness to pay. It does not count an
initialize handshake or a passing unit test as market proof. Follow
[BETA-PROOF-GATE.md](BETA-PROOF-GATE.md).

## Official Codex surfaces used

- [Codex app-server](https://learn.chatgpt.com/docs/app-server)
- [Model Context Protocol in Codex](https://learn.chatgpt.com/docs/extend/mcp?surface=cli)
- [Codex as a platform](https://developers.openai.com/blog/codex-as-a-platform)
- [Codex plugin architecture](https://developers.openai.com/plugins/concepts/plugins?site_locale=en)
