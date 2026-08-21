# Lians product direction

## Product decision

Lians is the **evidence-backed proof layer for AI work**.

The one-word action is:

> Check.

The public promise is:

> Your AI says it is done. Lians checks the receipts.

The company can serve anyone who uses AI without launching as a vague product
for everyone. Lians markets one universal moment: AI produced something, and a
person needs to know whether it is ready to use.

The first supported check is code. This is a capability wedge, not a permanent
customer niche. Code comes first because tests, builds, lint, and Git provide
objective evidence that Lians can measure without asking an AI to grade itself.

## Why this is a real universal problem

AI use is broad enough to support a universal behavior. Gallup reported in
February 2026 that [half of employed US adults use AI at work at least a few
times a year](https://www.gallup.com/workplace/704225/rising-adoption-spurs-workforce-changes.aspx).
The unresolved problem is trust. Gallup separately found that only
[27 percent of Americans trust businesses to use AI
responsibly](https://news.gallup.com/poll/712751/americans-cool-toward.aspx).

Model quality does not remove the need for checks. Google DeepMind describes
factuality as an ongoing research problem and reports that even leading models
remain imperfect on its
[FACTS benchmark suite](https://deepmind.google/blog/facts-benchmark-suite-systematically-evaluating-the-factuality-of-large-language-models/).
NIST is also developing guidance around
[AI agent security and identity](https://www.nist.gov/publications/summary-analysis-responses-request-information-regarding-security-considerations-ai),
which reinforces the need to know what an agent actually did and what evidence
supports the result.

These sources validate a broad trust gap. They do not prove demand for Lians.
Only activation, repeated checks, sharing, and retention can do that.

## Why this is not another chatbot

A chatbot generates or judges an answer with a model. Lians must do something a
prompt alone cannot do:

1. connect to evidence outside the model;
2. run or inspect the authoritative check;
3. bind the receipt to the exact work that was checked;
4. fail closed when proof is absent or stale; and
5. hand the evidence to a person for review.

If a proposed feature can be replaced by pasting the same request into ChatGPT
or Claude, it is not the Lians wedge.

## The current product loop

1. **Initialize once.** `lians init` discovers a short set of high-signal
   project commands and requires the user to authorize them.
2. **Check current work.** `lians check` runs those commands itself without an
   implicit shell and records bounded evidence.
3. **Bind the receipt.** The result is tied to the current Git state and the
   authorized policy. Changed code or commands require fresh proof.
4. **Show one clear state.** Lians reports `NO PROOF`, `NEEDS WORK`, or `READY TO
   REVIEW`, plus the next useful action.
5. **Invite the next check.** A receipt can be shared with a reviewer or team,
   creating the product-led distribution loop.

## Expansion without losing focus

The brand is universal. Each capability remains narrow until it works.

| Lane | Authoritative evidence | Status | Earliest build gate |
|---|---|---|---|
| Code | Tests, builds, lint, and Git state | Developer preview | Current focus |
| Research | Live source access, dates, quotes, and claim support | Hypothesis | 25 repeated user requests and a deterministic source-check prototype |
| Spreadsheets | Formulas, totals, constraints, and source reconciliation | Hypothesis | 25 repeated user requests and a safe workbook-check prototype |
| Documents | Required sections, fields, links, and cited source support | Hypothesis | 25 repeated user requests and an inspectable requirements contract |
| Completed actions | Confirmation from the system where the action occurred | Hypothesis | 25 repeated user requests and an authoritative integration receipt |

Every new lane must pass five tests:

1. The problem occurs repeatedly for many kinds of people.
2. A failed result has a real cost in time, money, trust, or risk.
3. Lians can inspect evidence the generating model does not control.
4. The result can be expressed as one clear state and one next action.
5. The receipt naturally reaches another person or another workflow.

Do not build a lane that fails any of these tests.

## Product-led distribution

The distribution unit is the receipt, not a generic social post.

- **Zero-friction start:** no Lians account, provider password, or model API key
  for the local code check.
- **Immediate value:** the first successful path stays `lians init`, then
  `lians check`.
- **Shareable outcome:** every receipt should have a small, safe summary with
  the state, checked commit, check names, and timestamp. Private code and raw
  logs remain private by default.
- **Referral moment:** after a useful second check, ask the user to share the
  receipt or invite one collaborator. Do not interrupt the first check.
- **Use-case search pages:** publish pages around moments such as "check AI
  generated code," "verify AI citations," and "check AI spreadsheet formulas."
  Only the code page may claim a working product today. Future pages collect
  demand and clearly say what is not built.
- **Short-form demos:** show the agent claiming completion, Lians catching a
  failed check, the fix, and a new ready receipt in 15 to 30 seconds.
- **Brand personality:** calm, plain, and slightly skeptical. Lians is the
  friend who asks to see the receipt, not a security company trying to scare
  people.

## First 100 users

Revenue is not the first gate. Repeated use is.

The first launch cohort is free and centered on one promise:

> Let your AI finish the task. Run Lians before you trust "done."

For each of the first 100 users, record only the minimum safe funnel data:

1. repository type and AI tool;
2. whether installation completed;
3. time to first receipt;
4. first result state;
5. whether Lians caught something useful;
6. whether the user ran a second check within seven days; and
7. whether a receipt or invitation brought in another user.

Direct outreach can begin with code users because that is the working lane, but
the public message should market the moment, not label Lians as a developer-only
company.

## Next 72 hours

### Hours 0 to 12

- keep the two-command path reliable on a clean machine;
- make the three states visually unmistakable;
- generate a privacy-safe receipt summary; and
- record anonymous local funnel events only with explicit permission.

### Hours 12 to 36

- publish one real 20-second demo;
- publish the "check AI generated code" use-case page;
- recruit the first 20 users from people already shipping with coding agents;
  and
- watch five installs live without explaining the interface.

### Hours 36 to 72

- fix the three largest activation failures;
- contact enough users to reach 100 qualified attempts;
- publish anonymized examples of failures Lians caught;
- add the referral ask after the second useful check; and
- decide from evidence whether to improve code activation or prototype one new
  lane. Do not widen the product because a new idea sounds larger.

## Metrics

The primary metric is **second check within seven days**. It is the first signal
that Check is a behavior rather than a demo.

Track:

- visitor to install;
- install to first receipt;
- median time to first receipt;
- percent of receipts that catch useful missing or failed proof;
- second check within seven days;
- weekly active checking repositories;
- receipts shared or collaborators invited;
- new users activated from a receipt; and
- four-week retained users.

Do not optimize signups, impressions, or repository stars while repeat checks
remain weak.

## Revenue later

Keep the individual Check loop free while establishing habit and distribution.
Paid value becomes credible when multiple people need shared proof policies,
CI enforcement, audit history, administration, or managed support.

Pricing remains a hypothesis until retained teams ask for those controls. Do
not place pricing work ahead of activation, the second check, or referrals.

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

Trust labels are types, not caller permissions. An agent-facing call that
declares `measured_local`, `measured_ci`, or `human_confirmed` is stored as
`agent_attested` and its declared label remains visible for audit. Satisfying
evidence enters only through a Lians-owned verifier, an attested CI import, or
an explicit interactive human confirmation.

`READY FOR HUMAN REVIEW` never means that the work is correct, approved,
merged, or safe to deploy. It means the configured review gate has the required
current evidence and no known failed, unknown, or blocked constraint.

## Explicit non-goals

Do not build or lead with:

- a general chat interface;
- an AI writing assistant;
- another model that grades model output;
- generic memory or transcript replay;
- broad agent SDKs and mode selection;
- autonomous high-risk actions without human approval;
- a dashboard before the receipt loop works;
- unsupported claims about correctness, safety, time saved, or money saved; or
- simultaneous launches for research, spreadsheets, documents, and actions.

Useful existing recovery capabilities can stay available behind progressive
disclosure. They are not the category Lians should try to own.

## Claim boundary

Lians may say that it recorded, recovered, measured, detected, invalidated, or
blocked something when an inspectable record supports that statement. It may
say `ready for human review` when the configured gate passes.

Lians must not claim that it proves semantic correctness, guarantees a safe
deployment, eliminates hallucinations, completes human review, or saves a fixed
percentage of time, tokens, or money without matching production evidence.

See [the Guard product contract](lians-guard.md) for the implementation boundary
and the [August 2026 market pressure test](market-pressure-test-2026-08.md) for
the competitive and evidence boundary.
