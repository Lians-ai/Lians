# Lians Guard market pressure test

Date: 2026-08-19

This is a decision document, not a fundraising claim. Repository behavior is
marked separately from customer and revenue evidence.

## Verdict

Lians has a credible product wedge, but the repository alone cannot make it
venture-ready or prove product-market fit. The repo can prove a sharper trust
boundary, current-state checks, installability, and test coverage. Only real
users can prove activation, retention, willingness to pay, distribution, and
time saved.

The current strategic position is:

> Pilot-ready after the clean-install workflow passes on the supported
> platforms. Not yet market-proven or venture-ready.

## What the market invalidates

The following are not strong standalone differentiation in the current market:

- cross-session memory;
- summaries and context compression;
- Git-linked agent session replay;
- native agent checkpoints;
- MCP connectivity;
- local storage;
- a task dashboard; and
- broad claims about saving tokens.

Native and adjacent products already cover much of that surface. See the
[current competitive landscape](competitive-landscape.md).

## The wedge that survives

Lians should own the boundary between an agent's claim and a human's review:

1. restore one bounded current task;
2. compare it with the current repository state;
3. downgrade agent-declared proof to an attestation;
4. accept measured evidence only through a Lians-controlled verifier, an exact
   attested GitHub Actions workflow with an interactively authorized criterion
   mapping, or explicit interactive human confirmation;
5. mark mismatches stale; and
6. show one result: `RECOVERED`, `STALE`, `BLOCKED`, or
   `READY FOR HUMAN REVIEW`.

That is narrower than memory and more useful than another session recorder.

## Consumer pressure test

| What a developer needs | Current repo answer | Score | Required proof |
|---|---|---:|---|
| Understand the value immediately | One Guard promise and four visible states | 4/5 | Five-user comprehension test |
| Reach first value in minutes | Local recovery exists, Guard setup still has several surfaces | 2/5 | Median install-to-first-recovery under 5 minutes |
| Keep the existing AI workflow | MCP and supported lifecycle hooks, no replacement chat UI | 4/5 | Live Claude Code and Codex sessions |
| Trust the result | Agent self-promotion is blocked; CI evidence is attested and commit-bound | 4/5 | Adversarial import and stale-state tests in production |
| Stay private and in control | Local encrypted state, correction, and deletion paths exist | 4/5 | Clean uninstall and deletion usability study |
| See a concrete benefit | Local reports can expose recovered, stale, blocked, and ready events | 2/5 | Repeated-explanation and review-rework baseline versus pilot |
| Return every week | No external retention data exists | 0/5 | Four-week cohort retention |

The consumer launch message should not explain the whole architecture. It
should show the relief: work resumes with the current task intact, stale claims
are caught, and the user knows what still needs review.

## Investor pressure test

Current investor writing consistently emphasizes a sharp wedge, retention,
efficient growth, durable margins, and differentiated data or evaluation loops.
The useful pressure is not whether Lians can make a larger feature list. It is
whether a narrow workflow becomes habitual and expands inside teams.

| Investment question | Current evidence | Honest answer |
|---|---|---|
| Is the problem clear? | Guard category, target customer, and visible states are documented | Yes, at hypothesis level |
| Is the product technically differentiated? | Trust boundary, workspace binding, attested CI intake, and provider-neutral task state | Promising, not yet a moat |
| Is there a distribution loop? | Open-source repo and integrations exist | Not yet observed |
| Do users retain? | No live cohort data | Unknown |
| Will teams pay? | Pilot price and packaging are hypotheses | Unknown |
| Can the company expand? | Team policy, reporting, regulated controls, and managed deployment are plausible expansions | Unproven |
| Are margins durable? | Local execution can reduce hosted inference cost, but support and enterprise operations are unmeasured | Unknown |
| Is there proprietary compounding data? | The architecture can collect bounded failure events | No meaningful corpus yet |

Relevant primary sources:

- Bessemer's [State of AI 2025](https://www.bvp.com/atlas/the-state-of-ai-2025)
  distinguishes fast-growing but fragile products from businesses with stronger
  retention and margins, and identifies memory and context as emerging moats.
- a16z's [enterprise AI review](https://a16z.com/ai-enterprise-2025/) emphasizes
  product quality, speed, and the way strong user pull can drive enterprise
  adoption.
- a16z's [consumer AI review](https://a16z.com/state-of-consumer-ai-2025-product-hits-misses-and-whats-next/)
  shows how concentrated paid consumer behavior remains, which raises the bar
  for habit and differentiation.
- Sequoia's [product-market-fit framework](https://sequoiacap.com/article/pmf-framework/)
  and [sustainable product growth](https://articles.sequoiacap.com/sustainable-product-growth)
  place retention and differentiated value ahead of launch attention.
- Sequoia's [Generative AI Act Two](https://sequoiacap.com/article/generative-ai-act-two/)
  frames proven value and retention as harder problems than initial demand.

## Current scorecard

These are internal judgments, not measured market scores.

| Dimension | Score | Why |
|---|---:|---|
| Problem clarity | 4/5 | The Guard promise is specific and understandable |
| Technical differentiation | 3.5/5 | Trust and freshness are real; competitors can still move into the space |
| Activation | 2/5 | Clean-install CI is added, but live onboarding data does not exist |
| Trust and evidence | 4/5 | Agent self-promotion is blocked and CI import requires exact attestation and commit binding |
| Resistance to native competition | 3/5 | Provider neutrality helps, but native tools own distribution |
| Distribution | 2/5 | The repo can attract users, but no repeatable acquisition loop is measured |
| Retention | 0/5 | No cohort evidence |
| Willingness to pay | 0/5 | No paid-pilot evidence |

## Validation gates

The following are operating hypotheses. They are not customer results:

1. Interview 30 qualified AI-native teams and identify the same Guard failure
   in at least 15.
2. Get 60 percent of qualified installs to a first recovery or blocked stale
   claim within 24 hours.
3. Get at least 50 percent of active pilot repositories to record a second
   meaningful Guard event in week two.
4. Close three 30-day paid design-partner pilots at $1,000 or more.
5. Retain at least 40 percent of pilot teams through week four before widening
   the roadmap.
6. Produce customer-reviewed evidence of reduced repeated explanation, stale
   work, unsupported completion, or review rework before making savings claims.

If Lians cannot pass these gates, add no new broad platform surface. Narrow the
problem, buyer, or activation loop instead.

## Repository actions from this pressure test

- Agent-facing checkpoint calls cannot grant themselves measured or
  human-confirmed trust.
- GitHub Actions evidence is accepted only after GitHub attestation verification
  for an exact repository, workflow, ref, commit, and hosted runner, plus an
  interactive authorization of the check-to-criterion mapping.
- Human confirmation requires an interactive exact confirmation phrase.
- Claude lifecycle capture covers supported `PreCompact` and `SessionEnd`
  events.
- The new workflow requires clean install on Linux, macOS, and Windows; its
  first hosted run is pending.
- The new workflow requires native GUI launch on macOS and Windows; its first
  hosted run is pending.
- A local Guard report exposes state counts and missing or untrusted evidence
  without claiming business outcomes.
- The repository-wide lint policy is explicit and centered on correctness.

## Claims still prohibited

Do not say that Lians saves a fixed amount of time, tokens, or money; proves
semantic correctness; guarantees deployment safety; has product-market fit;
has a moat; or is venture-ready until matching external evidence exists.
