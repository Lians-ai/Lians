# Lians v0.11.0 release readiness

Status: **engineering beta candidate; external proof gate pending review,
merge, and the tagged prerelease**.

## What is ready

- The Codex-only local product path can plan, execute, verify, escalate, persist,
  recover after interruption, and verify its receipts offline.
- A supported Codex installation now runs all stages of one mission in one
  app-server thread. Model, effort, working directory, approval policy, and
  sandbox remain explicit on every turn.
- Older Codex versions retain the tested `codex exec` compatibility adapter,
  and either bridge can be selected explicitly.
- Protect, Balanced, and Maximum routes remain deterministic and enforce hard
  premium-call ceilings at runtime.
- Mission contracts are time-aware, hash-linked, credential-screened, and
  checked for ledger tampering before revision.
- The beta export links matched runs with a keyed opaque fingerprint and omits
  task text, constraints, paths, prompts, model and verifier output, credentials,
  local job IDs, Codex thread IDs, and billing data.
- The Windows build now contains source provenance, the proof-gate protocol,
  and the Codex-bridge boundary. The hosted workflow will attest its artifacts.

## Current engineering evidence

- 83 automated tests pass, including 13 hosted-connector boundary tests.
- Branch-aware coverage is 73% overall and 70% for the new app-server bridge.
  CLI tests execute child processes and therefore are not credited by this
  coverage run even though those behaviors pass.
- Stress coverage still includes 3,000 deterministic route decisions, 540
  simulated runtime paths, 250 concurrent revisions, 200 tampered receipts,
  120 concurrent HTTP inspections, and 40 simultaneous run starts.
- The installed Codex app-server completed a real local initialize handshake.
  Turn parsing, per-turn usage, context non-replay, sandbox selection, authority
  denial, fallback selection, timeout, error, and shutdown paths are unit tested.
- A real two-turn Protect mission ran through the persistent Codex app-server
  against a disposable repository on 2026-09-21. Discovery used Luna, implementation
  used Terra, no premium call was attempted, the independent verifier passed, and
  the offline receipt verified as
  `467e6f0ebcae2e2fa01981f0a2c9dea1ae49b230ed53fd965f0fa8c99952f8bd`.
- Ruff lint and formatting, Python compilation, browser JavaScript syntax, and
  product-manifest JSON validation pass.
- The 0.11.0 wheel and source archive build. The wheel installs outside the
  checkout, imports the expected version and assets, and serves valid health,
  readiness, and security-header responses.
- PyInstaller 6.22.0 builds the self-contained Windows executable. The exact
  frozen binary passes live health, security-header, wordmark-integrity,
  portable-agent round-trip, privacy-bounded beta-export, and zero-run-mutation
  checks.
- The local ZIP has the six expected files and its executable hash matches
  `BUILD-PROVENANCE.json`. Because it was built before this branch was committed,
  it truthfully reports `source_dirty: true` and is not a releasable artifact.
- The pull-request workflow reproduced the source, wheel, and Windows archive
  from a clean checkout. Every repository check passed. The downloaded Windows
  artifact had a matching checksum, reported `source_dirty: false`, named the
  workflow commit, and passed GitHub attestation verification.

## What remains before testers receive it

1. Complete human review and merge the source change.
2. Create the matching `mission-control-v0.11.0` tag so the workflow publishes
   the attested prerelease and checksums.
3. Download that exact prerelease once and repeat the first-mission path on a
   clean Windows user account if one is available.

## What this does not prove

- It does not prove provider quota is extended. Lians reduces repeated work;
  the provider owns quota and billing.
- One small internal live mission does not prove effectiveness across real customer
  repositories. The first external matched runs provide that test.
- It does not prove equal quality across repositories or teams.
- It does not make Claude Code, Cursor Agent, or Gemini CLI routable.
- It has no independent security audit or code signature.
- App-server is experimental, so the persistent bridge remains beta.

## Product gate before a competitive claim

Follow [BETA-PROOF-GATE.md](BETA-PROOF-GATE.md): five external testers, twenty
matched tasks, at least sixteen pairs passing in both modes, no loss in Protect
completion count, at least 30% fewer observed premium calls, three later-day
returns, and one credible $12/month willingness-to-pay result.

Until those gates pass, the correct claim is **public engineering beta**, not
“market-proven,” “production-ready for everyone,” or “extends Codex usage.”
