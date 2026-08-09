# Lians Memory for Codex

Lians Memory is a distributable Codex plugin that recalls a bounded slice of
relevant memory before the first model request and exposes compact `remember`
and `recall` tools. Automatically injected hook context is marked as untrusted
data, score-gated, likely-secret redacted, and bounded before Codex sees it.
Explicit `recall` output is token-bounded and marked as untrusted data, but it
can return stored content; Codex must treat that content as evidence, never as
instructions.

## What we can honestly claim

On the measured 120-turn `gpt-5.6-sol` memory matrix, the candidate used
`0.450237577` of baseline estimated credits in aggregate: 2.22x same-budget
usage, or +122.10%. That result spans low, medium, high, xhigh, max, and ultra
reasoning efforts. It is not an every-prompt guarantee: only 21 of 60 paired
cells passed both the exact-answer and +80% economic gates, and the worst cell
used 4.06x the baseline. Short, self-contained, and no-memory prompts may not
benefit.

This v0.1 plugin packages the same bounded pre-model retrieval mechanism. A
plugin-installed end-to-end A/B remains a separate release gate before claiming
the packaged product itself reproduces the matrix result on a new machine.

The final packaged local path was functionally smoke-tested on Windows on
2026-08-08. Every doctor check except the intentionally blocking legacy-config
migration check passed. MCP advertised Codex's authenticated scope capability,
exposed exactly `remember` and `recall`, recalled an encrypted write in its
source project, and returned no hit in a second project; the marker was absent
from both raw SQLite files. Quiet SessionStart prewarm took 6.750 seconds, then
a fresh hook process injected the correct untrusted memory through the warm
daemon in 2.216 seconds without degraded retrieval. See `VALIDATION.json` for
the machine-readable boundary. This verifies packaging, isolation,
protected-content use, and hook transport—not a new model-cost A/B or a
universal latency result.

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

The exact BGE ONNX retrieval engine completed ten fresh dependency-light runs in
2.815 seconds p95 and 2.852 seconds max. Full production daemon cold prewarm was
6.574-12.152 seconds in recorded runs; after prewarm, prompt-time hook retrieval
was below 3.5 seconds. Keep the blocking `SessionStart` prewarm enabled and do
not present warm numbers as cold-start latency.

The local daemon is single-threaded and the recorded latency tests were
sequential. Concurrent-task latency is not yet qualified.
