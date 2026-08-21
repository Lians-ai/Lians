# Lians Check and Guard product contract

This document translates the product demand pressure test into a repository
contract. Market, pricing, and conversion numbers are hypotheses to validate,
not established results.

## Category

**Lians is the evidence-backed proof layer for AI work. Its first supported
check is code.**

The consumer surface is deliberately smaller than the underlying Guard trust
contract. The universal action is Check. In the current code lane, a user
authorizes project checks once, runs `lians check`, and sees `NO PROOF`, `NEEDS
WORK`, or `READY TO REVIEW`. Guard remains the internal and team policy layer
for typed evidence, freshness, CI intake, and shared control.

Lians helps a developer or team answer four questions after an agent session:

1. What task is current?
2. What changed since the last trustworthy checkpoint?
3. Which claims have evidence and which are only agent-reported?
4. What still blocks human review?

## User-visible states

| State | Meaning | Required user action |
|---|---|---|
| `RECOVERED` | The current task and its bounded context were restored | Continue from the shown next action |
| `STALE` | The saved task or its evidence no longer matches current state | Refresh or re-run the affected work |
| `BLOCKED` | A criterion, constraint, or dependency prevents review | Resolve the named blocker |
| `READY FOR HUMAN REVIEW` | The configured evidence gate passes | Review the work; do not treat this as approval |

These states should be large, direct, and understandable without opening an
audit graph or reading a transcript.

## Checkpoint binding

A trustworthy checkpoint should record:

- project identity and normalized repository root;
- base and current commit identifiers when available;
- clean or dirty working-tree state;
- a digest of changed paths and their status;
- task contract and decision versions;
- typed criterion and constraint evidence;
- blockers, next action, source client, and event time; and
- lineage to the state it supersedes.

A mismatch does not silently update the old checkpoint. It creates a stale
signal or requires new evidence.

## Trust rules

- Only `measured_local`, `measured_ci`, and `human_confirmed` evidence can open
  the review gate.
- `agent_attested` evidence remains useful recovery context but cannot satisfy a
  completion criterion.
- `inferred_activity`, including touched files, cannot satisfy a completion
  criterion.
- Agent-facing MCP and Bridge callers cannot self-assign `measured_local`,
  `measured_ci`, or `human_confirmed`. Lians stores those declarations as
  `agent_attested` until an authorized evidence path verifies them.
- Failed checks are displayed and block readiness.
- Unknown constraints keep the gate closed.
- Human review is always required after the gate opens.

Trusted evidence currently enters through one of three bounded paths:

- a Lians-owned local verifier;
- an exact GitHub Actions artifact whose attestation, repository, signer
  workflow, ref, commit, hosted-runner status, and selected checks all verify; or
- an interactive human confirmation that requires the exact criterion phrase.

Protect the trusted signer workflow and release branch in GitHub. An attestation
proves which workflow produced an artifact; it does not make a mutable or weakly
protected workflow trustworthy.

## Inspect and import evidence

Inspect the local project without sending task content to a hosted service:

```bash
lians report --json
```

Confirm one criterion interactively:

```bash
lians confirm TASK_ID CRITERION_ID --evidence "Reviewed in the running app"
```

Import a downloaded, attested GitHub Actions evidence artifact:

```bash
lians ci-evidence import lians-guard-evidence.json \
  --repo OWNER/REPO \
  --signer-workflow OWNER/REPO/.github/workflows/lians-guard.yml \
  --source-ref refs/heads/main \
  --task TASK_ID \
  --criterion CRITERION_ID \
  --check guard-tests
```

The importer fails closed for an unattested artifact, a self-hosted runner, the
wrong signer workflow, ref, repository, or commit, a missing selected check, or
a current Git HEAD that differs from the attested commit. It also displays the
selected checks and criteria, then requires an exact interactive authorization
before it records that mapping.

## Initial product boundary

The initial supported workflow is Claude Code and Codex connected to local Git
repositories, with GitHub Actions as the first CI evidence source. Other agents,
providers, and advanced Lians capabilities are secondary until this workflow is
reliable.

Free individual use centers on local recovery. Paid value centers on shared
current state, policy-backed completion gates, team visibility, support, and
managed operations.

## Commercial hypotheses after retention

The individual Check loop stays free while Lians proves activation, repeat
checks, referrals, and four-week retention. Team pricing should be tested only
when retained users ask for shared policy, CI enforcement, audit history,
administration, or managed support.

A future team pilot should establish a baseline and report changes in:

- repeated explanation events;
- interrupted sessions successfully recovered;
- stale task or requirement incidents detected;
- unsupported completion claims blocked;
- review rework and time to review; and
- weekly active repositories and developers.

Packaging and pricing are intentionally unset until usage shows which shared
control is valuable. Revenue is an expansion test, not the first product gate.

## Release gate

Do not market the paid Guard workflow as generally available until clean-install
tests, lifecycle capture, workspace freshness checks, and evidence invalidation
work across the supported path. Public product status must distinguish available
local recovery from Guard features that are still in preview.
