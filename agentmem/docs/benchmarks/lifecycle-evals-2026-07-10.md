# Lifecycle Evals - State Reconstruction, Time-Travel, Interjections

**Date:** 2026-07-10 · **Embeddings:** `Snowflake/snowflake-arctic-embed-l-v2.0` · **Scoring:** deterministic (case-insensitive substring), no LLM judge · **Harnesses:** `benchmarks/lifecycle_eval.py`, `benchmarks/scale_eval.py`, `benchmarks/agent_sim.py`

Where LOCOMO/LongMemEval score ranked recall on QA, this suite tests the thing a memory *lifecycle* is responsible for: maintaining a correct dynamic model of a user as facts are revised, interleaved, backdated, and buried under noise.

## What it tests

1. **State reconstruction** - messy interleaved preference streams ("I'm vegan" → "I eat fish now" → "allergic to salmon"). The profile is reconstructed from `snapshot(now)`: current value must be live, every superseded value must be gone (a leaked stale value = a zombie fact the agent would act on).
2. **Temporal disambiguation / time-travel** - the same profile reconstructed `as_of` past checkpoints (2024 meeting notes vs 2026 org update; the era's belief must be live, later values absent). Includes an out-of-order case: a backdated amendment ingested *after* the value that replaced it.
3. **Interjections** - a background fact dropped mid-task ("...my mother's birthday is June 5th, buy flowers") must be stored and recallable without polluting task-focused recall.
4. **Scale / closure / latency** (`scale_eval.py`) - core preferences revised twice each, interleaved with ~1,000 noise turns: latent recall of buried current values, closure of superseded revisions, latency curve.
5. **Agent-to-agent simulation** (`agent_sim.py`) - an LLM "User" with a hidden persona converses across sessions; scoring stays deterministic against the persona's ground truth. Requires `ANTHROPIC_API_KEY`.

Two modes isolate the layers: `--mode keyed` (caller supplies entity/field metadata - tests the lifecycle engine alone) and `--mode raw` (plain conversational text, passthrough adapter - the mem0/Zep-style deployment; semantic supersession must detect revisions unaided).

## Results

| Metric | Before fixes | After fixes |
|---|---|---|
| Keyed overall | 93.6% | **100%** (47/47) |
| Raw (plain text) overall | 61.7% | **95.7%** (45/47) |
| Time-travel checkpoints (both modes) | 100% | **100%** |
| Scale: closure of superseded revisions (330 & 1,030 turns) | 20/20 | **20/20** |
| Scale: stale values in top-5 under noise | 0 | **0** |
| Local ingest (per memory) | 1,232 ms | **200 ms** |
| Local recall p50 @330 live memories | 667 ms (2.4 s with Redis misconfig) | **357 ms** |

The two remaining raw-mode failures are one scenario: "I live in Chicago" → "I moved to Denver last week" measures doc-doc cosine 0.562, below the calibrated 0.60 revision-cue threshold (the strongest should-NOT-supersede control measures 0.587). This is the deterministic ceiling; Stage-3 LLM adjudication catches it when enabled.

## Fixes shipped (found by this suite)

1. **Backdated writes now close on arrival.** A fact whose event_time predates an existing live successor gets `valid_to` = the successor's event_time - queryable for its own era via `as_of`, never polluting the current view. Closure requires full metadata equivalence (not just structured keys), so a Q2 figure can't close a late-arriving Q1 correction.
2. **Unkeyed free-text supersession is now reachable.** Previously, differing unkeyed facts always classified ADDS and Stage-3 LLM adjudication was gated behind a Stage-2 SUPERSEDES that could never fire - plain-text revisions structurally couldn't supersede. Now: an update that *announces itself* as a revision (regex cue lexicon: "now", "instead", "switched", "moved", "promoted", "taken over", "revised"…) admits same-topic candidates at cosine ≥0.60 and supersedes its single most-similar candidate at confidence 0.7 - deterministic, reproducible, visible in `review_supersessions`, and Stage-3-eligible.
3. **Local mode no longer pays a Redis tax.** Cache helpers early-out on `recall_cache_enabled`; `LocalLiansClient` defaults it off. Was ~2 s per recall and ~1 s per write in connect timeouts.
4. **Vectorized scoring + batched hydration.** `_cosine` uses numpy; `hybrid_recall` fetches Memory rows with one IN-query instead of one `db.get()` per live fact. Remaining local-mode recall cost is ORM hydration of the candidate pool (~0.6 ms/live memory) - the next lever is a session-cached embedding matrix or raw-core rows.

## Reproduce

```
python -m benchmarks.lifecycle_eval --mode keyed
python -m benchmarks.lifecycle_eval --mode raw
python -m benchmarks.scale_eval --noise 1000
python -m benchmarks.agent_sim            # needs ANTHROPIC_API_KEY
```

Reports land in `results/lifecycle/`.
