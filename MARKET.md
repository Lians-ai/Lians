# MARKET.md — Act-Two: The Regulated AI Agent Memory Market

> This document sizes the addressable market for AgentMem and explains why the
> expansion from systematic funds to the broader regulated AI agent universe is
> sequential, defensible, and large enough for a venture-scale outcome.

---

## The one-sentence thesis

We are building the **correctness-and-compliance layer for AI agents operating on
time-sensitive, audited, regulated data** — a requirement that exists wherever an
agent accumulates facts over time and where those facts are wrong, stale, or
unauditable by default.

---

## Rung 1 — Systematic funds / prop shops

**Who:** ~500–1,000 systematic hedge funds, prop trading shops, and quantitative asset
managers globally. AUM range: $100M–$500B+.

**The pain:** AI agents ingesting market data, earnings revisions, rate decisions, and
analyst reports need provably-current, point-in-time-correct memory. Stale facts
contaminate signals. Unauditable memory fails compliance review.

**Why they buy vs. build:** The correctness layer (bitemporal store, supersession engine,
audit reconstruction) is exactly what senior quant engineers don't want to own. It's
boring to build, dangerous to get wrong, and never the fund's core IP.

**Market sizing:**
- ~800 systematic funds spending $2–10M/year on AI infrastructure
- Memory layer as ~5% of that spend = **$80–400M ARR addressable at Rung 1**
- Entry ACV: $50K–$250K/year per fund (API + compliance tier)

**Proof point required:** Reproducible benchmark on real, messy financial data showing
supersession accuracy, point-in-time correctness, and audit completeness.

---

## Rung 2 — Broader buy-side and sell-side

**Who:** Asset managers (BlackRock, Vanguard, Fidelity), banks (prime brokerage, trading
desks), insurance companies with investment arms, fintech lenders with AI underwriting.

**The pain:** Same correctness requirements as quants; larger compliance burden (SEC,
FINRA, MiFID II, Basel III); larger budgets; slower sales cycles.

**Why this is the venture-scale rung:**
- 500+ major buy-side firms, 50+ global systemically important banks
- AI agent spend here dwarfs the quant niche by 10–50x
- Entry gate: "the quant funds trust it" — Rung 1 logos are the entire sales motion

**Market sizing:**
- Global buy-side AI spend: ~$12B (2025), growing 35%/year (Gartner, IDC estimates)
- Compliance-grade memory as ~3–5% of AI spend = **$360M–$600M ARR** in this rung alone
- Target ACV: $200K–$2M/year per enterprise (volume + compliance tier + SLA)

**The expansion path is low-friction:** same core engine, new finance adapters for
institutional data schemas (Bloomberg, FactSet, ADV filings). No rewrite.

---

## Rung 3 — Other regulated, audit-heavy industries

The bitemporal memory primitives (point-in-time correctness, audit reconstruction,
crypto-shred erasure) are not finance-specific. They are **requirements of any domain
where an AI agent accumulates facts that can change, be revised, or be legally erased.**

### Legal / e-discovery

- AI agents analyzing case documents need to know "what did the agent know about
  this matter before the privilege cutoff date?"
- Audit reconstruction is a product, not an afterthought
- Information barriers (Chinese walls) are a legal requirement, not an IT feature
- Market: ~$7B global e-discovery software market; AI agent memory is nascent

### Healthcare / clinical AI

- "What did the triage agent know about this patient at 3am?" is a clinical and
  legal question, not a data engineering question
- HIPAA requires audit trails that survive data correction and erasure
- Point-in-time correctness is relevant for clinical trials (data lock dates,
  protocol amendments, regulatory submissions)
- Market: ~$45B AI in healthcare (2025); auditable agent memory is a new category

### Government / defense

- Classified AI systems require auditable, compartmentalized memory
- FedRAMP/IL5 compliance analog to the GDPR/MiFID II requirements AgentMem already
  satisfies
- Long procurement cycles but non-competitive once qualified

**Rung 3 sizing: $500M–$2B+ ARR** at mature penetration (5+ years). The domain adapter
model means each new vertical is a new adapter, not a new product.

---

## The "compliant memory standard" play (Rung 4)

Regulation around AI agents is trending toward explainability and auditability
requirements (EU AI Act Article 13, emerging SEC guidance on AI in investment
decisions, proposed NIST AI RMF implementation requirements).

If "auditable, erasable, point-in-time-correct agent memory" becomes a **regulatory
requirement category** (the way SOC2 is for SaaS or PCI DSS is for payment
processing), AgentMem is positioned as the default because:

1. We started at the strictest end (systematic finance)
2. We have audit reconstruction, crypto-shred, and hash chains already in production
3. We have the vocabulary regulators will use (bitemporal, erasure certificate,
   information barrier)

**This is not a linear TAM expansion — it's a category creation.** If this scenario
plays out, the market is larger than any Rung analysis can capture.

---

## Why the venture math works

| Rung | ARR Potential | Timeline | Key Unlock |
|------|--------------|----------|------------|
| 1 — Systematic funds | $80–400M | 0–18 months | Supersession benchmark on real data + 2 logos |
| 2 — Broader buy-side | $360M–$600M+ | 12–36 months | Rung 1 reference logos transfer |
| 3 — Adjacent regulated | $500M–$2B+ | 36–60 months | Adapter model; same engine |
| 4 — Compliant memory standard | Market-defining | 5+ years | Regulatory tailwind |

A Series A at Rung 1 proof with a credible Rung 2 path is a standard venture
narrative. The Rung 3 and Rung 4 story is what makes the TAM defensible to a
growth-stage investor.

**The discipline:** win Rung 1 before raising on Rung 2. Every premature expansion
dilutes the proof. The only thing that makes this story work is having the quant
logos that prove the hardest customer trusts the engine.

---

## Competitive moat summary

| Dimension | Mem0 | Zep | In-house build | AgentMem |
|-----------|------|-----|---------------|----------|
| Bitemporal correctness | ✗ | ✗ | Approximate | ✓ |
| Point-in-time recall | ✗ | ✗ | Rarely built | ✓ |
| Audit reconstruction | ✗ | ✗ | "We'll add logging" | ✓ |
| GDPR crypto-shred + audit | ✗ | ✗ | Contradictory | ✓ |
| Supersession on real data | ✗ | Partial | Unsolved | ✓ |
| Information barriers | ✗ | ✗ | Manual, leaky | ✓ |
| Backtest contamination | ✗ | ✗ | Not on roadmap | ✓ |

The in-house build is the real competitor. This moat is about making their build
look expensive, risky, and permanently behind — not about features vs. features.
