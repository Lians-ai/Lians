# Lians Check market pressure test

Date: 2026-08-20

This is a decision document, not a fundraising claim. Repository behavior is
marked separately from customer and revenue evidence.

## Verdict

Lians has a credible universal company thesis and a narrow product wedge in
**Lians Check**, but the repository alone cannot prove product-market fit. The
universal action is Check. The first supported lane is code. The repo can prove
a sharper trust boundary, current-state checks, installability, and test
coverage. Only real users can prove activation, repeated use, distribution, and
time saved.

The current strategic position is:

> Ready for a free first-100-user pressure test after the clean-install workflow
> passes on the supported platforms. Not yet market-proven.

## Universal wedge pressure test

The broad opportunity is not "AI for everyone." That language is too vague to
create a habit. The opportunity is one repeated moment shared by many people:

> AI says the work is done. Check it before you use it.

Current evidence supports the scale of the moment:

- Gallup reported in February 2026 that [half of employed US adults use AI at
  work at least a few times a year](https://www.gallup.com/workplace/704225/rising-adoption-spurs-workforce-changes.aspx).
- Gallup also found that only [27 percent of Americans trust businesses to use
  AI responsibly](https://news.gallup.com/poll/712751/americans-cool-toward.aspx).
- Google DeepMind says factuality remains an open problem and publishes
  imperfect results even for leading models in its
  [FACTS benchmark suite](https://deepmind.google/blog/facts-benchmark-suite-systematically-evaluating-the-factuality-of-large-language-models/).
- NIST's work on [AI agent security and
  identity](https://www.nist.gov/publications/summary-analysis-responses-request-information-regarding-security-considerations-ai)
  highlights the need to establish what an agent did and which authority or
  evidence supports it.

The evidence does not validate a generic fact-checking chatbot. That category
is crowded, and a second model opinion is not independent proof. Lians survives
the pressure test only when it connects to an evidence source outside the
generating model, binds the result to the exact work, and fails closed.

| Question | Honest answer |
|---|---|
| Can the brand serve everyone who uses AI? | Yes, through the universal Check moment |
| Can the product verify every kind of AI work today? | No, only the code lane is implemented |
| Is the first lane too narrow for the company? | No, if the proof contract transfers to later evidence adapters |
| Is this replaceable by ChatGPT or Claude? | Not when Lians runs external checks and binds receipts to exact state |
| Does broad demand prove Lians demand? | No, repeated checks and referrals must prove it |

## Multichannel demand update

The current and historical market evidence supports verification as a problem,
but not a broad new review bot:

- YC companies including [Stage](https://www.ycombinator.com/companies/stage),
  [cubic](https://www.ycombinator.com/companies/cubic), and
  [Greptile](https://www.ycombinator.com/companies/greptile) validate the review
  bottleneck. [Canary](https://www.ycombinator.com/companies/canary),
  [Autosana](https://www.ycombinator.com/companies/autosana), and
  [TesterArmy](https://www.ycombinator.com/companies/testerarmy) validate demand
  for testing and QA.
- Greptile and cubic publish paid team pricing around $30 per developer per
  month, which demonstrates adjacent willingness to pay. It does not prove that
  teams will pay Lians.
- Claude Code issue
  [63861](https://github.com/anthropics/claude-code/issues/63861) reports an agent
  claiming work was verified without running the canonical build. Claude Code
  issue [4462](https://github.com/anthropics/claude-code/issues/4462) reports
  subagents claiming files were created when they were not.
- Sonar's 2026 State of Code survey reports that 96 percent of respondents do
  not fully trust AI-generated code, only 48 percent always verify it, and 38
  percent say reviewing AI code requires more effort than reviewing human code.
- Native memory and review from GitHub, Cursor, Claude, and Codex weaken generic
  memory and generic review as standalone categories.
- Small open-source proof-gate tools demonstrate repeated founder pain, but
  their limited adoption means the exact Lians Check package still needs direct
  retention and distribution validation.

The decision from this evidence is to make the consumer surface smaller than
the underlying Guard system:

> Your AI says it is done. Lians checks the receipts.

The first loop is `lians init`, followed by `lians check`, followed by one of
`NO PROOF`, `NEEDS WORK`, or `READY TO REVIEW`.

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

Lians should own the boundary between an agent's claim and a human's review.
The public product is Lians Check. Guard remains the underlying trust contract:

1. restore one bounded current task;
2. compare it with the current repository state;
3. downgrade agent-declared proof to an attestation;
4. accept measured evidence only through a Lians-controlled verifier, an exact
   attested GitHub Actions workflow with an interactively authorized criterion
   mapping, or explicit interactive human confirmation;
5. mark mismatches stale; and
6. show one consumer result: `NO PROOF`, `NEEDS WORK`, or `READY TO REVIEW`.

That is narrower than memory and more useful than another session recorder.

## Consumer pressure test

| What a developer needs | Current repo answer | Score | Required proof |
|---|---|---:|---|
| Understand the value immediately | One Check promise and three visible states | 4/5 | Five-user comprehension test |
| Reach first value in minutes | A two-command Check path exists, but no live activation data exists | 2/5 | Median install-to-first-receipt under 5 minutes |
| Keep the existing AI workflow | MCP and supported lifecycle hooks, no replacement chat UI | 4/5 | Live Claude Code and Codex sessions |
| Trust the result | Agent self-promotion is blocked; CI evidence is attested and commit-bound | 4/5 | Adversarial import and stale-state tests in production |
| Stay private and in control | Local encrypted state, correction, and deletion paths exist | 4/5 | Clean uninstall and deletion usability study |
| See a concrete benefit | Check exposes missing, failed, and ready proof | 2/5 | Unsupported-claim and review-rework baseline versus cohort |
| Return every week | No external retention data exists | 0/5 | Four-week cohort retention |

The consumer launch message should not explain the whole architecture. It
should show the relief: an AI says the work is done, Lians catches missing or
failed proof, and the user knows what still needs review.

## Investor pressure test

Current investor writing consistently emphasizes a sharp wedge, retention,
efficient growth, durable margins, and differentiated data or evaluation loops.
The useful pressure is not whether Lians can make a larger feature list. It is
whether a narrow workflow becomes habitual and expands inside teams.

| Investment question | Current evidence | Honest answer |
|---|---|---|
| Is the problem clear? | Check action, current lane, and visible states are documented | Yes, at hypothesis level |
| Is the product technically differentiated? | Trust boundary, workspace binding, attested CI intake, and provider-neutral task state | Promising, not yet a moat |
| Is there a distribution loop? | Open-source repo and integrations exist | Not yet observed |
| Do users retain? | No live cohort data | Unknown |
| Will teams pay? | No pricing evidence exists | Unknown and not the first gate |
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
| Problem clarity | 4/5 | The Check action is specific and understandable |
| Technical differentiation | 3.5/5 | Trust and freshness are real; competitors can still move into the space |
| Activation | 2/5 | Clean-install CI is added, but live onboarding data does not exist |
| Trust and evidence | 4/5 | Agent self-promotion is blocked and CI import requires exact attestation and commit binding |
| Resistance to native competition | 3/5 | Provider neutrality helps, but native tools own distribution |
| Distribution | 2/5 | The repo can attract users, but no repeatable acquisition loop is measured |
| Retention | 0/5 | No cohort evidence |
| Willingness to pay | 0/5 | No paid-pilot evidence |

## Validation gates

The following are operating hypotheses. They are not customer results:

1. Get 100 real users to attempt Lians Check on non-confidential repositories.
2. Get 70 percent of qualified installs to a first Lians Check receipt within
   five minutes.
3. Get at least 50 percent of activated users to run a second check
   within seven days.
4. Have at least 20 percent of activated users share a receipt or invite one
   collaborator after a useful check.
5. Retain at least 40 percent of active users through week four before widening
   the roadmap.
6. Produce user-reviewed evidence of reduced repeated explanation, stale
   work, unsupported completion, or review rework before making savings claims.
7. Test team pricing only after repeated individual use reveals a real need for
   shared policy, CI enforcement, or audit controls.

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
