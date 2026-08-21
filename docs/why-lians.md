# Why Lians

## The short answer

Lians is the evidence-backed proof layer for AI work. Its first supported check
is code.

> Your AI says it is done. Lians checks the receipts.

The universal trigger is not a job title. It is the moment any person uses AI
work and has to decide whether to trust it. Lians is useful when accepting a
completion claim without current measured evidence would create rework, delay,
or harm.

Claude Code, Codex, and other agents can already store instructions, recall
context, generate work, and review their own output. Lians must not compete by
adding another chat box or asking another model for an opinion. It earns a place
in the workflow by checking evidence the model does not control.

## The pressure test

Persistent memory, vector search, MCP, local SQLite, and multi-client support
are useful but not scarce. Native agent products and dedicated memory tools
already cover much of that surface. A broad promise to remember more therefore
creates weak urgency and weak willingness to pay.

The expensive problem is execution state:

- an interrupted session loses the current task and next action;
- a requirement changes while old work still looks current;
- an agent reports success based on its own summary;
- touched files are mistaken for finished work; or
- a reviewer must reconstruct intent, changes, checks, and blockers by hand.

## What Lians is trying to own

The differentiation is a narrow Guard contract:

1. **Recovery.** Restore a bounded current task after interruption or an agent
   switch.
2. **Freshness.** Bind checkpoints to repository and task state, then mark
   mismatches stale.
3. **Evidence.** Separate measured local, measured CI, and human-confirmed proof
   from agent attestations and inferred activity.
4. **Readiness.** Keep the gate closed while criteria are missing or constraints
   are failed, unknown, or blocked.
5. **Human control.** Make the result inspectable and require human review.

The first implementation applies that contract to code because tests, builds,
lint, and Git provide objective evidence. Research, spreadsheets, documents,
and completed actions are future lanes only if Lians can connect each one to an
authoritative source and fail closed when proof is absent.

## Which approach should I use?

| Need | Best starting point |
|---|---|
| Stable repository instructions | `AGENTS.md`, `CLAUDE.md`, or repository instructions |
| Convenience inside one agent product | That product's native memory |
| A full record of one conversation | Export or retain the transcript |
| A general memory API for an application | A dedicated memory platform |
| Local recovery across supported coding agents | Free Lians recovery |
| Stale-state and evidence-backed review gates | Lians Guard |

Lians is additive. It does not replace source control, CI, the model, repository
instructions, or human review.

## Claim rules

Public comparisons and benchmarks must use identical scenarios, publish the
fixture and configuration, distinguish unsupported from failed, and retire a
claim when a rerun no longer supports it.

Lians can report what its configured policy measured, detected, invalidated, or
blocked. It cannot prove semantic correctness or guarantee safe deployment.

Read [the product direction](product-direction.md), [the Guard contract](lians-guard.md),
and [ContinuityBench v0.1](benchmarks/continuitybench-v0.1.md).
