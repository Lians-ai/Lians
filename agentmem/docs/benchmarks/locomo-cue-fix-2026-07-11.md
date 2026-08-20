# Cue-Supersession Fix + Interjection Extraction - LOCOMO A/B and New Baseline

**Date:** 2026-07-11 · **Embeddings:** `Snowflake/snowflake-arctic-embed-l-v2.0` · **Protocol:** judge-free evidence retrieval, k=10, cats 1–4 (n=1,536) - same as `locomo-v0.4.0.md` · **Harness:** `benchmarks/locomo_eval.py`

An A/B run to gate the interjection-extraction default instead caught a regression in the unkeyed revision-cue supersession shipped 2026-07-10 - then validated the fix to a new best evidence-retrieval result.

## Headline

| Engine | evidence_hit@10 | evidence_all@10 |
|---|--:|--:|
| Published baseline (2026-07-09, pre-cue) | 82.4% | 68.5% |
| HEAD with broken cue lexicon | 79.4% | 64.9% |
| **HEAD fixed + interjection extraction ON (new default)** | **83.5%** | **69.4%** |

## What broke

The revision-cue path (a cued unkeyed update supersedes its most-similar prior fact at cos ≥ 0.60) used a lexicon containing bare `wait`, `now`, `actually`, `quit` - constant filler in casual dialogue. DB forensics over the 10 A/B ingest databases: **527 of 5,882 raw turns (9%) were falsely closed** ("can't **wait** to see it!" closing "Wanna see my moves next Fri?"; a reminiscing turn closing "Lost my job as a banker yesterday"), each closure deleting a turn from live recall. The interjection feature was exonerated by a controlled arm: extraction ON and OFF at HEAD scored identically (conv_1: 72.8/64.2 both), and only 13 of 540 total closures came from derived clauses - nearly all legitimate.

## The fix (`supersession.py`)

1. **Lexicon tightened**: `wait`/`actually` only in self-correction comma form (`wait,`); `now` guarded against "what now / right now / for now / sentence-initial Now"; `quit` guarded against negation ("won't quit"); bare `move` dropped (kept `moved`).
2. **Compactness gate**: a revision announces itself compactly ("I eat fish now", "my day rate went up to $1100") - a reminiscence rambles. Cue candidacy now requires the cue-bearing text ≤ 160 chars (`SUPERSESSION_CUE_MAX_LEN`). Offline replay: the two changes prevent **81% of the false closures** while every calibrated true revision still qualifies; conv_1 raw-turn closures fell 46 → 5.
3. **Cue inheritance** (`cue_hint`): a derived interjection clause inherits its parent turn's cue status - the cue words often stay in the surrounding chatter while the clause is the revision payload.

Recovery check on the damage case (conv_1): broken 72.8/64.2 → fixed **79.0/70.4**, decimal-identical to the pre-cue engine, extraction ON and OFF alike.

## Interjection extraction: now default-on

`interjection.py` extracts durable-fact clauses buried in conversational turns ("...their whole team flying into **my studio in Portland**...", "**remind me I eat fish now**" mid-task) as derived memories beside the raw turn - deterministic clause splitting + cue lexicon, no model call. Derived rows carry `metadata._derived/._parent`, drop structured keys, inherit `dia_id`-style evidence metadata, and run the full supersession funnel, so a buried revision closes its buried predecessor. When a clause closes, the parent turn is timestamp-marked (`_stale_clauses`) and demoted at recall, time-awarely - `as_of` recall before the revision is untouched.

Found and validated by `benchmarks/agent_sim.py` (LLM-driven user with hidden persona, deterministic probe scoring): interjection probes went 3/6 → 100% of probes the sim actually uttered. The harness now records per-probe `top_k` texts and skips probes whose answer the sim never worked into the conversation.

## Full validation matrix

| Suite | Result |
|---|---|
| LOCOMO 10-conv evidence retrieval (new default config) | **83.5 / 69.4** (prior best 82.4/68.5) |
| Lifecycle raw (both extraction modes) | 45/47 - baseline held, known ceiling only |
| Lifecycle keyed | 47/47 |
| agent_sim | 100% of uttered probes, incl. as_of time-travel |
| supersession_eval / finance_bench | 12/12 · 8/8 |
| tests/test_interjection.py | 13/13 |

## Reproduce

```
INTERJECTION_EXTRACTION_ENABLED=  # now the default
SENTENCE_TRANSFORMER_MODEL=Snowflake/snowflake-arctic-embed-l-v2.0 \
python -m benchmarks.locomo_eval --conv N --k 10 --embeddings sentence-transformers \
    --db results/locomo_dbs_fixed/conv_N.sqlite --out results/locomo_fixed/conv_N.json
python -m benchmarks.locomo_aggregate results/locomo_fixed
```
