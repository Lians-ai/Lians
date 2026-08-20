# Point-in-Time vs. Validity Windows: What "Temporal Memory" Actually Means

*July 2026 · Lians engineering. An attempt to make the category's vocabulary
precise - because "temporal," "bitemporal," and "point-in-time" are being used
interchangeably for models with very different guarantees.*

**TL;DR:** A validity window (`valid_at`/`invalid_at` on a fact) tracks **one**
time axis: when the fact was true in the world. Full bitemporality tracks
**two**: when the fact was true (*valid time* / event time) **and** when the
system knew it (*transaction time* / knowledge time). The second axis is what
answers "what did the agent know at time T?" - the question auditors and
backtests actually ask. One interval cannot answer it, no matter how carefully
it is maintained.

## Three questions that sound identical and aren't

Take one fact with a real-world history: a company's FY guidance was $32B,
raised to $36B on May 10, and the raise was recorded in your system on May 12
(the ingestion pipeline ran late).

1. **"What is the guidance?"** → $36B. Any store answers this.
2. **"What was the guidance on May 11?"** → $36B - *the world* had changed on
   May 10. A validity-window model answers this correctly: the $32B edge was
   invalidated as of May 10.
3. **"What did our system know on May 11?"** → **$32B.** The raise wasn't in
   the system until May 12. A validity-window model gets this wrong - it
   projects today's knowledge back onto May 11.

Question 3 is the compliance question ("why did the model trade on May 11?"),
the backtest question ("no information the system didn't have yet"), and the
reproducibility question ("re-run the decision exactly as it happened"). It is
unanswerable with one time axis, because the model has already overwritten
*when it learned* with *what turned out to be true*.

## The two axes, precisely

| Axis | Other names | The question it answers | In Lians |
|---|---|---|---|
| **Valid time** | event time, business time | When was this true in the world? | `event_time` |
| **Transaction time** | knowledge time, system time, ingestion time | When did the system know it? | `ingestion_time`, `valid_from`/`valid_to` |

A **bitemporal** store keeps both, immutably: an update doesn't modify the old
fact, it *closes* it (sets `valid_to`) and inserts the new version. Every state
the system has ever been in remains reconstructible.

The canonical hard case is the **late revision** (restated earnings, corrected
lab result, amended filing): the *event* is old, but the *knowledge* is new.

- Validity window: the corrected value's validity starts at the (old) event
  date → an as-of query at any date after the event returns the corrected
  value - **including dates when nobody knew it yet.** In a backtest this is
  silent lookahead; to an examiner it misrepresents what the system knew.
- Bitemporal: the corrected value carries old `event_time`, new
  `valid_from` → as-of queries return it only from the moment it was actually
  known.

This isn't hypothetical: financial data vendors solved it decades ago
(point-in-time fundamentals databases exist precisely because backtests against
restated data are fiction). Agent memory is re-learning the lesson.

## Where current systems land

*As of July 2026; from public docs. Corrections welcome.*

- **Zep / Graphiti** - validity windows on graph edges (`valid_at` /
  `invalid_at`, LLM-maintained). Genuinely temporal (question 2), and its edge
  invalidation is real engineering. It does not model knowledge time as a
  queryable axis, so question 3 - and late revisions - are out of scope.
- **mem0** - append-only fact versions; no as-of query on either axis.
- **Letta / agentic memory** - the agent edits its own memory blocks; history
  is whatever the agent kept. No time-axis queries.
- **Vector stores** - a similarity index has no time axes at all; timestamp
  metadata filtering is question-2-only, and only if every consumer remembers
  to apply it.
- **Lians** - both axes on every fact and every graph edge: `event_time` +
  `valid_from`/`valid_to` + `ingestion_time`, with `as_of` on `recall`,
  `snapshot`, graph `path`/`neighbors`, and audit reconstruction.

To be fair in the other direction: if your workload never asks question 3 -
consumer personalization, stateless assistants - a validity-window or even
ADD-only model is simpler and enough. The two-axis machinery earns its
complexity when someone (an examiner, a backtest, a court) will interrogate
*what the system knew*.

## The test you can run

The distinction is empirical, not philosophical. The two-line experiment:

1. Write fact F1 (event May 1). Later, supersede it with F2 (event May 10,
   ingested May 12).
2. Query as-of **May 11**.

Full bitemporality returns F1. A validity-window model returns F2. An ADD-only
model returns both, ranked by embedding luck. The
[regulated-memory eval](regulated-eval-results.md) runs exactly this class of
probe ("point-in-time recall" and "stale revision suppressed" invariants), and
the [lookahead-bias demo](../demo/lookahead-bias/README.md) shows the dollar
cost of getting it wrong: the same strategy scores Sharpe 4.6 against a
one-axis view of history and −0.6 against the honest two-axis view.

```python
# Lians: the honest answer is one parameter
mem.recall_at(agent_id="desk", query="FY guidance",
              as_of=datetime(2026, 5, 11, tzinfo=timezone.utc))
# -> $32B  (F2 existed in the world, but not in the system, on May 11)
```

## Glossary for the category

- **Temporal memory** - any system that stores time with facts. Weakest claim.
- **Validity windows** - one axis (world time) as an interval per fact/edge.
  Answers "what was true at T."
- **Point-in-time / as-of correctness** - answers "what was *known* at T."
  Requires the second axis.
- **Bitemporal** - both axes, immutably versioned. Point-in-time correctness
  falls out of it; so do audit reconstruction and honest backtests.

## Related

- [Lians vs Zep/Graphiti - full technical comparison](compare-zep.md)
- [Your agent's memory is contaminating your backtest (reproducible demo)](../demo/lookahead-bias/README.md)
- [GDPR crypto-shredding: the erasure half of the compliance story](gdpr-crypto-shredding.md)
