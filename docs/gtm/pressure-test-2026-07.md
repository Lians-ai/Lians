# Pressure Test — Lians GTM Plan vs. Reality

*July 3, 2026. Written to be disagreed with. Read after `lians-gtm-plan.md`.*

## The one-line diagnosis

The product is materially ahead of the plan (most of "Build A" and all of §4's
table stakes already exist in the repo), and the distribution is materially
behind it: the public repo has **1 star**, appears in **zero** third-party
roundups, and no artifact has ever been put in front of an audience. Every
engineering day spent from here has worse marginal return than a launch day.
The binding constraint is not code. Stop building; start shipping artifacts
to strangers.

## What's actually true today (verified)

- Public repo (`Lians-ai/Lians`), Apache-2.0, v0.3.2 on PyPI/npm, MCP registry
  listing, 5 SDK languages, 5 framework integrations, 557+ tests.
- Regulated eval exists with 6 systems scored; results doc published in-repo.
- Lookahead demo (Build B) now exists: Sharpe 4.6 vs −0.6, 918 logged leaks,
  reproducible in 30s with no keys.
- Comparison pages (Zep, mem0), alternatives pages, migration guides, three
  regulatory deep-dives, distribution kit, roundup target list: all drafted.
- 1 GitHub star. Zero inbound. Zero design-partner conversations logged.

## Where a skeptic lands the punches

### 1. The benchmark's competitor columns are self-assessed (highest risk)

The regulated eval runs Lians live but scores competitors from
"capability maps" encoded in our own adapters. The results doc is honest about
this, but the screenshot that circulates will be the 5.0-vs-1.0 table, not the
methodology note. On HN this reads as *"vendor benchmarks own product, wins
5/5."* One credible rebuttal ("mem0 actually has X, your adapter is wrong")
poisons the entire compliance-axis strategy, which is the whole strategy.

**Fix before promoting the scores anywhere:**
- Execute at least Mem0 OSS and Graphiti columns **live** (both pip-installable,
  self-hostable — no API keys required for their OSS paths). "We ran their
  code" is a different sentence than "we read their docs."
- Send each vendor the methodology + their column with a 2-week right-of-reply
  window, *documented in the repo* ("contacted 2026-07-xx, response: …").
  Their silence then becomes our credibility.
- The GTM plan's own rule — publish the harness before the scores — was
  effectively skipped (both are already in-repo, but nobody has seen either;
  sequencing can still be honored publicly: methodology post first, scores
  post one week later).

### 2. "Compliance-grade" claims outrun certification

Docs say "SEC 17a-4 posture," "SOC 2 readiness," "HIPAA safeguard mapping."
One compliance officer asking "are you SOC 2 certified?" gets a no. That's
survivable — *if* every claim is phrased as "designed to satisfy, evidence
here" and never "compliant." The website-layout doc already flags this for
legal review; do that review before any launch, because the first regulated
prospect will run the claims past counsel, and one overclaim discredits the
honest ones. Same for "Managed Cloud" in the pricing table: if it isn't real
today, cut it from public pages — vaporware in a trust-based sale is a
self-inflicted wound.

### 3. The star-count trap

Roundups literally rank by stars; MemPalace has ~54k, mem0 ~47k. Lians will
not win that axis in any timeframe that matters, and chasing it burns the
window. The counter is the plan's own thesis: **change the axis.** The eval
and the demo are ranking-independent artifacts — a roundup can cite "the only
system scoring 5/5 on compliance invariants" without caring about stars. But
this only works if the artifacts are *seen*: an unlaunched benchmark defines
no axis. (Also: 1 star looks worse than 0 in a pitch. Get it to ~30+ via
honest channels — personal network, the integrations' upstream communities —
before pitching roundup authors who will click through.)

### 4. Test-suite hygiene will be checked

The first thing a technical skeptic does after an HN launch is clone and run
the tests. Known order-dependent flakiness in the suite (from the July 1 limit
testing) must be fixed or quarantined first — "their own tests fail" is a
comment that writes itself.

### 5. Buyer reachability is still the business risk, and no artifact fixes it

Everything above is air cover. The plan's honest kill signal (two quarters,
zero paid pilots → stop) is the discipline that matters. Watch for the
seductive failure mode: HN front page, 800 stars, three roundup inclusions,
zero dollars. That outcome *feels* like traction and proves nothing about the
thesis that regulated buyers will pay. The only metric that validates the
business is a signed pilot. Corollary: the 30-min/day outreach block in the
plan is the highest-value line item in the whole document, and it's the one
most likely to be skipped for another feature. Design partners come from the
founder's network at this stage, not from content.

### 6. Solo-founder surface area

Five SDKs, six integrations, an eval harness, a demo repo, a docs site, plus
outreach — maintained by one person (plus Codex co-editing marketing docs;
coordinate to avoid drift between the two agents' claims). Every public
artifact is a maintenance promise. Prefer deepening the two flagships over
adding a sixth SDK; an unmaintained integration in someone's roundup matrix
is negative signal.

## What was finished today (2026-07-03)

| GTM item | Artifact |
|---|---|
| Build B — lookahead demo | `demo/lookahead-bias/` (data, runner, receipts, chart, writeup) |
| §1B distribution kit | `docs/gtm/lookahead-distribution-kit.md` (HN/Reddit/LinkedIn/cold email + rebuttals) |
| §2.1 alternatives pages | `docs/alternatives-zep-regulated.md`, `docs/alternatives-mem0-self-hosted.md` |
| §3 regulatory pieces #2, #3 | `docs/gdpr-crypto-shredding.md`, `docs/point-in-time-vs-validity-windows.md` (#1 = existing `worm-storage.md`) |
| §2.2 roundup outreach | `docs/gtm/roundup-outreach.md` (11 targets + 3 awesome lists + pitch) |

## Recommended sequence (next 10 days)

1. **Day 1–2:** Fix test flakiness; legal-pass the compliance claims; strip or
   soften "Managed Cloud"; extract `demo/lookahead-bias` to a standalone
   public repo.
2. **Day 2–4:** Run Mem0 OSS + Graphiti live in the eval; email all scored
   vendors the methodology with right-of-reply; publish the methodology post.
3. **Day 5:** Launch the lookahead demo (HN + r/algotrading + LinkedIn per the
   kit). This is the sharper, less attackable artifact — it leads.
4. **Day 5 onward:** 30 min/day design-partner outreach with the demo. Start
   with warm NYC fintech network. Log every conversation.
5. **Day 8–10:** Awesome-list PRs, AlternativeTo listing, roundup pitches
   (wave 1: Particula, Vectorize, Developers Digest — the ones that test).
6. **Week 3:** Scores post (after right-of-reply window), comparison pages go
   live on the site, regulatory pieces on the publishing cadence.

The plan's 27-day schedule assumed the builds didn't exist. They mostly do.
The revised bet: two launches and 40 outreach conversations in the next 30
days, and let the kill-signal clock start honestly.
