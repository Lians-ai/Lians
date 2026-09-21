# Lians Mission Control

> Give Lians the outcome once. It keeps the mission intact, chooses the least
> expensive capable agent for each stage, protects premium usage, and refuses
> to claim completion without evidence.

This is the first product slice of the Lians rebuild: a local-first governance
layer for the AI tools a person or team already uses. It is not a generic
memory database or another chat interface. The complete product boundary and
build order live in [FULL-REPLACEMENT.md](FULL-REPLACEMENT.md).

## Open the application

From an installed wheel:

```powershell
lians-finish-app
```

From this source checkout:

```powershell
$env:PYTHONPATH = "$PWD\src"
python -m lians_finish.app
```

The application opens at `http://127.0.0.1:4318/`. It runs only on the local
computer and provides:

- a repository picker and one outcome box;
- Protect, Balanced, and Maximum mode cards;
- a no-model route preview;
- background execution with an explicit verification command;
- live PASS, BLOCK, ERROR, or recovered INTERRUPTED status;
- local run history with hash-bound receipts; and
- read-only detection of supported agent CLIs, with honest connector status;
- append-only, hash-linked mission revisions with time and expiry; and
- automatic proof suggestions plus observed local usage totals; and
- checksummed `.lians-agent` export and inert import of mission, policy, and proof;
- one persistent Codex thread per mission when the installed CLI supports its
  app-server protocol, with the one-shot `codex exec` adapter retained for
  older installations; and
- a privacy-bounded beta report for the preregistered Protect-versus-Maximum
  proof gate.

The local API requires a per-launch token embedded in the page, validates the
loopback host and request origin, rejects ambiguous JSON, and sends restrictive
browser security headers. Only one live run is allowed at a time, and the
verification command is validated before the background job starts. Run and
artifact records are atomically published; an active record left by a stopped
process becomes `INTERRUPTED` on restart rather than remaining falsely active.
Run data defaults to `%LOCALAPPDATA%\Lians Finish` on Windows. Use
`lians-finish-app --help` for a different port, repository, or data directory.

## Move an agent between workspaces

Open **System**, then choose **Export**. Lians downloads a small
`<workspace>-<mode>.lians-agent` JSON file containing the current mission,
premium policy, route provenance, and proof command. It contains no provider
credentials and exporting neither records a mission revision nor runs a model.

Choose **Import** to verify the file checksum and reload those fields. Import
never starts the agent or runs the proof command. The full local workspace path
is never exported; Lians leaves the workspace blank so the recipient must
choose the destination. Custom executable approvals are intentionally not
portable.

## Product loop

```text
task -> classify risk -> choose models -> implement -> verify -> escalate only if needed
```

Execution is Codex-only today so the core claim can be measured before
cross-provider complexity is added. Lians may detect Claude Code, Cursor Agent,
or Gemini CLI locally, but it labels them as detected rather than routable
until an official, tested connector exists.

On a current Codex installation, Mission Control uses the official app-server
integration surface to keep discovery, implementation, verification repair,
and review inside one Codex thread. Each turn can still change model, reasoning
effort, and sandbox. This reduces repeated context reconstruction; it does not
increase an account's provider quota. Codex currently labels app-server
experimental, so Lians feature-detects it and keeps the existing `codex exec`
path as a compatibility option. Use `--codex-bridge exec` or set
`LIANS_CODEX_BRIDGE=exec` to force that path.

The open-source Lians MCP/plugin remains the in-Codex state and proof layer.
Mission Control is the local routing and receipt layer. See
[CODEX-BRIDGE.md](CODEX-BRIDGE.md) for the exact boundary.

## Plan a task without consuming quota

```powershell
$env:PYTHONPATH = "$PWD\src"
python -m lians_finish.cli run "Add password reset" `
  --mode protect `
  --repo . `
  --out output/password-reset
