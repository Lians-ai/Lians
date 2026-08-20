# Pre-release launch copy

These drafts remain unpublished until the associated product state, video, and
claims are approved.

## GitHub release or launch page

**Recover the task. Reject stale state. Block unsupported done.**

Lians Guard is the current-state and completion guard for AI coding agents. It
restores interrupted work in supported Claude Code and Codex sessions, checks
whether a checkpoint still matches the repository, and separates measured
evidence from an agent's own completion claim.

- Free local recovery
- No Lians account or provider API key required
- Measured local, measured CI, and human-confirmed evidence
- Clear `RECOVERED`, `STALE`, `BLOCKED`, and `READY FOR HUMAN REVIEW` states

Try the free local recovery path and inspect the Guard developer preview:
<https://github.com/Lians-ai/Lians>

## Short social post

Your coding agent said it was done. What changed? What was measured? Is the
checkpoint still current?

Lians Guard recovers the task, rejects stale state, and keeps unsupported `done`
out of human review. Free local recovery. Guard workflow in developer preview.

<https://github.com/Lians-ai/Lians>

## Show HN

**Show HN: Lians Guard, current-state and completion checks for Claude Code and Codex**

I built Lians because recovering an interrupted agent session is useful, but
recovering the wrong state or trusting an agent's own completion summary is
expensive.

The free path stores bounded local project context and restores it in a later
supported session. The Guard developer preview adds a task contract, typed
evidence, a local Git workspace fingerprint, stale-state detection, and a gate
whose strongest automated result is `READY FOR HUMAN REVIEW`.

Agent summaries and touched files stay visible as context, but they do not
satisfy completion criteria. The repository includes the implementation,
quickstart, trust rules, adversarial tests, limitations, and local controls. I
would especially value feedback from AI-native agencies and small engineering
teams on interrupted work, stale requirements, and review rework.

<https://github.com/Lians-ai/Lians>
