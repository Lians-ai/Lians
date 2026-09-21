# Verification record — 2026-09-20

## v0.10.1 release-candidate checks

- Automated routing, runtime, CLI, policy, receipt, verifier security,
  application, governance, portable-agent, HTTP, recovery, concurrency, and
  packaging tests: **59/59 passed**.
- The unchanged v0.6 execution core previously passed five consecutive
  full-suite runs: **260/260 test executions** with no intermittent failure.
  v0.10.1 keeps the working-surface hierarchy and 12px interface-type guards,
  preserves byte-integrity checks for the canonical README wordmark, and adds
  strict portable-agent round-trip and tamper tests.
- Branch-aware coverage: **84% overall**.
- Stress cases passed:
  - 3,000 deterministic route decisions;
  - 540 simulated runtime paths under hard premium ceilings;
  - 250 concurrent revisions in one verified mission hash chain;
  - 200 receipt-tampering mutations;
  - 120 concurrent authorized HTTP inspections; and
  - 40 simultaneous run starts, with exactly one admitted and 39 rejected.
- A stress test reproduced a Windows atomic-replacement sharing collision. The
  job fell to `ERROR`; bounded Windows replacement retries were added, and the
  test plus all five endurance suites then passed.
- Queued jobs are now flushed before their worker starts. Startup recovery turns
  orphaned `QUEUED` or `RUNNING` records into truthful `INTERRUPTED` records.
- Mission-ledger hash tampering blocks the next append. Credential-like text in
  the goal, constraints, or definition of done is rejected before persistence.
- Host spoofing, cross-origin writes, missing tokens, duplicate JSON fields,
  non-finite JSON values, shell-control characters, and unsupported verifier
  executables were rejected in tests.
- Portable-agent import rejects checksum changes, duplicate or unknown fields,
  non-finite values, unsupported proof runners, and transferred custom
  executable approvals. The full local workspace path is never exported, and
  every import requires an explicit destination choice.
- Ruff lint and formatting: **passed**.
- Python bytecode compilation: **passed**.
- Browser JavaScript syntax check: **passed**.
- Desktop interaction QA confirmed that the context tool expands and collapses,
  the navigation rail collapses, and Preview reveals the route and evidence
  rail only after a mission revision exists. The live tab was reset afterward.
- A 540 x 900 responsive pass found and fixed a stretched navigation grid row;
  the retest keeps the header, composer, all three modes, Preview, and Start in
  view without horizontal overflow.
- The 540px route surface was retested with the larger type: all four stages fit
  without element or page overflow.
- The canonical README wordmark is used intact in navigation; the composer and
  evidence surfaces crop its original lotus directly instead of redrawing it.
- Distribution QA removed the founder-specific profile row, replaced the
  hard-coded Ready claim with live connector availability, and keeps Start
  disabled until a routable Codex connector is detected.
- Browser interaction QA exported a real 835-byte `.lians-agent`, displayed the
  no-execution boundary, and left the mission unrun. The first narrow pass found
  and fixed the System popover stacking behind the composer.
- A Chromium DevTools end-to-end test exported through the live UI and fed that
  exact file into the hidden Import input. The mission returned, the workspace
  remained blank, no full path was exported, runs stayed 0 -> 0, and mission
  records stayed 0 -> 0.
- The final v0.10.1 surface was reloaded in the in-app browser: the connector
  settled from Checking to Available, Start became enabled only afterward, the
  founder name was absent, and the page had no horizontal overflow.

## Distribution checks

- Clean isolated build produced the v0.10.1 wheel and source archive.
- The wheel installed without dependencies into a fresh isolated directory.
- The source archive rebuilt and installed into a second fresh isolated
  directory.
- Both installed artifacts imported v0.10.1 from the isolated target, contained
  all four web assets including the byte-identical packaged README wordmark,
  started an ephemeral loopback server, returned the expected health and
  readiness payloads, and supplied security headers.
- PyInstaller 6.22.0 produced a self-contained Windows executable and a ZIP
  containing only `Lians.exe`, `README-WINDOWS.txt`, and `SHA256SUMS.txt`.
- The included Windows guide was inspected from the final ZIP and contains the
  first-mission path, Codex prerequisite, local-record location, portable-agent
  privacy boundary, and unsigned-build warning.
- The exact built executable was started hidden on port 4320 and passed live
  health, security-header, wordmark-integrity, agent export/import, and
  zero-runs-before/after checks. Its process path was verified before shutdown.

## Artifact hashes

```text
107C1FE2F352243A9B7DFA6C7A4A93A0888429428396A5D6D68CDA50371DB979  Lians-0.10.1-Windows-x64.zip
7CB30A444CBAFDE3E4D7F361F090BF4AFCE7E93EEAD780A3C3DA38B0455D42AD  Lians.exe
B9C1687F9885EBAFD06E0D8E625C0DBD1CEA0AC97D98F87E37FBD86DB1C0D656  lians_finish-0.10.1-py3-none-any.whl
A9B91A1EAEAF8F46506F1CE50F3A7B9E1738AC77ECE56C173DCE8F63F90867E1  lians_finish-0.10.1.tar.gz
```

## Six-task live matched pilot

All six matched tasks passed in Protect and Maximum. Protect used 2 observed
premium calls versus Maximum's 14, 19.2% fewer derived uncached input tokens,
and 37.0% less wall time. All 12 countable receipt hashes were independently
recomputed. The aggregate report and invalidation disclosures are in
`../../experiments/lians-finish-1/PILOT-RESULTS.md`.

This is a small internal pilot on Lians's own codebase, not an external or
provider-quota claim.

## Remaining product gate

- Reproduce the matched comparison across the preregistered 20 external tasks.
- Have at least three of five external users complete a second task.
- Have at least one user pay after seeing the matched usage receipt.
- Do not describe Claude Code, Cursor Agent, or Gemini CLI as governed until an
  official connector passes the same acceptance suite.
- Obtain independent security review before handling high-value production
  repositories as a generally available product.

Until those gates pass, v0.10.1 is an **engineering release candidate**, not a
market-proven or generally production-ready release.
