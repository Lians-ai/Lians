# Lians Memory for Codex

Lians Memory is a distributable Codex plugin that recalls a bounded slice of
relevant memory before the first model request and exposes compact `remember`
and `recall` tools. Automatically injected hook context is marked as untrusted
data, score-gated, likely-secret redacted, and bounded before Codex sees it.
Explicit `recall` output is token-bounded and marked as untrusted data, but it
can return stored content; Codex must treat that content as evidence, never as
instructions.

## What we can honestly claim

A controlled checkout-hook ABBA run on one public LOCOMO question used
`gpt-5.6-sol` at Ultra effort. All four answers exactly matched the gold answer,
and the candidate's pooled estimated credits supported 2.035x same-budget usage,
or +103.51%, for that workload. These are estimates from Codex token telemetry,
not provider-reported per-turn debits. The normal installed-plugin loader was
not exercised, and the selected candidate repeat was 5.90x slower end to end.
This is not an every-prompt, quota, latency, or installed-product guarantee. See
the [controlled usage evidence](../../docs/benchmarks/codex-sol-ultra-checkout-hook-usage-evidence-2026-08-08.json).

The current bundle's `lians-sdk` 0.5.0 wheel was tested through an isolated,
direct-path install: 84 focused SDK tests passed and 2 optional-platform tests
skipped. Its pinned BGE artifact validated; an encrypted subject-bound write
was recalled; the plaintext marker was absent from SQLite; dynamic MCP
`--check` passed; and every hashed wheel RECORD entry plus the embedded license
was verified. A separate clean frozen plugin-runtime sync imported `greenlet`
successfully. The plugin package suite passed 75 tests with 7 optional-platform
skips. Hook inventory is present, but this host did not dispatch the installed
plugin hooks through either the app server or `codex exec`. One paid candidate
was rejected for that reason, so there is no accepted installed-plugin
economics result.

Separately, historical installed-cache evidence used an earlier `.15` SDK
bundle. Quiet SessionStart prewarm took 6.871 seconds. After prewarm, 20 fresh
hook processes reached 1.188 seconds p95 and 1.197 seconds max wall time, with
all 20 non-degraded. Those numbers were not rerun on the current wheel and do
not establish cold-start or overall model response-time improvement. See the
[historical latency evidence](../../docs/benchmarks/codex-installed-plugin-latency-evidence-2026-08-08.json)
and `VALIDATION.json` for the machine-readable boundary.

## Package contents

- `hooks/hooks.json`: quiet `SessionStart` prewarm plus score-gated
  `UserPromptSubmit` recall.
- `.mcp.json`: optional compact `remember` and `recall` tools launched through
  the unique `lians-memory-mcp` command. Codex supplies the active task path in
  authenticated MCP metadata; missing, invalid, or changing scope fails closed.
  The server is deliberately non-required until first-run setup succeeds.
- `skills/lians-memory`: safe recall/write behavior and setup guidance.
- `vendor/`: one hash-verified SDK wheel built from this checkout. The launcher
  never falls back to the older public PyPI package.
- `runtime/`: the tested hook/daemon and a frozen `uv` environment.
- `scripts/lians_plugin.py`: setup, doctor, MCP, hook, prewarm, and daemon entry
  points.

Mutable databases, virtual environments, models, daemon state, and receipts are
stored beneath a fixed OS-native, per-user Lians data directory, never in the
installed plugin snapshot. Version 0.1 rejects custom data-home overrides so a
shared or replaceable path cannot redirect the trusted runtime.

## Install from this checkout

Prerequisite: install [uv](https://docs.astral.sh/uv/). Then add the repository
marketplace and install the plugin:

```text
codex plugin marketplace add /absolute/path/to/Lians
codex plugin add lians-memory@lians
```

Run the setup command from the installed plugin or from this checkout. Local
mode uses a pinned BGE ONNX artifact and does not require a Lians API key:

```text
uv run --managed-python --no-project --python 3.11 python -I -B plugins/lians-memory/scripts/lians_plugin.py setup --mode local --download-bge
uv tool update-shell
```

Local setup also generates a unique 256-bit memory-encryption key in the
OS-native data directory. The key is never printed or bundled; setup applies
mode `0600` on POSIX and a user/SYSTEM/Administrators-only ACL on Windows.
Back it up separately from the database: losing it makes encrypted memories
unrecoverable, while copying only the database does not provide the key needed
to decrypt protected memory fields.

Managed mode is available only for a deployed HTTPS Lians service supplied by
the operator; this package does not claim a live public default endpoint. It
keeps the API key in the environment and never writes it. Setup does not need
the key, so do not expose it to the dependency-install process:

```text
uv run --managed-python --no-project --python 3.11 python -I -B plugins/lians-memory/scripts/lians_plugin.py setup --mode managed --managed-url https://your-lians.example
```

After setup, make `LIANS_API_KEY` available through the operating-system
secret manager or managed environment used to launch Codex. Doctor and the
runtime read it there. In managed mode, prompt-derived recall queries and
content explicitly sent through `remember` leave the machine for that HTTPS
service; use only an operator and data policy you trust.

Setup installs `lians-memory-mcp` into the directory printed by `uv tool dir
--bin`. Fully restart Codex and its launching shell after `uv tool update-shell`,
then run `doctor`. If doctor reports legacy Lians configuration, back up and
disable the old user-level MCP and hook definitions, restart, and rerun doctor;
the plugin never edits those files automatically. After doctor reports ready,
open `/hooks`, review and trust both plugin definitions, then start a new Codex
task. Installing or updating a plugin does not automatically trust its hooks.

For a second machine, follow [PARTNER_INSTALL.md](./PARTNER_INSTALL.md). A tagged
repository marketplace is the intended team distribution path; the bundled
stdio MCP is not yet a public universal-directory submission.

## Isolation and migration

On the tested local Codex Desktop/CLI host, Codex adds the active task path to
each MCP call as authenticated sandbox metadata. The server binds once to that
resolved path and stores each project's local database under its own hashed
directory; missing, malformed, or later-changing scope is rejected. Prompt
hooks inherit the active task directory and derive the same identifier. Remote
or executor-owned plugin hosts are not yet project-scope qualified. The
installer does not copy Codex config, secrets, existing databases, local
encryption keys, or embedding models to another machine.

If this machine already uses the older user-level Lians MCP and hooks, doctor
reports a blocking migration state until those definitions are backed up and
disabled. This prevents shadowed tools and duplicate prewarm/recall from being
mistaken for a qualified plugin run. Do not silently import an existing
database: its embedding provider may not match the plugin's pinned BGE index.

## Latency boundary

Historical evidence from the installed `.15` bundle completed 20 sequential,
process-fresh prompt hooks against a 419-memory BGE store with 1.188 seconds p95
and 1.197 seconds max wall time after prewarm. The one-time fresh-identity
SessionStart prewarm took 6.871 seconds and did not beat 3.5 seconds. These
measurements were not rerun for the current 0.5.0 wheel. Keep SessionStart
prewarm enabled and do not present the warm numbers as cold-start or end-to-end
response latency.

The local daemon is single-threaded, the OS/model page cache was not flushed,
and concurrent tasks are not yet qualified. If SessionStart was skipped or the
daemon expired, the prompt hook fails open without memory instead of spawning
an unverified daemon; start or resume a task to prewarm it again.
