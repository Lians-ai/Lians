# LOCOMO judged benchmark - Lians 92.9% (vs Mem0's published 91.6)

**Date:** 2026-07-09 · **Protocol:** LLM-judged QA accuracy ("J-score"), measured by the
**unmodified [mem0ai/memory-benchmarks](https://github.com/mem0ai/memory-benchmarks) harness**
· **Answerer/Judge:** `gpt-5` / `gpt-5` (the harness's own defaults) ·
**Dataset:** snap-research locomo10, categories 1–4, n = 1,540 questions ·
**Raw data:** `memory-benchmarks/results/locomo/locomo_results_20260709_102640.json`
(+ per-question files in `results/locomo/predicted_lians_arctic/`)

## Headline

On the LLM-judged LOCOMO protocol - the metric on which Mem0 publishes **91.6** -
Lians scores **92.9%** at the same top-200 retrieval cutoff, evaluated by Mem0's own
open-source harness with zero modifications: their answer-generation prompt, their
judge prompt and rubric, their metrics code, their default models. The only Lians
component in the loop is the retrieval payload each question receives.

| cutoff | overall | multi-hop (282) | temporal (321) | open-domain (96) | single-hop (841) |
|---|---|---|---|---|---|
| top-10 | 83.5% | 77.7% | 85.4% | 67.7% | 86.6% |
| top-20 | 87.3% | 84.0% | 88.5% | 66.7% | 90.2% |
| top-50 | 90.0% | 85.8% | 92.2% | 69.8% | 92.9% |
| **top-200** | **92.9%** | **91.1%** | **93.1%** | **75.0%** | **95.4%** |

For context, published J-scores on this benchmark: Mem0 **91.6** (their 2026
token-efficient algorithm update; their earlier paper reported 66.9), Zep **75.14**
(disputed), Synthius-Mem **94.4** (arXiv 2604.11563, different judge setup).

## How it was measured

The mem0 harness separates retrieval from judging. We used exactly that seam:

1. **Retrieval (Lians).** Every LOCOMO turn is ingested into `LocalLiansClient`
   (embedded SQLite, per-conversation stores) as an event-timed memory. For each of
   the 1,540 questions we dump the engine's top-200 recall in the harness's native
   `search_results` format (`benchmarks/locomo_dump_mem0.py` in the Lians repo).
   Dump-vs-live-engine equivalence is verified: the same offline scoring pipeline
   reproduced live `recall()` results exactly (100% agreement on all 1,982
   evidence-retrieval judgments, and digit-identical headline scores on two live
   verification runs).
2. **Answering + judging (Mem0's code).** `python -m benchmarks.locomo.run
   --evaluate-only` runs their pipeline over those files: chronological memory
   presentation, their 7-step answer prompt, binary CORRECT/WRONG judge with their
   partial-credit / paraphrase / date-tolerance rubric, gpt-5 in both seats.

### Retrieval configuration (Lians dev build, post-v0.4.0)

- **Embeddings:** `Snowflake/snowflake-arctic-embed-l-v2.0` (1024-dim, CPU), documents
  raw, queries with the model's `"query: "` instruction.
- **Ranking:** hybrid score `0.50·cosine + 0.05·BM25` + recency/importance terms,
  with **temporal-context smoothing** (a memory inherits 0.3× its strongest
  temporally-adjacent neighbor's semantic score, ≤1 h gap) and **temporal query
  grounding** (+0.1 for memories inside a calendar window named in the query).
  No MMR, no reranker, no LLM anywhere in the retrieval path - recall is
  deterministic.
- **Memory contents:** raw dialogue turns (speaker-prefixed, photo captions included).
  No LLM fact extraction was used for this run.
- Underlying evidence-retrieval quality (judge-free, gold dia-id metric):
  evidence_hit@10 = 82.4–84.3%, evidence_hit@200 = 98.4–98.6% on the same corpus.

### Token note

Lians memories are raw turns (~25 tokens each), so the top-200 payload is ≈5–6k
tokens per question - in the same band as Mem0's "under 7,000 tokens per retrieval
call" claim for their 91.6.

## Disclosures

- **Same protocol, not same sitting.** Mem0's 91.6 is their self-reported number.
  Ours was produced by running their published harness ourselves (single run;
  gpt-5 answerer/judge introduce some run-to-run variance, historically ±0.5pt on
  samples this size). The right head-to-head is both systems through this harness in
  one sitting - that run (their OSS backend vs ours, same models, same day) is the
  planned follow-up.
- **What differs between systems is memory content by design.** Mem0 stores
  LLM-extracted facts; Lians (this run) stores raw turns. The harness judges
  whatever the memory system returns - that asymmetry is the product comparison,
  not a protocol flaw.
- **Category 5 (adversarial, 446 questions) is excluded by the harness itself**
  (`CATEGORIES_TO_EVALUATE = [1,2,3,4]`), matching Mem0's published methodology.
- 15 questions from an initial smoke test were first judged at only two cutoffs;
  they were re-judged at all four cutoffs before the final metrics were computed
  (final files contain complete `cutoff_results` for all 1,540 questions).
- Windows reproducers need `PYTHONUTF8=1` (the harness reads/writes files with the
  platform default encoding).

## Reproduce

```bash
# 1. In the Lians repo (agentmem/): ingest + dump retrieval payloads
python -m benchmarks.locomo_eval --conv N --k 10 --embeddings sentence-transformers \
    --db results/locomo_dbs_arctic/conv_N.sqlite --out results/locomo_arctic/conv_N.json   # N = 0..9
python -m benchmarks.locomo_dump_mem0 --out ../memory-benchmarks/results/locomo/predicted_lians_arctic

# 2. In mem0ai/memory-benchmarks (OPENAI_API_KEY set, PYTHONUTF8=1):
python -m benchmarks.locomo.run --project-name lians_arctic --evaluate-only \
    --top-k-cutoffs 10,20,50,200 --dataset-path <path-to-locomo10.json>
```

## Where the remaining 7.1% lives (top-200 failure analysis)

110 questions are wrong at top-200. Splitting by whether the gold-evidence turns
were present in the retrieved 200:

- **90 answer-side** (evidence retrieved, answer still wrong): aggregation questions
  ("what activities has X done?" - items scattered across many turns), counting,
  wrong-instance selection between similar events, and specificity mismatches
  against the gold phrasing.
- **20 retrieval-side**: mostly date-pinned or low-vocabulary queries.

Follow-up work in progress targets both: opt-in **LLM fact distillation at ingest**
(`src/lians/enrichment.py` - derived, dated, attributed fact memories stored
alongside raw turns; the answer-side failures are largely aggregation over raw
dialogue) and the temporal grounding above (which took evidence_hit@10 from 82.4%
to 84.3% and is already included in this run's retrieval).
