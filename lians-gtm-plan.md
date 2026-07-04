# Lians — Compliance-Grade Memory: Positioning, Marketing & Build Plan

**Version:** 1.0 · July 2026
**Owner:** E
**Window:** 27 days of heavy build capacity, then ongoing maintenance cadence

---

## 0. The Thesis (read this when you lose the plot)

The agent-memory category is real, crowded, and settled around a known cast: Mem0 (largest ecosystem, ~47k stars, 21 framework integrations), Zep/Graphiti (owns "temporal" in the public mind), Letta, Cognee, LangMem, Cloudflare Agent Memory, plus fast risers (MemPalace ~54k stars, Evermind, Hindsight).

**What is NOT ownable:** "bitemporal memory" as a headline. Zep already occupies temporal positioning with Graphiti's validity-window model.

**What IS ownable — the white space:**

1. **The compliance bundle.** No player in any public comparison is scored on tamper-evident audit trails, cryptographic right-to-erasure, point-in-time reconstruction for auditors, or data residency. The category benchmarks recall and latency; nobody benchmarks "can this survive a regulator."
2. **Full openness for regulated deployment.** Zep retreated from open self-hosting (Graphiti open, platform SaaS). Mem0 gates graph features behind its $249/mo Pro cloud tier. For regulated builds, "data never leaves our infra" is a hard requirement — only fully-open systems clear that bar. The leaders voluntarily walked away from this position.

**The position, in one sentence:**

> Lians is the fully open, self-hosted, compliance-grade memory layer — bitemporal correctness, tamper-evident audit trails, and cryptographic erasure — for AI agents in regulated industries.

Against Zep: *"They timestamp facts. We survive examiners. And you can self-host all of it."*
Against Mem0: *"The features regulated teams need aren't behind our paywall — or anyone's cloud."*

**The GTM reality:** discovery in this category happens through comparison roundups and benchmark artifacts. Lians currently appears in zero of them. The entire marketing strategy below is about (a) entering the comparison set and (b) defining a new axis of comparison that Lians wins by construction.

---

## 1. The Two Flagship Builds

These are the engineering-heavy artifacts that do double duty as marketing AND design-partner ammunition. Everything else supports them.

### Build A — The Compliance Readiness Benchmark ("Can Your Memory Layer Survive a Regulator?")

**What it is:** An open-source evaluation suite + published report that scores agent memory systems (Mem0 OSS, Zep/Graphiti, Letta, Cognee, Lians) on compliance-critical capabilities that no existing benchmark measures.

**Why it wins:** LoCoMo and LongMemEval are the category's contested territory — everyone fights over recall scores and disputes each other's numbers. Nobody has published the compliance axis. Whoever defines the axis owns it. This is category creation through evaluation.

**The evaluation dimensions (the "CRAB" score — Compliance Readiness of Agent memory Benchmarks, or name TBD):**

1. **Right-to-erasure (GDPR Art. 17)**
   - Can a specific data subject's memories be deleted — actually deleted?
   - Is deletion cryptographic (key destruction) or logical (tombstone/flag)?
   - Do embeddings, graph edges, and derived facts get purged, or do ghosts remain?
   - Test: ingest synthetic PII, issue erasure, then attempt recovery via retrieval, raw store inspection, and embedding-neighborhood probing.

2. **Audit-trail integrity (SEC 17a-4 / tamper evidence)**
   - Is every read/write/supersession logged?
   - Is the log tamper-evident (hash-chained / WORM-compatible) or just an editable table?
   - Test: mutate a record out-of-band; does the system detect and surface the tamper?

