# SCALE.md — Beating the In-House Build and Expanding Beyond the Niche

> Working strategy doc. The goal is twofold and in tension: (1) be **better than what a quant fund builds for itself**, and (2) build the thing so the **same engine fans out** to adjacent finance, then to other regulated industries, without a rewrite. This doc treats both as one design problem, because the decisions that win against build-vs-buy are the same ones that determine whether you can generalize later.

---

## 0. The one-sentence thesis to keep honest

We are not "memory for AI." We are the **correctness-and-compliance layer for AI agents that operate on time-sensitive, audited, regulated data** — landing first where the requirement is hardest (systematic funds) because winning the hardest customer is the proof we can win every easier one.

If a sentence in this doc doesn't serve that thesis, cut it.

---

## 1. Why a quant fund's in-house build is your real competitor (not Mem0/Zep)

The objection that kills this company is **"we'll just build it."** Quants build infra by default, have the talent, and distrust outside dependencies on the critical path. So the entire product strategy is organized around making their build look expensive, risky, and incomplete — not around beating a horizontal startup on features.

A fund's weekend/quarter build will get them:
- A vector store + recency heuristic. "Good enough" for a demo.
- It will **not** get them: provable point-in-time correctness across late-arriving and revised data, audit reconstruction that survives a regulator's question, GDPR-style erasure that doesn't break the audit trail, or correct supersession on messy real data.

Those four are the things that are *boring to build, dangerous to get wrong, and never the fund's core IP.* That's your wedge. You win build-vs-buy not by being flashier but by owning the unglamorous-but-catastrophic-if-wrong layer they don't want to own.

### The build-vs-buy scorecard (your product must win every row)

| Dimension | In-house build | You must be |
|---|---|---|
| Point-in-time correctness | Approximate, ad hoc | Provably correct, tested against named invariants |
| Late-arriving / revised data | Usually ignored → silent contamination | First-class; bitemporal by construction |
| Audit reconstruction | "We'll add logging later" | One query reconstructs agent's knowledge at any T |
| GDPR / erasure | Contradicts their audit log | Crypto-shredding: erase content, keep audit |
| Supersession on real data | Unsolved, hand-rolled | Solved + benchmarked (this is your open problem — solve it) |
| Maintenance burden | Their senior engineers forever | Your problem, not theirs |
| Time-to-trust | Months of their own validation | Ships with proofs + test suite |

**Rule:** if a row is ever "tie," it's a loss, because tie goes to the incumbent (themselves).

---

## 2. The non-negotiable: solve supersession-on-real-data

Your own self-audit flagged this as unsolved. It is the single most important technical bet in the company, for two reasons:

1. It's the row above most likely to become a "tie." If a fund's naive approach handles supersession "well enough," your wedge thins.
2. It is the **most generalizable** capability — every downstream industry (legal, healthcare, compliance) has the same "which fact supersedes which, as of when, given conflicting/revised sources" problem.

**Action:** treat supersession as the flagship. Get it working on a real fund's messy data (not synthetic), benchmark it, and make the benchmark public. A reproducible benchmark on real-world supersession is both your strongest sales artifact *and* your strongest YC signal — it proves the hard part is actually hard and you actually solved it.

---

## 3. Architecture decisions that simultaneously win the niche AND enable expansion

The trap is building something so quant-specific it can't move. The discipline: **keep the core domain-agnostic, push all finance-specific logic to the edge.**

```
┌─────────────────────────────────────────────┐
│  DOMAIN ADAPTERS (swappable, per-vertical)  │  ← finance today, legal/health later
│  - market-data semantics, ADV ingestion     │
│  - corporate-actions / revision rules        │
├─────────────────────────────────────────────┤
│  CORRECTNESS + COMPLIANCE CORE (universal)  │  ← THE moat, never vertical-specific
│  - bitemporal store (valid-time + tx-time)   │
│  - supersession engine                       │
│  - audit reconstruction                      │
│  - crypto-shred erasure                       │
├─────────────────────────────────────────────┤
│  STORAGE PRIMITIVES                          │
│  - immutable audit layer                     │
│  - encrypted erasable content store          │
└─────────────────────────────────────────────┘
```

**The rule that protects scalability:** nothing finance-specific is ever allowed into the core. The moment "valid-time" means "trade timestamp" inside the engine instead of "an abstract valid-time," you've built finance software you can't reuse. The core knows about *time, revision, supersession, audit, erasure* — never about *markets.* Markets live in an adapter.

This is the decision that lets the same engine later serve a hospital reconstructing "what did the triage agent know about this patient at 3am" or a law firm reconstructing "what did the agent know about this matter before the privilege cutoff." Same primitives, new adapter.

---

## 4. Better-than-in-house: the features that compound

Beating their build is necessary but not sufficient; you need features that get *more* valuable the longer they use you and that they'd never build:

