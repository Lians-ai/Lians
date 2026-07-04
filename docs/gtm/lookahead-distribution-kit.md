# Distribution Kit — Lookahead-Bias Demo

Everything needed to launch `demo/lookahead-bias/` (GTM plan §1 Build B).
Post in this order: HN first (weekday, 8–10am ET), Reddit ~2h later, LinkedIn
same afternoon, cold outreach begins the day after (link has social proof by then).

**Before posting:** extract `demo/lookahead-bias/` to a standalone public repo
(`ebeirne/lookahead-bias-demo`) so the link lands on the README with the chart
above the fold. Pin the results PNG. Confirm `python run_demo.py` works from a
fresh `pip install lians-sdk[local]`.

---

## 1. Hacker News (Show HN)

**Title (pick one — A is the recommendation):**

- A: `Show HN: Your agent's memory layer is leaking the future into your backtests`
- B: `Show HN: Reproducing lookahead bias in LLM-agent backtests (and the fix)`

**Text:**

> Quant teams spend enormous effort keeping lookahead bias out of price data —
> point-in-time databases, restatement handling, survivorship filters. Then they
> bolt an LLM agent onto the research stack with a vector-store memory and
> reintroduce the exact bug.
>
> The mechanism: during a backtest at date D, "semantic relevance" retrieval
> happily returns memories *created after D* — tomorrow's earnings note, next
> week's analyst downgrade. The agent trades on information that didn't exist
> yet. Nothing errors. Sharpe looks amazing.
>
> This repo reproduces it end to end with zero API keys: same strategy, same
> data, same memory store, run twice. Present-time retrieval: +44%, Sharpe 4.6.
> Retrieval pinned to decision time (`as_of`): −4%, Sharpe −0.6. The diff
> between the runs is literally one parameter. Every leaked retrieval is logged
> with its creation timestamp — 918 receipts across 124 trading days.
>
> The market is synthetic (seeded, fictional tickers) on purpose: it makes every
> leak provable from the data files and the whole thing reproducible in ~30s on
> CPU. The causal structure it encodes — the note describing an outcome can't
> exist before the outcome — is true of every real market.
>
> The same "what did the system know at time T?" machinery is also the answer
> to a regulator asking why your model traded, which is why we think
> bitemporal memory is infrastructure, not a feature. Happy to answer anything —
> methodology criticism especially welcome.

**Prepared answers for predictable comments:**

- *"Synthetic data is a strawman"* → The leak is a retrieval-layer property, not
  a data property. Swap in real prices and the receipts table doesn't change —
  a note about an outcome still can't predate the outcome. Synthetic makes it
  reproducible without a data vendor agreement.
- *"Just filter by timestamp in your vector store"* → That fixes the easy half
  (`future_event`). It misses `late_revision`: an old event whose corrected
  figure arrived after the checkpoint. You need two time axes (event time vs
  knowledge time) — that's the bitemporal argument, and it's also why
  validity-window models that track one interval per fact don't fully close it.
- *"Nobody backtests LLM agents"* → Every eval on historical replays is a
  backtest: support bots on past tickets, coding agents on old issues (SWE-bench
  date contamination is the same bug class), medical agents on past cases.
- *"This is a vendor ad"* → The harness is Apache 2.0, the naive baseline is our
  own engine with the honest parameter removed, and the receipts format is
  memory-layer-agnostic — run it against your own stack.

## 2. r/algotrading

**Title:** `Lookahead bias has a new home: your LLM agent's memory layer [reproducible demo]`

**Body:**

