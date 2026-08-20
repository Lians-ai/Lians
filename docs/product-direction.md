# Lians product direction

## Product decision

Lians is becoming **Lians Guard, the current-state and completion guard for AI
coding agents**.

The public promise is:

> Lians recovers interrupted agent work, rejects stale task state, and blocks
> `done` until the current task is ready for human review.

In plain language:

> Your agent can forget the chat. It cannot forget what is finished, what
> changed, or what still has to pass.

This is a focused reliability product, not a general memory platform. Memory
and cross-agent handoff remain important because they create recovery. They are
the free acquisition wedge, not the category Lians should try to own.

## The first customer

The primary customer is an AI-native software agency with 5 to 30 developers
using Claude Code, Codex, Git, and GitHub Actions across several client
repositories. These teams feel the cost of interrupted sessions, repeated
explanations, stale requirements, false completion claims, review rework, and
missed handoffs every week.

The secondary customer is an AI-native SaaS team with 5 to 25 engineers.
Individual developers remain the free user and distribution base.

## The product loop

1. **Recover.** Lians saves a bounded task checkpoint at a supported lifecycle
   event and restores it in a later Claude Code or Codex session.
2. **Check freshness.** The checkpoint is bound to the repository, commit,
   working-tree state, changed-file digest, task definition, and recorded time.
3. **Invalidate stale work.** If a requirement, decision, or repository state
   changes, dependent work is marked stale instead of silently reused.
4. **Evaluate readiness.** Definition-of-done criteria are evaluated using
   typed evidence. Agent prose and file activity alone do not satisfy them.
5. **Show one clear state.** The product reports `RECOVERED`, `STALE`, `BLOCKED`,
   or `READY FOR HUMAN REVIEW` with the evidence and remaining work visible.

## Free and paid layers

### Free local recovery

- local, inspectable task save points;
- bounded resume context across supported Claude Code and Codex sessions;
- current-state correction and stale-history exclusion;
- no Lians account, AI account password, or provider API key; and
- open-source tools for individual developers.

### Paid team guard

- authoritative task state across a team;
- stale-state detection and invalidation;
- definition-of-done policies connected to Git and CI evidence;
- shared queues, reporting, administration, support, and incident review; and
- managed deployment for agencies and AI-native engineering teams.

Pricing is a hypothesis until pilots validate willingness to pay. The current
pilot offer is $1,000 for 30 days, up to 5 developers and 3 repositories. A
successful pilot should reduce repeated explanation, stale-state incidents,
unsupported completion claims, or review rework in a way the customer can
measure.

## Evidence trust model

Every criterion and constraint record has a trust class:

| Trust class | Meaning | Can satisfy a completion criterion? |
|---|---|---|
| `measured_local` | Produced by a local command or deterministic inspection | Yes |
| `measured_ci` | Produced by a trusted CI run | Yes |
| `human_confirmed` | Explicitly confirmed by an authorized human | Yes |
| `agent_attested` | Reported by an AI agent or session summary | No |
| `inferred_activity` | Inferred from touched files, messages, or other activity | No |

Failed evidence remains a blocker regardless of its source. Positive evidence
must be measured or human-confirmed. A changed file is an artifact, not proof
that the requested behavior works.

Trust labels are types, not caller permissions. An agent-facing MCP or Bridge
call that declares `measured_local`, `measured_ci`, or `human_confirmed` is stored
as `agent_attested` and its declared label remains visible for audit. Satisfying
evidence enters only through a Lians-owned local verifier, an attested CI import,
or an explicit interactive human confirmation.

`READY FOR HUMAN REVIEW` never means that the work is correct, approved,
merged, or safe to deploy. It means the configured review gate has the required
current evidence and no known failed, unknown, or blocked constraint.

## What to build now

1. Make automatic save, resume, stale detection, and the four visible states
   reliable for Claude Code, Codex, Git, and GitHub Actions.
2. Bind checkpoints to workspace fingerprints and invalidate evidence when its
   inputs no longer match.
3. Make installation and removal predictable on clean macOS, Windows, and Linux
   environments.
4. Run three paid design-partner pilots before widening the product surface.
5. Measure recovery success, stale-state detections, blocked unsupported claims,
   review rework, weekly active repositories, retention, and revenue.

## What not to lead with

Do not lead the product or onboarding with generic memory, more agent SDKs,
browser and research features, video tools, 3D graphs, formal-proof language,
mode selection, or token-reduction claims. Keep useful existing capabilities
available behind progressive disclosure while the Guard workflow becomes
reliable.

## Claim boundary

Lians may say that it recorded, recovered, measured, detected, invalidated, or
blocked something when an inspectable record supports that statement. It may
say `ready for human review` when the configured gate passes.

Lians must not claim that it proves semantic correctness, guarantees a safe
deployment, eliminates hallucinations, completes human review, or saves a fixed
percentage of time, tokens, or money without matching production evidence.

See [the Guard product contract](lians-guard.md) for the user-facing states,
economic model, and implementation boundary. See the
[August 2026 market pressure test](market-pressure-test-2026-08.md) for the
competitive, consumer, and investor claim boundary.