```

Dry-run planning is the default. It invokes no model and writes:

- `route.json` — models, effort levels, triggers, reasons, and premium budget;
- `receipt.json` — a hash-bound record with an explicit claim boundary;
- `receipt.md` — a readable task and route summary.

Use `--json` to print the final receipt JSON to standard output instead of the
human summary. The normal `route.json`, `receipt.json`, and `receipt.md` files
are still written.

## Verify a receipt offline

```powershell
lians-finish verify-receipt output/password-reset/receipt.json
```

The command reads without writing or invoking models. It prints
`VALID <hash>` with exit code `0`, `TAMPERED` with exit code `2`, or an
`ERROR:` message with exit code `1`. A matching checksum proves internal
consistency, not authenticity or correctness.

## Execute deliberately

Live execution requires both `--execute` and a verification command:

```powershell
python -m lians_finish.cli run "Add pagination to the project list" `
  --mode protect `
  --repo C:\path\to\repo `
  --verify "python -m unittest" `
  --execute
```

Lians uses the locally authenticated Codex CLI in a read-only sandbox for
discovery and a workspace-write sandbox for implementation. It will not use a
premium model merely because one is available. Normal work escalates to Astra
only after verification fails. High-risk work, including authentication,
payments, security, deployment, and production-data changes, receives a
premium review even if the first verification passes.

Verifier commands are validated before any model call. Bare executables are
limited to `python`, `python3`, `pytest`, and `ruff`. Other executables require
an absolute path exactly matched by a repeatable `--allow-verifier PATH`.
Arguments containing shell-control characters or line breaks are rejected;
the verifier is launched without a shell.

Every executed PASS or BLOCK receipt includes `verifier_command_sha256`, the
SHA-256 of the canonical UTF-8 JSON argv array. The field is covered by the
overall receipt hash.

## Modes

List the available modes and their premium-call budgets:

```powershell
lians-finish modes
```

From a source checkout, set `$env:PYTHONPATH = "$PWD\src"` and run
`python -m lians_finish.cli modes`.

The command prints one deterministic JSON object with modes sorted by name.
Each item contains only `name` and the integer `premium_budget`:

```json
{"modes":[{"name":"balanced","premium_budget":2},{"name":"maximum","premium_budget":4},{"name":"protect","premium_budget":1}]}
```

It exits with code `0`, writes no artifacts, and invokes no routing, models,
or verification. It accepts no extra arguments or options. Use
`lians-finish --help` for command help.

| Mode | Default implementation | Premium behavior |
|---|---|---|
| `protect` | Luna for simple work; Terra otherwise | At most one Astra call |
| `balanced` | Sol | Astra for failure or high-risk review |
| `maximum` | Astra for discovery and implementation | Prioritize capability; matched baseline mode |

Models and policies are deliberately explicit in v0.11.0. A learned router should
replace these rules only after real task outcomes exist.

Use `run --premium-budget N` to lower the premium-call budget. `N` must be an
integer from zero through the selected mode's built-in limit: `protect=1`,
`balanced=2`, or `maximum=4`. Omitting the flag keeps that mode's default. The
effective budget appears in the route, receipts, and human summary.

Failed premium attempts count toward the budget. If a required stage cannot
run within the budget, execution returns a `BLOCK` receipt instead of skipping
the stage.

Use `run --policy-file FILE` to override only Protect's implementation model
and effort with strict JSON:

```json
{"protect":{"implementation_model":"gpt-5.6-sol","implementation_effort":"high"}}
```

Allowed models are Luna, Terra, and Sol; allowed efforts are `low`, `medium`,
and `high`. Unknown fields, Astra, malformed JSON, and missing files are
rejected before artifacts or model calls. Omitting the flag preserves the
default route and router version.

## Test

```powershell
$env:PYTHONPATH = "$PWD\src"
python -m unittest discover -s tests -v
```

## Join the proof-gate beta

Follow [BETA-PROOF-GATE.md](BETA-PROOF-GATE.md). Run the same bounded task from
the same starting Git state once in Protect and once in Maximum, then open
**System -> Beta proof report -> Export**. The export contains an opaque pair
fingerprint and measurements, not task text, prompts, repository paths, model
output, verifier output, credentials, or billing data. Read the JSON before
attaching it to a public issue.

## Claim boundary

Lians does not increase a provider's official quota. The current build proves
locally that it can plan and enforce different models by stage, preserve one
Codex mission thread when app-server is available, require verification, cap
premium calls, and export privacy-bounded matched-test evidence. A six-task
internal matched pilot found fewer observed premium calls and derived tokens in
Protect than Maximum while all tasks passed, but that is not a provider-quota
or external-market claim. External quality, repeat use, and willingness to pay
remain unproven.
