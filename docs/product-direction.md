# Lians product direction

## Product decision

Lians is becoming **Lians Check, the evidence-backed proof-of-done check for AI
coding agents**.

The public promise is:

> Your AI says it is done. Lians checks the receipts.

In plain language:

> Run the real checks. Match them to the current code. Know what still needs
> work before review.

This is a focused verification product, not a general memory platform or
another AI reviewer. Memory and cross-agent handoff remain useful supporting
capabilities. They are not the category Lians should try to own.

## The first customer

The first user is a technical founder, vibe coder, or developer using Claude
Code, Codex, or Cursor to change a real Git repository. The first buyer is an
AI-native software agency or SaaS team that needs a consistent review gate
across several developers and repositories.

The secondary customer is an AI-native SaaS team with 5 to 25 engineers.
Individual developers remain the free user and distribution base.

## The product loop

1. **Initialize once.** `lians init` discovers a short set of high-signal project
   commands and requires the user to authorize them.
2. **Measure current work.** `lians check` runs those commands itself without an
   implicit shell and records bounded evidence.
3. **Bind the receipt.** The result is tied to the current Git state and the
   authorized policy. Changed code or commands require fresh proof.
4. **Show one clear state.** The product reports `NO PROOF`, `NEEDS WORK`, or
   `READY TO REVIEW`, plus the next useful action.

## Free and paid layers

### Free local check and recovery

- measured local checks and signed receipts for the current Git state;
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

1. Make `lians init` and `lians check` reliable on clean macOS, Windows, and
   Linux environments.
2. Keep the three visible states large, direct, and consistent in the terminal,
   desktop app, and GitHub checks.
3. Add the same receipt as an optional required GitHub status check.
4. Run three paid design-partner pilots before widening the product surface.
5. Measure time to first receipt, repeat weekly checks, real unsupported claims
   caught, weekly active repositories, retention, and revenue.

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
