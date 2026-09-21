# Lians full-replacement product contract

## The product

Lians is the local-first governance layer for a person or small team's AI
workforce. The user states one durable mission and one definition of done.
Lians chooses the least expensive capable agent for each stage, transports only
the context that stage needs, enforces the user's privacy and risk policy, and
does not call the work complete without evidence.

Lians does not replace the underlying models, IDEs, or coding agents. It
replaces the manual supervision between them: repeated explanations, choosing
which subscription to use, carrying corrections across tools, checking the
result, and reconstructing what happened.

## Who it is for first

The first buyer is an AI-heavy individual or a small technical team already
using more than one model or agent. This is intentionally not a mass-consumer
launch. The consumer multi-subscription segment is real but small; multi-tool
use is substantially more common inside companies.

The initial job to be done is:

> Give Lians the outcome once. It keeps the goal intact, assigns work across
> the AI tools you already have, protects scarce premium usage, and produces a
> receipt showing whether the result actually passed.

## The governance loop

```text
mission contract
  -> detect available agents
  -> apply cost, privacy, latency, and risk policy
  -> plan stages and capability requirements
  -> route through official local CLIs or APIs
  -> verify the result
  -> escalate only when evidence or risk requires it
  -> store the receipt, correction, and validity window
```

The model is replaceable. The mission, constraints, corrections, and proof
belong to the user.

## Configuration boundary

On first run, Lians should:

1. Detect installed agent CLIs without opening them or reading credentials.
2. Ask the user to confirm which plans or usage windows they want Lians to
   protect. Lians must not scrape consumer subscription pages or infer billing.
3. Capture a simple policy: cheapest acceptable route, local-only data classes,
   tasks that require approval, and maximum premium calls.
4. Enable only connectors that use an official CLI or API and that can produce
   an auditable run receipt.

Account pooling, quota evasion, browser automation against consumer chat
products, and false claims of quota extension are outside the product.

## Competitive boundary

| Product class | What it owns | What Lians must own instead |
|---|---|---|
| Codex and Devin | Agent execution and delegated coding sessions | Cross-agent mission continuity, policy, and evidence |
| Cursor | The editor, code context, and in-editor agent workflow | Governance across editors and agents |
| Memorable and memory stores | Recall of procedures, facts, or graph context | What is valid now, which correction applies, and whether the outcome passed |
| Evaluation platforms | Experiments, datasets, and model comparisons | The live per-mission decision to route, verify, escalate, or block |

This is a product bet, not a proven market claim. It wins only if users trust
Lians with the mission more often than they manually choose an AI tool.

## Build order

### Live in 0.5

- Protect, Balanced, and Maximum routing policies.
- Codex execution through the user's local authenticated CLI.
- Verification-gated PASS or BLOCK results.
- Premium-call ceilings and hash-bound local receipts.
- Read-only local detection of Codex, Claude Code, Cursor Agent, and Gemini CLI.
- Honest connector labels: only Codex is routable today.
- Hash-linked mission revisions with constraints, definitions of done, `as_of`,
  and optional expiry.
- Automatic, user-visible proof-command discovery for common repository types.
- Aggregate local usage telemetry derived only from completed receipts.

### Next proof slice

- A matched pilot measuring Protect against Maximum on the same work.
- A correction capture that turns reviewer changes into expiring tests.
- One externally verified connector after Codex, selected from actual demand.

### Then

- Versioned context and corrections with validity windows.
- Reviewer diffs that become expiring regression tests.
- Official Claude Code, Cursor Agent, and Gemini CLI adapters, one at a time.
- Diff review, approval gates, worktree isolation, and team policy.

## Proof metrics

Lians is working only if it improves these numbers on real repeated use:

- verification pass rate is no worse than Maximum mode;
- premium-model calls per verified task decrease;
- repeated explanations per mission decrease;
- known requirement violations and repeated corrected mistakes decrease;
- users return and delegate a second real task without the author present.

## Rejection gates

Stop or narrow the product if any of these are true after a real pilot:

- users prefer choosing the model themselves after five tasks;
- Protect materially lowers verified task quality;
- the mission contract does not reduce re-explanation;
- official connectors cannot expose enough evidence to govern honestly; or
- users value the receipt but will not pay for the routing and continuity loop.

Research, benchmarks, and standards come only after this loop earns repeated
use. Product first, then measurement, paper, and protocol.