- **Backtest-contamination detection.** Flag when an agent's memory contains data it couldn't have known at the simulated timestamp. Quants viscerally fear lookahead bias; this speaks their language and is impossible to get from a vector store.
- **Audit reconstruction as a product surface**, not a log. "Show me the agent's complete knowledge state as of 2025-03-14T09:30." One call. This is the compliance demo that closes the deal.
- **Erasure that proves itself.** A verifiable certificate that content X is cryptographically unrecoverable while the audit trail remains intact. Compliance officers buy proofs, not promises.
- **Drift / supersession observability.** Show *why* fact B replaced fact A and when. Turns a black box into something a risk committee will approve.

Each of these is (a) painful for a fund to build, (b) directly tied to a fear they already have, and (c) fully domain-agnostic, so it ships to every future vertical for free.

---

## 5. The expansion ladder (how the niche becomes a horizon)

Investors fund the horizon, so the path has to be explicit and sequential. **Do not skip rungs.** Each rung's credibility is borrowed from the one below it.

**Rung 1 — Systematic funds / prop shops (now).**
Hardest correctness bar, smallest budgets, fastest to move. Purpose: prove the engine on the most demanding customer. Outcome: reference logos + the supersession benchmark. *This rung is marketing for every rung above it.*

**Rung 2 — Broader buy-side + sell-side (asset managers, banks, insurers).**
Same correctness-and-compliance requirement, far bigger budgets, slower cycles. The pitch writes itself: "the layer the quant funds trust." Reuse: core unchanged, new finance adapters. This is where venture-scale revenue actually lives.

**Rung 3 — Other regulated, audit-heavy industries (legal, healthcare, gov).**
Identical primitives: point-in-time correctness, audit reconstruction, compliant erasure. New domain adapter, new compliance mapping (HIPAA instead of GDPR, etc.), same engine. This is the "beyond the industry" expansion — and it's only credible *because* you survived finance first.

**Rung 4 — Horizontal "compliant memory" standard.**
If regulation around AI agents tightens (it's trending that way), "auditable, erasable, point-in-time-correct agent memory" becomes a *requirement* category, not a feature. You're positioned to be the default because you started at the strict end. This is the act-two that makes the $100M+ story defensible.

**The discipline:** never jump to Rung 3 to chase TAM before Rung 1's proof exists. The whole strategy is "we earned the right to expand by winning the hardest customer." Skipping rungs forfeits that.

---

## 6. The open-source question, resolved

Do **not** open-source the core — that arms Mem0/Zep with exactly the bitemporal correctness they can't currently retrofit, and it's your moat.

**Do** open-source one thin, genuinely useful primitive — a point-in-time-correctness checker or backtest-contamination detector. Purpose: credibility + self-identifying lead-gen. The quant engineer who stars it or files an issue is your next design partner. The engine, audit reconstruction, erasure, and supersession stay closed and paid.

---

## 7. Risks that bound the upside — and the kill criteria

Be honest about what would make this *not* venture-scale, and decide in advance what evidence would make you pivot.

- **"Feature, not a product."** If supersession + bitemporality turn out narrow enough that an incumbent absorbs them as a checkbox, the standalone company thins. *Kill/pivot signal:* a fund's in-house build reaches "tie" on the scorecard in under a quarter.
- **Build-vs-buy never breaks your way.** *Signal:* design partners validate the pain but still insist on building in-house after seeing your proofs. Means the maintenance-burden argument isn't landing — re-tool the pitch around their senior-engineer opportunity cost.
- **Niche caps you and Rung 2 stalls.** *Signal:* you win quant funds but asset managers/banks don't treat the quant logos as proof. Means the "hardest customer" narrative isn't transferring — fix positioning before raising on the horizon.
- **Sales cycle vs. burn.** Secretive, slow, compliance-heavy buyers + small initial contracts is the worst combo for the early growth curve. *Mitigation:* design-partner-first, land-and-expand pricing, and a thin paid tier that shortens time-to-first-dollar.

---

## 8. The 90-day focus (what actually moves the needle)

1. **Solve and benchmark supersession on one real fund's data.** Everything else is downstream of this. Make the benchmark reproducible and public.
2. **Land 2 design partners** who validate the audit/point-in-time pain — Rung 1 proof.
3. **Ship the audit-reconstruction demo** ("knowledge state as of T" in one call). This is the thing that closes compliance-minded buyers.
4. **Enforce the core/adapter boundary in code.** Every finance-specific line that leaks into the core is future expansion you're destroying. Audit the codebase for leaks now, while it's small.
5. **Open-source the thin primitive** and use issues/stars as a design-partner funnel.
6. **Write the act-two market sizing** (regulated AI agents) so the horizon is on paper, not implied.

---

## 9. The single test for every decision

> Does this make us **harder to replace by an in-house build** *and* keep the core **domain-agnostic enough to expand**?

If yes to both, do it. If it wins the niche but locks you into finance, you've traded the horizon for the beachhead. If it generalizes but ties on the build-vs-buy scorecard, you've built something nobody needs to buy. The whole company lives in the intersection.
