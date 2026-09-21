# Lians proof-gate beta protocol

This beta tests one narrow claim:

> For real Codex coding tasks, Protect can preserve verifier-backed completion
> while using fewer observed premium-model calls than Maximum.

It does **not** test whether Lians increases a provider quota, lowers a bill, or
works for every repository. The provider remains the authority for quota and
billing. Lians counts only calls and usage reported in its own local receipts.

## Entry requirements

A tester needs Windows x64, an installed and signed-in Codex CLI, a Git
repository they are allowed to modify, and a proof command that can return a
deterministic exit code. Do not use a production, regulated, confidential, or
high-value repository in this unsigned engineering beta.

The target cohort is five external testers. Each tester contributes four
matched task pairs, for 20 matched tasks and 40 total runs.

## Install the engineering beta

1. Open the latest `mission-control-v*` prerelease on the
   [Lians releases page](https://github.com/Lians-ai/Lians/releases).
2. Download `Lians-<version>-Windows-x64.zip` and its `.sha256` file. Confirm
   the archive's SHA-256 before extracting it.
3. Read `README-WINDOWS.txt`, `CODEX-BRIDGE.md`, and this protocol in the ZIP.
4. Extract the ZIP, then open `Lians.exe`. The build is unsigned; stop if an
   organizational policy does not allow unsigned engineering software.
5. Open **System** and confirm Codex is available before selecting a test
   repository. Planning, import, and export work without Codex; execution does
   not.

The release workflow builds the executable from the public source, records the
exact source commit and executable hash in `BUILD-PROVENANCE.json`, attaches a
GitHub build-provenance attestation, and smoke-tests the frozen application.
Those controls establish traceability, not independent security review.

## Run one matched pair

1. Choose a real task before seeing either outcome.
2. Prepare two clean worktrees or clones at the same Git commit. Both must show
   the same clean or dirty state.
3. Use the exact same task, constraints, definition of done, and proof command
   in both worktrees.
4. Randomize which mode runs first. Run **Protect** once in one worktree and
   **Maximum** once in the other.
5. Do not repair one result manually before the other run. Record any necessary
   intervention in the feedback issue.
6. Confirm that each run ends in PASS, BLOCK, ERROR, or INTERRUPTED. A model's
   written claim is not completion; only the configured proof can produce PASS.
7. Open **System → Beta proof report → Export** after the pair.

The export uses a private local key to assign the same opaque pair fingerprint
to runs that began with the same task, proof, and Git state. The key never
leaves the computer.

## What the report includes

- a random beta participant ID;
- an opaque matched-task fingerprint;
- Lians version and mode;
- execution bridge (`codex_app_server` or `codex_exec`);
- starting Git commit and a digest of the starting dirty state;
- PASS or BLOCK status;
- observed model calls, premium calls, token counters, and duration;
- receipt and verifier-command hashes.

It excludes task text, constraints, definition-of-done text, repository path,
prompts, model output, verifier output, credentials, Codex thread IDs, local
job IDs, and provider billing data.
Read the JSON before sharing it. If it contains anything you do not want to
publish, do not attach it.

## Preregistered product gate

Lians may move from “public engineering beta” to a competitive product claim
only after all of these are observed:

- five external testers complete installation;
- 20 matched tasks are completed under the protocol;
- at least 16 pairs pass in both modes;
- Protect's verified completion count is not lower than Maximum's;
- Protect uses at least 30% fewer observed premium calls across matched pairs;
- at least three testers return on a later day and complete another task; and
- at least one tester says they would pay $12 per month after seeing their own
  receipt.

The team must report failures and excluded runs alongside passes. A small beta
cannot establish universal model quality or provider savings.

A Protect/Maximum pair counts only when both runs used the same known execution
bridge. A mixed app-server/exec pair remains visible as incomparable and does
not enter the matched aggregate.

Both runs must also contain per-stage usage telemetry. Missing telemetry is
reported as an incomplete-usage pair and cannot support the premium-reduction
claim.

## Immediate stop conditions

Stop the beta and preserve the local receipt if Lians:

- edits a repository before Start is chosen;
- exceeds the selected premium-call ceiling;
- reports PASS when the configured proof failed or did not run;
- exposes a task, path, prompt, credential, or raw output in the beta report;
- loses or silently rewrites an existing mission or receipt; or
- cannot distinguish an interrupted run from a verified one.

## Submit feedback

Open a
[Mission Control beta result](https://github.com/Lians-ai/Lians/issues/new?template=mission_control_beta.yml)
issue. Attach the exported report only after reviewing it. Use synthetic text
for screenshots or examples and never post private repository content.
