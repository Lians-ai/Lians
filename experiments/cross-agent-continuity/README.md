# Lians cross-agent project continuity experiment

This experiment tests one product claim:

> Start a fresh Codex session after working in Claude Code without re-explaining the project.

It is not a second memory framework. The experiment is a thin mapper and evaluator over the
existing local Lians runtime.

## Result

The committed synthetic fixture currently produces:

| Measure | Result |
| --- | ---: |
| Expected continuity facts recovered | 10 / 10 |
| Continuity accuracy | 100% |
| Stale facts presented as current | 0 |
| Selected continuity items | 11 |
| Estimated handoff size | 231 tokens |
| Active project memories considered | 8 |
| Superseded facts excluded | 1 |

These results prove the deterministic extraction, supersession, scoping, and bounded-selection
path for the included fixture. They do not prove that every live agent session will be extracted
perfectly or that a second agent will never repeat work. The latter requires the live comparison
test described below.

The production hook path was also exercised on Windows with Claude Code 2.1.210. Claude changed
two fixture files, ran pytest successfully, and intentionally left one documentation task open.
The real `SessionEnd` hook automatically captured three completed items, the unfinished item, the
pytest decision, the v1-to-v2 supersession, touched files, and do-not-redo constraints without
storing the transcript. The candidate Codex `UserPromptSubmit` hook then emitted a signed,
project-scoped continuation brief bounded to 1,809 characters. This validates the live hook seam;
it is not a claim that arbitrary agent prose will always extract perfectly. The acceptance prompt
requested the explicit section headings consumed by the deterministic extractor; the user did not
write or paste a handoff and did not call `remember` manually.

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

The JSON session record remains the deterministic fixture contract. In an installed client, the
Claude `SessionEnd` hook reads only a bounded JSONL transcript tail and maps explicit headings,
tool-touched files, and current work state into the same Lians primitives. The transcript is
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
2. Work in Claude on one repository and end the session normally.
3. Lians captures explicit completed work, open work, decisions, changes, constraints, and touched
   files automatically from Claude's `SessionEnd` evidence. Do not write a manual Markdown handoff.
4. Open a fresh Codex session in the same Git repository.
5. Ask `Pick up where we left off.`

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

## Current boundary

The existing Lians primitives cover scope, supersession, work state, bounded retrieval, receipts,
and cross-agent injection. The production Claude session-end adapter now performs conservative,
deterministic extraction from explicit session language and file-tool evidence. It intentionally
does not use a hidden model call, retain transcripts, infer unspoken decisions, or claim that test
output is verified beyond Claude's reported evidence.

This is ready for public beta testing. It is not a general-availability claim. The next evidence
gate is repeated live Claude-to-Codex comparison across varied real repositories, measuring
extraction misses and redundant work rather than presenting one successful path as universal.
