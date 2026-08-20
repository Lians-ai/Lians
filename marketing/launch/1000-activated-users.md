# Path to 1,000 activated users

Target date: 2026-12-31.

An activated user is not a download, visitor, or GitHub star. For the current
local product, activation means:

1. at least one supported AI client is connected; and
2. saved context is successfully reused in a later task.

Fresh-session completion remains self-reported until a client supplies a
trustworthy content-free session boundary. See [the activation measurement
boundary](ACTIVATION.md).

## Funnel hypothesis

From 2026-08-19 there are about 19 weeks to the target date. The operating target
is 53 new activated users per week.

| Stage | Target | Assumed conversion |
|---|---:|---:|
| Qualified GitHub visitors | 12,500 | - |
| Installs | 2,500 | 20% visitor to install |
| Activated users | 1,000 | 40% install to activation |

These conversion rates are hypotheses. Replace them weekly with observed cohort
rates; do not preserve the visitor goal if actual conversion proves materially
different.

## Channel accountability

| Channel | Activation target | Weekly average |
|---|---:|---:|
| Search guides and ContinuityBench | 300 | 16 |
| Integration directories and partners | 250 | 13 |
| Small coding creators | 200 | 11 |
| Founder-led communities and design partners | 150 | 8 |
| Team pilots and referrals | 100 | 5 |
| **Total** | **1,000** | **53** |

## Sequence

### 0–100: prove the handoff

- Recruit developers who switch between at least two AI coding tools weekly.
- Pair on installation and observe the complete two-task handoff.
- Record the first failed step and time to first successful reuse.
- Interview both retained and inactive users after seven days.

Do not scale paid distribution while fewer than 40% of assisted installs reach a
successful context reuse.

### 100–300: capture high-intent demand

- Publish fair answers for searches such as “share Claude memory with Codex” and
  “keep Cursor project context current.”
- Launch the ContinuityBench contract and invite methodology corrections.
- Post reproducible two-task proofs to relevant communities without disguising
  promotion as an independent recommendation.
- Keep the GitHub README as the canonical destination until the website is ready.

### 300–700: multiply proven creative

- Start with five small coding creators whose audiences already use the supported
  tools.
- Give each creator the same synthetic before/after proof and claims boundary.
- Attribute qualified visits, installs, activations, and day-seven activity.
- Continue only when activated-user economics work; views alone are not success.

### 700–1,000: use team and integration loops

- Convert retained individual users into small team pilots.
- Ask adapter maintainers and integration directories to link the tested path.
- Let activated users explicitly generate a privacy-safe `lians share-card`.
- Add teammate invitations only when shared-project permissions and deletion are
  ready for the promised use.

## Weekly review

Track by cohort and channel:

- qualified repository visitors;
- install starts and completions;
- connected clients;
- successful context reuse;
- activation conversion;
- median time to activation;
- day-seven and day-30 activity;
- support requests per activated user; and
- paid cost per activated and retained user.

Public counts must keep downloads, stars, self-reported activations, and measured
opt-in activations separate.

## Decision rules

- **Install → activation below 40%:** stop increasing traffic and repair setup.
- **Day-seven activity below 25%:** interview the cohort and change the recurring
  workflow before adding features.
- **Creator views without activations:** stop or rewrite the demonstration.
- **Most retained users stay in one tool:** test whether verified current-state
  control is stronger than the cross-tool wedge.
- **Native products match cross-vendor continuity:** monetize team governance,
  provenance, portability, and policy - not storage.

For the next product cycle, allocate roughly 60% of effort to activation and
distribution, 25% to adapter reliability, and 15% to new capability.
