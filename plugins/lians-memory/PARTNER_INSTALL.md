# Install Lians Memory on another Codex machine

This installs the same reviewed plugin code on a second machine without moving
the first user's Codex configuration, memories, credentials, virtual
environment, or embedding model. Share a tagged repository checkout, not a copy
of anyone's home directory or plugin-data directory.

OpenAI documents plugins as installable packages shared by ChatGPT and Codex;
local marketplaces are the supported development and team-testing path. See
[Build plugins](https://learn.chatgpt.com/docs/build-plugins) and
[Codex hooks](https://learn.chatgpt.com/docs/hooks).

## 1. Prerequisites

- Codex with plugin support.
- [uv](https://docs.astral.sh/uv/) on `PATH`.
- Python 3.11, which `uv` can install on demand.
- For local mode, enough disk and network capacity for the pinned 1.34 GB BGE
  ONNX graph plus the frozen Python environment.

Use either a checkout of the same tagged Lians revision or the reviewed partner
ZIP produced from that revision. Do not send a preinstalled plugin cache: Codex
should install from source on the partner's machine.

For a repository checkout, run these commands from any shell after replacing
the example path with the checkout's absolute path:

```text
codex plugin marketplace add C:\path\to\Lians
codex plugin add lians-memory@lians
```

For the partner ZIP, verify the supplied SHA-256 first, extract it to a private
directory, and point Codex at the extracted root (the directory containing
`.agents` and `plugins`):

```text
codex plugin marketplace add C:\path\to\lians-memory-codex-plugin
codex plugin add lians-memory@lians
```

On macOS or Linux, use the corresponding absolute POSIX path. Keep the extracted
source directory in place while this local marketplace is installed; remove the
marketplace in Codex before deleting it.

## 2. Choose one backend

### Local SQLite: no API key

Local mode creates a fresh database for each project. It downloads the pinned
BGE artifact from its revision-specific URLs and verifies both file hashes
before the SDK exporter accepts them. It also creates a random 256-bit local
encryption key, restricts the key file to the operating-system account, and
never prints or distributes it. Back up that key separately from the database;
it is required to recover encrypted memories.

Windows PowerShell:

```powershell
powershell -ExecutionPolicy Bypass -File .\plugins\lians-memory\scripts\setup.ps1 --mode local --download-bge
uv tool update-shell
```

macOS or Linux:

```sh
sh plugins/lians-memory/scripts/setup.sh --mode local --download-bge
uv tool update-shell
```

For an offline or centrally downloaded model, supply a directory containing
the exact pinned `model.onnx` and `tokenizer.json` instead:

```text
... setup --mode local --bge-source /absolute/path/to/bge-files
```

Setup refuses a hash mismatch. It never downloads or substitutes a different
model revision.

### Managed Lians: environment-only API key

Managed mode stores only the non-secret service URL in its profile. Setup does
not require the API key. The plugin reads `LIANS_API_KEY` from the Codex
process environment at doctor/runtime use time and never writes or prints the
value.

PowerShell setup:

```powershell
powershell -ExecutionPolicy Bypass -File .\plugins\lians-memory\scripts\setup.ps1 --mode managed --managed-url https://your-lians.example
uv tool update-shell
```

POSIX setup:

```sh
sh plugins/lians-memory/scripts/setup.sh --mode managed --managed-url https://your-lians.example
uv tool update-shell
```

Managed mode requires an operator-supplied deployed HTTPS endpoint. This
package does not claim that `api.lians.dev` or another public default endpoint
is currently live.

After setup, use the partner's operating-system secret manager or managed
launch environment to provide `LIANS_API_KEY` to doctor and Codex. Do not
paste the key into the setup command, shell history, `.mcp.json`, `hooks.json`,
`config.toml`, the repository, or a support message.

Managed mode sends prompt-derived recall queries and content explicitly passed
to `remember` to the configured HTTPS service. Use only an operator and data
handling policy the partner trusts. Local mode keeps those operations on the
machine.

## 3. Restart, diagnose, and activate hooks

Setup copies the verified `lians-memory-mcp` console launcher from the frozen
environment into the directory printed by `uv tool dir --bin`; it does not
perform a second package resolution. Fully restart Codex and the shell that
launches it after `uv tool update-shell`, then run the platform-specific
doctor command:

```powershell
powershell -ExecutionPolicy Bypass -File .\plugins\lians-memory\scripts\doctor.ps1
```

```sh
sh plugins/lians-memory/scripts/doctor.sh
```

Doctor intentionally reports `not ready` while the launcher is absent from
`PATH`.

If doctor reports `migration required`, back up and disable the older
user-level Lians MCP and hook definitions, fully restart Codex, and rerun
doctor. The plugin does not rewrite those files automatically.

After doctor reports `ready`:

1. Open `/hooks` in Codex.
2. Review and trust the Lians `SessionStart` and `UserPromptSubmit` definitions.
3. Start a new task so Codex reloads the installed plugin.

Codex intentionally does not trust newly installed or changed plugin hooks
automatically. Before setup succeeds, the optional MCP exits quickly and the
hooks fail open without adding prompt context.

## 4. Smoke test without sharing personal data

In a disposable test project, ask Codex to remember a harmless project fact,
for example: `Remember that this test project's release color is amber.` Start
a new task in the same project and ask for the release color. Then run doctor
again.

On the tested local Codex Desktop/CLI host, prompt hooks inherit the resolved
task path and Codex supplies that same path to MCP calls as authenticated
sandbox metadata. An unrelated project therefore gets a different agent,
namespace, subject, and SQLite file. Missing or changing MCP scope fails closed.
The plugin clears ambient `LIANS_AGENT_ID` and `LIANS_NAMESPACE` values to
preserve that isolation in both local and managed mode. Remote or executor-owned
plugin hosts are not yet project-scope qualified.

Do not validate the installation by copying another user's database. Existing
stores may use a different embedding model even when their vector dimensions
match.

## 5. What is and is not distributed

The plugin repository contains:

- source code and hook definitions;
- one provenance-recorded, SHA-256-verified SDK wheel;
- a frozen `uv.lock` for exact dependency resolution.

It does not contain:

- `~/.codex/config.toml` or `~/.codex/hooks.json`;
- any SQLite or Postgres data;
- any Lians API key;
- any generated local memory-encryption key;
- ONNX, safetensors, PyTorch, or other embedding-model binaries;
- daemon state, receipts, caches, or a virtual environment.

The launcher writes mutable state beneath one OS-native Lians data directory:
`%LOCALAPPDATA%\Lians\CodexMemory` on Windows,
`~/Library/Application Support/Lians/CodexMemory` on macOS, or the XDG data
directory on Linux. Version 0.1 intentionally rejects `LIANS_MEMORY_HOME` and
one-shot data-directory overrides: the frozen interpreter, profile, keys, and
memory state stay under the same private native root. The plugin does not
depend on an ephemeral plugin-cache path.

## 6. Updating

After checking out a newer tagged plugin build, reinstall it from the same local
marketplace, rerun `setup` in the chosen mode, and run `doctor`. Setup verifies
the new wheel provenance and performs `uv sync --frozen`; it does not select a
public PyPI version. If the hook definition changed, review it again with
`/hooks`, then start a new task.

## Measurement boundary

The measured Sol memory workload showed a 2.22x pooled same-budget result, but
the every-prompt gate failed. Treat usage extension as workload-dependent, not
as a guaranteed Codex quota increase. Short or self-contained prompts may not
benefit.
