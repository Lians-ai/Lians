# Lians cross-agent project continuity experiment

This experiment tests one product claim:

> Start a fresh Codex session after working in Claude Code without re-explaining the project.

It is not a second memory framework. The experiment is a thin mapper and evaluator over the
existing local Lians runtime.

## What it reuses

- repository-derived project identity from `lians_easy.project`
- encrypted project-scoped memory from `MemoryStore`
- named current state and bitemporal supersession from `set_current`
- work status, evidence, blockers, decisions, and signed briefs from `TaskContractService`
- automatic bounded task injection from the existing Claude and Codex prompt hooks

No core change was required for the experiment.

## Data flow

```text
Claude session evidence
        ↓
session-end structured extraction
        ↓
task contract + current named Lians state
        ↓
fresh Codex prompt hook
        ↓
bounded project continuity context
```

The JSON session record is the output contract for a future Claude session-end hook. It is
evidence input, not the canonical handoff and not a stored transcript. The canonical state stays
inside Lians. The handoff is generated when the next agent asks to continue.

## Reproduce the fixture

From the repository root:

```powershell
$experiment = "experiments/cross-agent-continuity/continuity_experiment.py"
$database = "$env:TEMP/lians-cross-agent-demo.sqlite3"

python $experiment --data $database --project-root . capture `
  --session experiments/cross-agent-continuity/fixtures/claude-session.json `
  --client claude

python $experiment --data $database --project-root . show --client codex

python $experiment --data $database --project-root . evaluate `
  --expected experiments/cross-agent-continuity/fixtures/expected.json
```

The expected handoff says that the v2 implementation and tests are complete, documentation is
still open, pytest must remain, the v1 route is stale, and documentation is the next action.

## Real Claude to Codex path

1. Install Lians for both Claude Code and Codex using the existing installer.
2. Work in Claude on one repository. Use the existing `start_task` and `checkpoint_task` tools, or
   export the session-end JSON contract used by this experiment.
3. Run `capture` once at session end. Do not write a manual Markdown handoff.
4. Close Claude completely.
5. Open a fresh Codex session in the same Git repository.
6. Ask `Pick up where we left off.`

The existing Codex prompt hook detects the repository, finds the only unresolved task contract,
and injects bounded current Lians state. To inspect the exact derived view without an agent, run
`show`.

If several active tasks exist, Lians returns their IDs and injects no task. Pass `--task-id` after
the user chooses one. The experiment never guesses across concurrent goals.

## Acceptance questions

In the fresh Codex session, ask:

```text
What was completed in the previous work session?
What remains unfinished?
What decisions were made?
What changed during the prior session?
What should you avoid redoing or reversing?
What should you work on next?
```

Then ask Codex to continue. Record whether it repeats completed work or reverts a superseded fact.
The offline evaluator cannot honestly measure agent behavior, so redundant-work rate remains a live
observation rather than a fabricated number.

## Metrics

`show --json` reports:

- continuity context token estimate
- selected continuity item count
- active project memories available
- stale facts excluded from current state
- re-explanation facts avoided

`evaluate` reports continuity accuracy and stale-fact error rate against the fixture. Passing means
at least 80% of expected facts are correct and no stale fact is presented as current.

Run the automated tests with:

```powershell
python -m pytest -q packages/lians-easy/tests/test_cross_agent_continuity_experiment.py
```

## Comparison conditions

For a live evaluation, run the same continuation task four times:

| Condition | Context supplied |
| --- | --- |
| A | none |
| B | static `AGENTS.md` or `CLAUDE.md` only |
| C | human-written summary |
| D | generated Lians continuity |

Compare correctness, stale assumptions, repeated work, user re-explanation, context tokens, and time
to the first useful action. Lians does not need to beat a careful human summary. It needs to approach
that handoff quality without making the human write it.

## Current boundary and missing automation

The existing Lians primitives cover scope, supersession, work state, bounded retrieval, receipts,
and cross-agent injection. The remaining integration gap is a production Claude session-end hook
that automatically emits this structured extraction from transcript evidence, tool calls, diffs,
commits, and tests. This experiment intentionally defines and validates that seam without adding a
transcript database or changing the core memory model.
