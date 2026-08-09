# LOCOMO lexical + ONNX reranker audit — 2026-08-08

The optional local profile `pure BM25 top100 → ms-marco MiniLM ONNX cross-encoder → top20` passed the predeclared full-corpus quality gates and a renderer-inclusive fresh-process latency gate. It is the only configuration among candidate windows 30/50/75/100 and return sizes 3/5/20 that passed every overall, category, and corpus-size requirement.

This result supports an optional evidence-retrieval backend. It does **not** establish Sol answer quality, refusal correctness, universal prompt coverage, token savings, or Codex credit extension.

## Decision summary

| Measure | Candidate | Archived BGE reference | Delta / gate |
|---|---:|---:|---:|
| Cats 1–4 evidence-any | 74.349% | 72.526% | +1.823 pp |
| Cats 1–4 evidence-all | 60.872% | 59.700% | +1.172 pp |
| Fresh-process p95, renderer included | 2.750 s | 3.500 s target | 0.750 s under |
| Exact top-100 product candidate parity | 100/100 | required exact | pass |
| Exact cross-encoder order parity | 100/100 | required exact | pass |

The quality comparison covers all ten read-only LOCOMO conversation stores: 1,986 prompts total, 1,982 with evidence, 1,536 answerable category 1–4 prompts, and 446 adversarial category-5 prompts. No language-model or network calls were made.

The archived reference is BGE at `k=10`; no immutable BGE `k=20` artifact exists. The candidate retrieves 20 rows before the Codex renderer applies its context budget, so this is a profile comparison rather than an equal-`k` model comparison.

## Predeclared quality gates

The candidate could trail the archived reference by at most:

- 1 percentage point for overall evidence-any and evidence-all;
- 3 percentage points for evidence-any in each answerable category;
- 2 percentage points for evidence-any in each corpus-size stratum.

The exact checked-out renderer result passed all three gates:

| Stratum | Candidate hit | BGE hit | Delta |
|---|---:|---:|---:|
| Category 1 | 69.858% | 65.248% | +4.610 pp |
| Category 2 | 81.620% | 79.439% | +2.181 pp |
| Category 3 | 43.478% | 45.652% | −2.174 pp |
| Category 4 | 76.457% | 75.268% | +1.189 pp |
| Large corpus, ≥650 | 74.937% | 74.937% | 0.000 pp |
| Medium corpus, 500–649 | 74.755% | 70.254% | +4.501 pp |
| Small corpus, <500 | 71.429% | 69.264% | +2.165 pp |

Compact alternatives did not graduate. `w100→k3` scored 65.104% evidence-any / 51.823% evidence-all; `w100→k5` scored 69.141% / 56.185%. `w75→k20` reached 72.135% / 58.789% but failed the corpus-size gate. Only `w100→k20` passed every gate.

## Category 5 boundary

For the 446 category-5 prompts, the exact rendered context contained any gold evidence for 63.901%, all gold evidence for 62.332%, and the adversarial answer string for 37.892%.

These are exposure measurements. They are not refusal correctness, safety-judge accuracy, or answer quality; no answer model ran.

## Cold latency

Ten separate Python processes each performed the following sequence from scratch: open the conv0 SQLite store read-only, generate 100 lexical candidates, load an FP32 ONNX cross-encoder and tokenizer, rerank to 20, and apply the checked-out Codex 768-budget renderer.

- Median outer wall time: **2.662 s**
- Nearest-rank p95 / maximum: **2.750 s**
- Target: **<3.500 s**
- Every run returned 20 ranked memories and the same top evidence ID, `D1:3`

The machine was an AMD Ryzen 9 7940HS, Windows 10 19045, AMD64, Python 3.12.6, ONNX Runtime 1.28.0, with four intra-op threads. Each run used a fresh process and fresh ONNX session, but the operating-system file cache was not explicitly flushed. This is process-cold latency with persistent local artifacts, not a power-cycle or cold-disk measurement.

## Renderer token finding

The hook calls its setting a token budget, but currently enforces `max_tokens × 4` characters. Across all 1,982 scored prompts, the `768` setting rendered:

| Distribution | Characters | char/4 estimate | Exact `o200k_base` tokens | Memories |
|---|---:|---:|---:|---:|
| Minimum | 2,371 | 593 | 735 | 8 |
| p50 | 3,072 | 768 | 854 | 12 |
| p95 | 3,072 | 768 | 943 | 18 |
| p99 | 3,072 | 768 | 979 | 20 |
| Maximum | 3,072 | 768 | 1,018 | 20 |

The renderer marked 98.587% of cases truncated. Therefore, this profile must not be described as an exact 768-token ceiling: its median exact count was 854 tokens and its maximum was 1,018.

## Mixed-vector-space audit

A separate all-prompt candidate-generation ablation confirms that a zero-padded 384-dimensional MiniLM query must never score stored BGE 1024-dimensional documents. Right-zero-padding preserves MiniLM cosine only when both queries and documents were embedded by MiniLM; it does not create a shared space with BGE.

