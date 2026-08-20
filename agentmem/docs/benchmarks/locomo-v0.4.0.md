# LOCOMO benchmark report - Lians v0.4.0

**Date:** 2026-07-07 · **Engine:** Lians v0.4.0, `LocalLiansClient` (embedded SQLite) · **Embeddings:** BAAI/bge-large-en-v1.5 (CPU) · **Protocol:** judge-free evidence retrieval · **Raw data:** `results/locomo/` (per-conversation reports + `aggregate.json`, per-question detail included)

## Headline

On LOCOMO's 1,536 answerable questions (categories 1–4), Lians surfaces the
gold-evidence dialogue turn(s) in its top-10 recall for **72.5%** of questions
(`evidence_hit@10`), and surfaces *all* evidence turns for **59.7%**
(`evidence_all@10`). Its strongest category is the one the engine is built
around: **temporal questions, 79.4%**.

| Category | n | evidence_hit@10 | evidence_all@10 |
|---|---|---|---|
| **Headline (cats 1–4)** | **1,536** | **72.5%** | **59.7%** |
| temporal (2) | 321 | 79.4% | 73.8% |
| single-hop (4) | 841 | 75.3% | 72.5% |
| multi-hop (1) | 282 | 65.2% | 15.6% |
| open-domain (3) | 92 | 45.7% | 28.3% |
| adversarial (5, excluded) | 446 | 64.3% | 63.0% |

Per-conversation headline scores range 66.3%–80.9% (no outliers); the
aggregate is stable, not driven by any single conversation.

## Protocol and disclosures

- **What is scored.** LOCOMO annotates each question with the dialogue turn(s)
  containing the answer (`evidence` dia_ids). Every turn is ingested as an
  event-timed memory (session timestamp + per-turn second offsets; photo turns
  include the BLIP caption). A question scores a hit if any gold-evidence turn
  appears in `recall(query=question, k=10)`; `evidence_all` requires all of
  them. Deterministic - no LLM judge, no generation step.
- **What is NOT scored.** This is not the LLM-judge QA accuracy that Mem0
  (66.9%) and Zep (75.14% / disputed) report on LOCOMO. Those numbers grade a
  generated answer; ours isolates the retrieval half - the part a memory layer
  is responsible for - and is not comparable to theirs. A judged run is
  planned (phase 2), as are competitor runs through this same harness via the
  existing `benchmarks/adapters/` (phase 3).
- **Exclusions.** Category 5 (adversarial/unanswerable) tests refusal - a
  generation property - and is excluded from the headline but reported. The 4
  questions without evidence annotations are skipped.
- **Settings.** Default engine ranking (W_SEM .50 / W_LEX .20 / W_REC .15 /
  W_IMP .15, ANN prefetch 20×k), default admission control, k=10,
  `EMBEDDING_PROVIDER=sentence-transformers` (bge-large-en-v1.5, 1024-dim).
  No per-benchmark tuning of any kind.
- **Reproduce.**
  `python -m benchmarks.locomo_eval --conv N --k 10 --embeddings sentence-transformers --out results/locomo/conv_N.json`
  for N in 0..9, then `python -m benchmarks.locomo_aggregate results/locomo`.
  (Windows: set `PYTHONIOENCODING=utf-8`.)

## What the benchmark caught in our own product

Running a public benchmark honestly means reporting what it found in us:

1. **Local mode's default embedding provider is a test stub.**
   `LocalLiansClient(embedding_provider='local')` resolves to
   `LocalProvider` - deterministic token-hash vectors documented as "for
   tests". On this benchmark it scores **24.0%** vs bge's **68.7%**
   (conversation 1). Any `lians-sdk[local]` user who never sets a provider is
   silently getting test-grade semantic recall. → v0.4.1: change the default
   when sentence-transformers is installed, or warn loudly at construction.
2. **`as_of` recall does not re-anchor recency decay.** `_recency_decay`
   (ranking.py) always measures age from wall-clock now, even under
   point-in-time queries. Measured impact here: none (decay is uniformly ~0 on
   a 3-year-old corpus, so ordering is unaffected - confirmed by an A/B trial
   that scored identically). But it is semantically wrong for point-in-time
   recall and should decay relative to the pinned time. → v0.4.1 candidate.

