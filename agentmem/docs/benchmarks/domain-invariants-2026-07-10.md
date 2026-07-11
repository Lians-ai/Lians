# Domain Invariant Evals — Supersession Classifier & Financial Point-in-Time Recall

**Date:** 2026-07-10 · **Scoring:** deterministic (exact relation match / substring), no LLM judge · **Harnesses:** `benchmarks/supersession_eval.py`, `benchmarks/finance_bench.py`

Where LOCOMO/LongMemEval score ranked recall and the lifecycle suite scores state maintenance, these two evals pin down the engine's *decision layer*: does the supersession classifier assign the right relation between fact pairs, and does as-of recall return the value that was true at the query date in a realistic financial-facts stream?

## Supersession classifier (`supersession_eval.py`)

12 fact-pair cases over guidance revisions, restatements, rating changes, price-target moves, confirmations, and additive facts — each labeled with the expected relation (`SUPERSEDES` / `CONFIRMS` / `ADDS` / `CONTRADICTS_SAME_TIME`).

| Metric | Result |
|---|---|
| Overall accuracy | **1.00** (12/12) |
| SUPERSEDES precision | **1.00** |
| SUPERSEDES recall | **1.00** |

Covered distinctions include: raise vs. lower both supersede; identical value at a later time **confirms** (no false supersession); different metric on the same ticker **adds**; conflicting figures for the same period **contradict** rather than silently replace (deliveries revision, revenue restatement). One case documents Stage-2 behavior in isolation (cross-ticker, same metric → SUPERSEDES at Stage 2); in production Stage 1's metadata gate blocks that pair from ever reaching Stage 2.

## Financial point-in-time recall (`finance_bench.py`)

A year of financial facts with real supersession chains (NVDA guidance revised twice, AAPL margin updated, Moody's rating upgraded) ingested into a fresh in-memory store, then queried `as_of` eight dates spanning the revisions.

| Metric | Result |
|---|---|
| Point-in-time accuracy | **1.00** (8/8) |

Each probe requires the top result to be the value in force *at the as-of date* — before the first revision, between revisions, and after the last — including the control that an unrelated metric (AAPL services revenue) is untouched by a sibling metric's update.

## Context

These invariants are the executable substrate behind the regulated-memory comparison (`docs/regulated-eval-results.md`), where Lians scores 5/5 live vs. live-executed mem0 OSS (0.5) and Graphiti OSS (2.0) and capability-assessed Letta, Hindsight, and Supermemory (1.0 each). A same-day re-run of the Lians column (2026-07-10) reproduced 5/5. Note: regenerating that doc with `--write` requires `OPENAI_API_KEY`, or the live mem0/Graphiti columns silently fall back to capability-assessed and the live-run appendix is lost.

## Reproduce

```
python -m benchmarks.supersession_eval
python -m benchmarks.finance_bench
```