> If you're experimenting with LLM agents in your research loop, here's a bug
> class worth checking for. Agent memory layers (vector stores, "agent memory"
> SaaS) retrieve by semantic relevance. Relevance has no time axis. So in a
> backtest at date D, retrieval quietly surfaces notes written after D — and
> your agent front-runs its own future knowledge.
>
> I built a minimal reproduction: 6 synthetic tickers, 24 events, one memory
> store, the same dumb keyword strategy run twice. The only difference is
> whether retrieval is pinned to decision time.
>
> - Naive retrieval: +44%, Sharpe 4.6 (fiction)
> - Point-in-time retrieval: −4%, Sharpe −0.6 (the strategy is deliberately dumb — that's the point)
> - Full receipts: 918 retrievals of not-yet-existing notes, each logged with
>   decision timestamp vs note creation timestamp
>
> Runs in ~30s, no API keys, seeded/deterministic: https://github.com/ebeirne/lookahead-bias-demo
>
> The subtle case worth knowing even if you never touch LLMs: `late_revision` —
> the event is old but the *corrected number* arrived later (restatements,
> revised guidance). Single-timestamp filtering can't catch it; you need
> event-time and knowledge-time as separate axes, which is the same
> point-in-time discipline you already apply to fundamentals data.
>
> Methodology criticism welcome — the receipts format is designed so you can
> run the same check against whatever memory stack you use.

(No product pitch in the body; the repo does that. Mods remove ads.)

## 3. LinkedIn thread

**Post 1 (the hook):**

> We found a bug class hiding in AI-agent trading stacks: the memory layer
> leaks the future into backtests.
>
> Same strategy, same data, same memory store, run twice:
> • naive retrieval → +44%, Sharpe 4.6
> • retrieval pinned to decision time → −4%, Sharpe −0.6
>
> The difference is one parameter. The +44% is fiction. [chart]

**Post 2 (the mechanism):**

> Vector stores retrieve by relevance. Relevance has no time axis. At simulated
> date D, "what do I know about this ticker?" returns the earnings note written
> at D+1. The agent buys the day before the announcement — every time. No error,
> no warning. We logged 918 of these retrievals in a 124-day backtest, each with
> its creation timestamp. Receipts in the repo.

**Post 3 (the point):**

> Quant funds solved this for market data decades ago — it's called point-in-time
> data. Agent memory needs the same discipline: bitemporal facts, as-of queries.
> And the same machinery answers the examiner's question — "what did the system
> know when it decided?" Backtest correctness and audit-readiness are the same
> primitive. Reproducible demo (30s, no API keys): https://github.com/ebeirne/lookahead-bias-demo

## 4. Five-slide DM summary (for quant contacts)

1. **Title:** "Your agent's memory is contaminating your backtest." One chart:
   the two equity curves.
2. **Mechanism:** vector retrieval has no time axis → backtest at D retrieves
   notes created at D+k. Diagram: timeline with a leaking arrow.
3. **Receipts:** 3 rows of the receipts table — decision time, retrieved note,
   note timestamp, next-day return.
4. **Fix:** `recall(...)` → `recall_at(..., as_of=decision_time)`. One
   parameter. Bitemporal store underneath (event time ≠ knowledge time —
   catches late revisions too).
5. **Why us:** same as-of machinery = the audit answer. Fully open,
   self-hosted, compliance-grade. Ask: 20 minutes; we'll run `backtest_check`
   against your setup.

## 5. Cold outreach email (GTM plan §5)

**Subject:** `lookahead bias via agent memory — 30s reproduction`

> Hi {name} —
>
> We found a bug class in LLM-agent research stacks: the memory layer leaks
> future information into backtests. Vector retrieval has no time axis, so a
> backtest at date D happily retrieves notes written at D+1. Same strategy run
> with and without the leak: Sharpe 4.6 vs −0.6, with a receipts table proving
> every contaminated retrieval.
>
> Reproduction (30 seconds, no keys): https://github.com/ebeirne/lookahead-bias-demo
>
> If your team is putting agents anywhere near the research loop, worth 20
> minutes? We'll run the contamination check against your memory setup — if
> it's clean, you've lost 20 minutes; if it isn't, you found it before the
> examiner did.
>
> — E

One paragraph, one link, one ask. No feature list. Follow-up after 5 business
days: forward the HN thread ("this got some discussion — the late_revision
case in the comments is the one most shops miss").

## 6. Launch-day checklist

- [ ] Standalone repo public, README chart renders, `pip install` path tested on a clean venv
- [ ] Repo linked from lians.ai and from the main Lians README
- [ ] HN post 8–10am ET Tue–Thu; reply to every substantive comment within 30 min
- [ ] r/algotrading ~2h later; LinkedIn post 1 that afternoon, posts 2–3 next two days
- [ ] 10 warm DMs with the 5-slide summary, day 1–2
- [ ] Cold list (20 names) starts day 2, 5/day
- [ ] Log every "we should check ours" reply → design-partner pipeline sheet