## Failure analysis

**1. Multi-hop strict recall is structurally capped at k=10.**
`evidence_all@10` by number of required evidence turns:

| evidence turns | n | hit_any | hit_all |
|---|---|---|---|
| 2 | 134 | 60% | 27% |
| 3 | 57 | 63% | 11% |
| 4 | 45 | 71% | 0% |
| 5 | 40 | 82% | 0% |

Questions needing 4–5 specific turns to co-occupy half the top-10 never
succeed. Partial evidence is retrieved increasingly often (hit_any rises with
evidence count - more targets), but full assembly fails. This is a ranking
*diversity* problem as much as a quality one.

**2. Short, underspecified questions fail disproportionately.**
Questions ≤8 words hit 67.6%; longer questions 75.1%. Typical failures are
entity+verb stubs ("What did Caroline research?", "What is Caroline's
identity?") whose embeddings carry little signal, and whose evidence is a
first-person paraphrase sharing no vocabulary ("I'm transgender…").

**3. Open-domain questions are often inference, not retrieval.**
The worst headline category (45.7%) includes questions like "What would
Caroline's political leaning likely be?" - the gold evidence is a turn the
answer must be *inferred from*, not one that *states* it. Retrieval-only
protocols undercount here by design; worth noting rather than fixing.

**4. No temporal-position bias.** Evidence in early/mid/late thirds of a
conversation hits at 70.6% / 70.7% / 73.0% - recall is not biased toward
recent sessions on this corpus.

## Improvement plan (ranked by expected value ÷ effort)

1. **Add bge's query instruction prefix.** bge-en-v1.5 models are trained to
   embed retrieval *queries* prefixed with "Represent this sentence for
   searching relevant passages: ". We embed queries raw
   (`memory_service.py:664`). This is the documented usage of the model we
   ship, disproportionately helps exactly our worst slice (short queries), and
   is a two-line change gated on provider+call-site (documents stay
   unprefixed). Re-run the benchmark after; expect single-digit headline
   gains concentrated in short/multi-hop queries.
2. **Diversify top-k for multi-hop assembly (MMR or per-session caps).**
   The all@10=0% wall at 4–5 evidence turns suggests near-duplicate or
   same-topic turns crowd the top-10. Measure redundancy first (pairwise
   cosine within returned sets), then apply maximal-marginal-relevance or a
   soft per-session cap at rank time.
3. **Query expansion for short queries.** Deterministic option: append agent
   profile terms / expand entities from the relationship graph. LLM option
   (HyDE-style hypothetical statement) works but forfeits the "deterministic,
   judge-free" property of the default path - if added, keep it opt-in.
4. **Batch embeddings at ingest.** Ingest runs ~1.2–1.5s/turn because each
   `add` embeds singly. Batching (and optionally bge-small for dev) is a
   10–20× ingest speedup, and is the gate to running LongMemEval (whose
   corpus is ~20× LOCOMO's).
5. **Renormalize degenerate ranking terms.** On old corpora recency and
   importance contribute ~0 for every candidate, deadening 30% of the score
   mass. Ordering is unaffected, but any absolute-score thresholding
   downstream is distorted; renormalize weights over live signals.
6. **Ship the two v0.4.1 fixes above** (local-mode embedding default,
   `as_of` decay anchoring).

## Phases 2 and 3

- **Phase 2 - judged protocol:** generate answers from top-k memories and
  grade with an LLM judge, mirroring Mem0's published eval for comparability.
  Produces the number that sits next to Mem0's 66.9% / Zep's 75.14%.
  ~half-day build; ~$10–30 API cost per system per run.
- **Phase 3 - competitors through this harness:** ingest locomo10 through
  `benchmarks/adapters/` (Mem0, Zep, Letta, Hindsight, Supermemory) and score
  the identical evidence-retrieval metric. Real-SDK adapters are the
  methodological high ground in the current benchmark disputes: same data,
  same metric, executable by anyone.