3. **Point-in-time reconstruction (bitemporal correctness)**
   - Can you answer "what did the agent know at time T?" — not "what is true about time T?"
   - Distinguish valid-time vs transaction-time. (This is where Zep's model gets stress-tested honestly: validity windows ≠ full bitemporality.)
   - Test: write fact F1 at t1, supersede with F2 at t2, query as-of t1.5 — do you get F1, F2, or a blend?

4. **Data residency / self-host completeness**
   - What fraction of the marketed feature set actually runs fully self-hosted, offline, with zero calls out?
   - Document the gap between the README and the paywall for each system.

5. **Provenance & explainability**
   - For any retrieved memory: can you trace it to source conversation, timestamp, and transformation chain?

**Deliverables:**
- `lians-ai/compliance-bench` public repo: harness, synthetic datasets, adapters per system, reproducible Docker setup.
- Scored results table (the money screenshot — this is what gets embedded in every future roundup).
- Long-form report on lians.ai + condensed versions for HN, r/MachineLearning, LinkedIn.
- Methodology honesty rules: publish the harness BEFORE the scores, invite vendors to submit corrections, score Lians with the same knife. Credibility is the entire asset; a rigged-looking benchmark is worse than none.

**Effort estimate:** 8–10 build days (adapters are the grind; Mem0/Letta/Graphiti are pip-installable, scoring logic is the fun part).

---

### Build B — The Lookahead Bias Demo ("Your Agent's Memory Is Contaminating Your Backtest")

**What it is:** A reproducible notebook + writeup demonstrating that a naive memory layer (vector store or non-bitemporal memory) leaks future information into past decisions during backtesting — quietly inflating results — and that point-in-time-correct memory fixes it.

**Why it wins:** Lookahead bias is the one failure mode every quant feels viscerally. Nobody has publicly connected it to agent memory. This piece speaks to your exact wedge buyer in their native language, and it converts cold outreach from "please look at my infra" into "we found a bug class in your stack — here's the proof."

**The demo structure:**

1. **Setup:** A simple LLM-agent trading strategy (e.g., news/sentiment-informed signal on a liquid universe — SPY components or crypto for data availability). Agent consults its memory layer for "what do I know about ticker X?" at each decision step.
2. **The contaminated run:** Memory layer is a standard vector store populated with the full history. During backtest at date D, retrieval surfaces facts learned AFTER D (a later earnings result, a subsequent analyst note, a memory written during a "future" session). Backtest Sharpe looks great.
3. **The honest run:** Same strategy, memory swapped to Lians with as-of queries pinned to the decision timestamp. Performance degrades to realistic.
4. **The receipt:** Side-by-side equity curves + a table of specific contaminated retrievals (memory retrieved at D, provably created at D+k). Make the leak undeniable and screenshot-able.
5. **The kicker:** Show the same as-of machinery doubling as the audit answer — "what did the system know at decision time" is both the backtest fix and the examiner's question.

**Deliverables:**
- Public repo: notebook, pinned deps, cached dataset (no API keys needed to reproduce).
- Blog post on lians.ai: "Lookahead Bias in Agent Memory: How Your Backtest Is Lying to You."
- Distribution kit: HN post, r/algotrading post, LinkedIn thread, 5-slide summary for DMs to quant contacts.
- Cold-outreach email template anchored on the demo (see §3).

**Effort estimate:** 5–7 build days. Biggest risk is spending too long making the strategy "good" — the strategy should be deliberately simple; the leak is the star.

---

## 2. The Comparison-Set Campaign (Priority Zero, Fast-Follow)

Discovery = roundups + "X vs Y" searches. Get in the set.

### 2.1 On-site comparison pages
Bottom-of-funnel intent still clicks even in the AI-Overview era. Build these as honest, spec-level comparisons (the BCS/Billy playbook — aggressive but factual):

- `/compare/lians-vs-zep` — angle: full self-hosting + true bitemporality + compliance bundle vs. validity windows + SaaS platform.
- `/compare/lians-vs-mem0` — angle: nothing gated behind a Pro tier; compliance features vs. personalization features.
- `/alternatives/zep-alternative-regulated` — targets "Zep alternative" + regulated modifier.
- `/alternatives/mem0-alternative-self-hosted` — targets the documented pain of Mem0's graph paywall.
- Each page embeds the compliance-bench scorecard (Build A output) — the pages and the benchmark reinforce each other.

**Honesty rule:** concede what competitors do better (Mem0's ecosystem breadth, Zep's retrieval latency work). Comparison pages that only flatter themselves read as ads and die.

### 2.2 Roundup outreach
- List every "best agent memory 2026" article found in research (vectorize.io, techsy.io, fountaincity, apiscout, evermind's own, Medium comparisons, dev.to roundups). These authors update constantly and need new entrants.
- Pitch: "We published a compliance benchmark scoring the systems you cover — happy to share data/access." Give them content, not a request.
- Target: Lians named in 3+ third-party roundups within 90 days.

### 2.3 GEO (Generative Engine Optimization)
- AI assistants now answer "best memory layer for regulated industries" — the comparison pages, benchmark repo, and regulatory content below are exactly the citable, factual, well-structured sources these systems retrieve. Structure pages with clear claims, tables, and dates.

---

## 3. Regulatory Content Moat

Two-to-three deep pieces that make Lians the pre-existing answer when compliance officers start asking engineering teams the question.

1. **"SEC 17a-4 and AI Agent Memory: What WORM Means for Your Agents"** — maps tamper-evident audit trails to broker-dealer recordkeeping requirements. Nobody has written this. When it becomes a real procurement question, be the citation.
2. **"GDPR Right-to-Erasure for AI Memory: Why Crypto-Shredding Is the Only Real Answer"** — the industry-wide unresolved question; embeddings and derived facts make logical deletion a lie. Directly showcases the Lians erasure model.
3. **"Point-in-Time vs. Validity Windows: What 'Temporal Memory' Actually Means"** — the honest technical piece that differentiates bitemporality from Zep's model without naming-and-shaming; educates the market on the axis Lians wins.

Cadence: one per two weeks after the flagship builds ship. Each cross-links the benchmark and comparison pages.

---

## 4. Ecosystem Table Stakes (Unglamorous, Compounding)

The roundups literally score by stars and integration lists. Check the boxes:

- [ ] **Framework integrations:** LangChain, LlamaIndex, Mastra (TypeScript-first gets you into a distinct list), CrewAI. Each one = an entry in someone's integration matrix + a docs page that ranks.
- [ ] **MCP server for Lians** — fast-growing discovery surface; "add compliant memory to Claude/agents via MCP" is its own announcement post.
- [ ] **README overhaul:** honest benchmark numbers, compliance scorecard, 20-minute Docker self-host quickstart (Mem0's "working local API in under 20 minutes" is the bar).
- [ ] **Run LoCoMo/LongMemEval on Lians and publish whatever the numbers are.** You don't need to win recall — you need to be *present* and credible on the standard axes while winning your own.
- [ ] Submit to AlternativeTo, and the standing awesome-lists for agent memory.

---

## 5. Design-Partner Motion (Where Revenue Actually Comes From)

Marketing above generates air cover; deals come from direct motion. Target: **2–3 paid design partners in regulated finance within two quarters.**

**Who:** quant funds and systematic shops (your network), fintech infra teams under SEC/FINRA scope, RIA-tech and compliance-tech vendors who need memory inside THEIR product (a partner channel — they resell your compliance story).

**The wedge conversations:**
1. "Agent audit trail an examiner will accept" (compliance officer pain)
2. "Point-in-time-correct research memory / no lookahead contamination" (quant researcher pain — Build B is the door-opener)
3. "Right-to-erasure without rebuilding the vector store" (any EU-exposed shop)

**Mechanics:**
- Warm network first — NYC fintech proximity is a real asset; coffee > cold email at this stage.
- Cold outreach template: lead with the lookahead demo, one paragraph, link, ask for 20 minutes. No feature lists.
- Paid pilot structure: fixed-fee 6–8 week pilot, defined success criteria, publishable (anonymized) case study as part of the price.
- **Kill signal (hold yourself to it):** if after two honest quarters of this motion zero regulated firms will pay even a pilot fee, that is data about buyer reachability, not a prompt to add features.

---

## 6. 27-Day Execution Plan

**Week 1 — Build B (Lookahead Demo).**
Smaller, sharper, fastest to ammunition. Days 1–2 strategy scaffold + dataset caching; days 3–4 contaminated vs. honest runs + receipts table; days 5–6 writeup + repo polish; day 7 distribution kit.

**Weeks 2–3 — Build A (Compliance Benchmark).**
Days 8–9 harness + synthetic datasets + methodology doc (publish methodology first); days 10–14 adapters (Mem0 OSS, Graphiti, Letta, Cognee, Lians); days 15–17 scoring runs + results; days 18–19 report + scorecard graphics; day 20 launch (HN + roundup outreach wave 1).

**Week 4 — Comparison set + table stakes.**
Days 21–23 comparison/alternative pages wired to benchmark data; days 24–25 MCP server + first framework integration; day 26 README/quickstart overhaul; day 27 regulatory piece #1 draft + outreach wave 2.

**Parallel throughout:** 30 minutes/day of design-partner outreach once Build B ships (day 8 onward). Do not wait for "everything ready."

---

## 7. Success Metrics (90-Day Horizon)

| Metric | Target | Why it matters |
|---|---|---|
| Third-party roundups naming Lians | 3+ | Existence in the consideration set |
| Compliance-bench repo stars | 500+ | Category-axis credibility |
| Benchmark report: HN front page OR 10k+ reads | 1 launch | Distribution proof |
| Qualified design-partner conversations | 10+ | Pipeline |
| Paid pilots signed | 1–2 | The only metric that pays rent |
| "Zep alternative regulated" / comparison-page organic clicks | trending ≠ 0 | Bottom-funnel capture starting |

---

## 8. Risks & Honest Caveats

- **Benchmark blowback:** vendors will dispute scores (they already dispute each other's). Mitigation: methodology-first publication, reproducible harness, vendor right-of-reply, score yourself with the same knife.
- **Zep responds:** they could bolt on erasure/audit features. Mitigation: full-openness is the part they've structurally abandoned (SaaS pivot); speed + regulated-vertical depth is yours to lose.
- **Buyer reachability remains the core risk** — everything in this doc improves the odds but doesn't remove it. The kill signal in §5 is the discipline.
- **Solo bandwidth:** two flagship builds + campaign in 27 days is aggressive. If forced to cut: Build B ships no matter what (smallest, sharpest, opens doors); Build A can slip a week; comparison pages before integrations.

---

*Next step: pick Build B's strategy scaffold and dataset, and start cutting code.*
