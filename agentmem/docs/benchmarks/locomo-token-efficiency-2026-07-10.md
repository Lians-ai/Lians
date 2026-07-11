# LOCOMO Token Efficiency — Judged Accuracy per Context Token

**Date:** 2026-07-10 · **Tokenizer:** `o200k_base` (exact, the GPT-4o/gpt-5 tokenizer) · **Harness:** `benchmarks/locomo_tokens.py` over the judged-run retrieval dumps (`predicted_lians_arctic/`, 1,540 questions, categories 1–4) · **Accuracy source:** unmodified mem0ai/memory-benchmarks judge (see `locomo-judged-2026-07-09.md`)

Accuracy is half the story: the other half is how many tokens the answering model must read to get it. This report pairs each judged-run cutoff with the exact context cost of that operating point, against the baseline of stuffing the entire conversation into context.

## Results

| Answer context | Judged accuracy | Mean tokens / question | Share of full context |
|---|--:|--:|--:|
| Lians top-10 | 83.4% | 549 | 3.0% |
| Lians top-20 | 87.3% | 1,083 | 5.9% |
| Lians top-50 | 90.0% | 2,656 | 14.6% |
| Lians top-200 | **92.9%** | 10,283 | 56.4% |
| Full conversation | — | 18,218 | 100% |

Reference point: mem0's published LOCOMO score is **91.6**. Lians clears it at top-200 while sending ~44% fewer tokens than full context, and reaches 90.0% — within 1.6 points of mem0's headline — at **one-seventh** of the full-context cost (85.4% token reduction).

## Method

- **Context cost** = tokens of the top-k retrieved memories as dumped for the judged run, rendered `[created_at] text` per line — the same payload the mem0 harness's answer prompt consumes. Exact `o200k_base` counts, not estimates.
- **Full-context baseline** = every session of the question's conversation rendered as `speaker: text` turns under session-date headers (mean 18,218 tokens; p95 21,045).
- **Accuracy** = per-cutoff `metrics_by_cutoff` from the judged run (gpt-5 answerer + judge, grading verbatim from mem0's harness). Same 1,540 questions in both columns.
- Judge-free and model-free: this analysis is pure accounting over existing artifacts — rerunning it costs nothing.

## Reproduce

```
python -m benchmarks.locomo_tokens \
    --pred ../memory-benchmarks/results/locomo/predicted_lians_arctic \
    --out results/locomo/token_efficiency.json
```

Per-cutoff medians and p95s land in `results/locomo/token_efficiency.json`.