| Candidate generator, all 1,982 evidence prompts | Hit@20 | All@20 | Hit@30 | All@30 | Hit@100 | All@100 |
|---|---:|---:|---:|---:|---:|---:|
| MiniLM dense, valid same space | 63.169% | 52.624% | 69.324% | 58.527% | 85.621% | 75.933% |
| Lexical only | 56.155% | 46.973% | 61.554% | 51.312% | 79.062% | 67.911% |
| MiniLM hybrid, valid same space | 66.902% | 56.155% | 72.755% | 62.109% | 87.487% | 78.557% |
| MiniLM query vs BGE documents, invalid mixed space | 3.532% | 2.523% | 5.096% | 3.280% | 16.801% | 10.898% |

The graduated lexical → cross-encoder profile avoids dense-vector compatibility entirely. A separate plain MiniLM ONNX dense path reached 0.827 s process-cold p95 but regressed conv0 evidence quality to 58.0% / 46.67% at `k=10` versus the BGE reference's 68.67% / 56.0%, so it was rejected. PyTorch MiniLM fresh-process startup took roughly 9.5–11.3 seconds and also failed the latency target.

## Product parity and admission condition

On the conv0 check query, the benchmark matched the checked-out product's complete 100-candidate order under pure `src.lians.ranking._bm25_score` with `event_time`/ID ties. It also matched the product ONNX reranker's complete 100-row order and top 20 exactly.

The full quality audit deliberately used synthetic score `1.0` and renderer minimum score `0.0`; cross-encoder logits were used only for ordering. The current reranker returns each row with its original candidate score. A production profile only inherits this quality result when renderer admission is rank-based or otherwise configured not to discard the evaluated CE top 20. Applying an unrelated fixed BM25 threshold after cross-encoding is outside this audit.

## Artifact and runtime provenance

The supplied graph was exported locally from the cached `cross-encoder/ms-marco-MiniLM-L-6-v2` checkpoint as FP32 ONNX opset 17. It accepts `input_ids`, `attention_mask`, and `token_type_ids` as `int64[batch, sequence]` and returns `logits` as `float[batch, 1]`. The runner used maximum length 256 and batch size 64.

- ONNX graph: 90,977,787 bytes; SHA-256 `e7025df05c3fb09e6de28b123d11ab1d92fcbb21c07fa5457007a0c54e546c6f`
- Tokenizer JSON SHA-256: `d241a60d5e8f04cc1b2b3e9ef7a4921b27bf526d9f6050ab90f9267a1f9e5c66`
- Benchmark SHA-256: `071a4c01a1d0685f9bf231a96f855cf67150f576c05a3ad5604a5facecb917ad`
- Renderer SHA-256: `3532d7b3744381ad8519bc3cad9692f6b86cfd2803190de52ab4e9a4202fc13c`

No model binary is checked in. The benchmark requires explicit local `--model` and `--tokenizer` paths and never downloads artifacts.

Trial packages added or updated in `agentmem/sdk/python/.venv` were retained after the combined gate passed: `onnxruntime==1.28.0`, `onnx==1.22.0`, `flatbuffers==25.12.19`, `ml-dtypes==0.5.4`, and `protobuf==7.35.1`. Runtime inference needs ONNX Runtime, Tokenizers, and NumPy; ONNX and its export-side transitive packages are not required merely to run an already-exported graph.

## Reproduction

The checked-in runner is `agentmem/benchmarks/locomo_onnx_lexical_rerank_eval.py`. With the external graph and tokenizer available:

```powershell
agentmem\sdk\python\.venv\Scripts\python.exe `
  agentmem\benchmarks\locomo_onnx_lexical_rerank_eval.py full `
  --model C:\path\to\ms-marco-MiniLM-L-6-v2.onnx `
  --tokenizer C:\path\to\tokenizer.json `
  --candidate-windows 30 50 75 100 `
  --output-ks 3 5 20 `
  --workers 4 `
  --out C:\path\to\full-report.json

agentmem\sdk\python\.venv\Scripts\python.exe `
  agentmem\benchmarks\locomo_onnx_lexical_rerank_eval.py cold `
  --model C:\path\to\ms-marco-MiniLM-L-6-v2.onnx `
  --tokenizer C:\path\to\tokenizer.json `
  --candidate-window 100 `
  --output-k 20 `
  --repeats 10 `
  --out C:\path\to\cold-report.json

agentmem\sdk\python\.venv\Scripts\python.exe `
  agentmem\benchmarks\locomo_onnx_lexical_rerank_eval.py parity `
  --model C:\path\to\ms-marco-MiniLM-L-6-v2.onnx `
  --tokenizer C:\path\to\tokenizer.json `
  --candidate-window 100 `
  --output-k 20 `
  --out C:\path\to\parity-report.json
```

Machine-readable aggregates, exact hashes, dependencies, and limitations are in `docs/benchmarks/locomo-onnx-lexical-rerank-2026-08-08.json`.
