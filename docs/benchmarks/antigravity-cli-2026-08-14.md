# Antigravity CLI compatibility check - 2026-08-14

This check used Antigravity CLI 1.1.13 on Windows with a fresh empty project,
the Lians Easy local MCP runtime, a disposable database, and narrowly scoped
MCP permissions. No dangerous global auto-approval was used.

## Result

The global `~/.gemini/config/mcp_config.json` route discovered Lians schemas but
did not expose an invocable Lians tool in a fresh safe session. Packaging the
same MCP server as an Antigravity plugin exposed `call_mcp_tool` and completed
the memory lifecycle across new conversations:

1. `remember` stored a disposable memory in 0.045 seconds.
2. A fresh conversation recalled it in 0.071 seconds.
3. `forget_memory` erased its content in 0.096 seconds.
4. A final fresh conversation returned `No relevant memories found` in 0.055
   seconds. The disposable database was then removed.

Antigravity changed the explicitly supplied memory payload to the single word
`Synthetic` before calling the tool. The lifecycle passed for the value the
host actually sent, but exact argument preservation did not pass. This is a
host-agent reliability boundary and should remain visible in compatibility
claims.

## Host token baseline

| Fresh Antigravity turn | Total tokens | Lians tool time |
| --- | ---: | ---: |
| Write | 45,485 | 0.045 s |
| Recall | 20,983 | 0.071 s |
| Forget | 22,883 | 0.096 s |
| Recall after forgetting | 21,942 | 0.055 s |

These totals are Antigravity host usage, not Lians context size. This run does
not prove token reduction. It shows that the MCP operations were fast while the
host's fixed prompt and reasoning overhead dominated the end-to-end token bill.
Future token claims should compare the same task with and without recalled
context under the same host, model, permissions, and prompt.

## Reproducibility boundary

- Host: Antigravity CLI 1.1.13
- OS: Windows
- Transport: local stdio MCP through an Antigravity plugin
- Memory runtime: Lians Easy local SQLite
- Isolation: fresh conversations in an empty project
- Cleanup: memory content erased, post-delete recall verified empty, disposable
  database removed

The plugin route is a compatibility workaround for Google's open
[Antigravity CLI issue #71](https://github.com/google-antigravity/antigravity-cli/issues/71),
which tracks custom MCP servers whose schemas are discovered but whose tools are
not invocable through the ordinary configuration path.
