# LOCOMO production-profile full-corpus audit

**Date:** 2026-08-08 · **Cost:** zero language-model credits and zero network calls · **Corpus:** all 1,986 LOCOMO prompts · **Profile:** `k=20`, 768-token estimate / 3,072-character cap, minimum score 0.45

This audit applies the checked-out Codex production renderer to all 1,540 archived category 1–4 predictions and a deterministic cached-Arctic replay of all 446 category-5 prompts. It measures what retrieval would expose before and after the renderer boundary; it does not generate or judge answers.

## Results

| Slice | n | Injected | Evidence any: raw top-20 | Before cap, after 0.45 threshold | After cap | Evidence all after cap |
|---|---:|---:|---:|---:|---:|---:|
| Answerable categories 1–4 | 1,540 | 118 (7.66%) | 87.76% of 1,536 annotated | 4.36% | 4.36% | 4.04% |
| Adversarial category 5 | 446 | 41 (9.19%) | 85.65% | 2.02% | 2.02% | 2.02% |
| Full corpus | 1,986 | 159 (8.01%) | 87.29% of 1,982 annotated | 3.83% | 3.83% | 3.58% |

The 768-token-estimate cap itself caused no evidence loss and no context was marked truncated. The absolute 0.45 score threshold caused nearly all of the loss between raw top-20 retrieval and rendered context.

For the 118 injected answerable prompts, exact `o200k_base` returned-context tokens were p50 **93**, p95 **189**, p99 **323**, and max **355**. Across all 159 injections they were p50 90, p95 181, p99 323, and max 355—well below the nominal cap.

Literal answer-string coverage on categories 1–4 was 32.27% in raw top-20, 1.95% after the score threshold, and 1.88% after rendering. This is deliberately a weak lexical diagnostic; temporal and inferred answers often do not occur verbatim even when the annotated evidence is present.

For category 5, the annotated adversarial answer string appeared in 47.76% of raw top-20 retrievals but only 1.12% after the production boundary. This is exposure telemetry, not refusal accuracy: category 5 contains false-premise/entity-swap questions and needs a generation/judge test to establish whether a model refuses correctly.

## Zero-credit threshold and rank-policy sweep

The sweep holds each prompt's ranking constant and changes only the admission rule. Fixed thresholds are meaningful only on this archived Arctic score scale. Top-N is rank-only; calibrated gates must be learned separately for every retrieval backend/model revision.

| Policy | Answerable injected | Evidence any after cap | Evidence all after cap | Exact tokens p95 / p99 | Category-5 adversarial-string exposure |
|---|---:|---:|---:|---:|---:|
| Fixed 0.45 (current artifact profile) | 7.66% | 4.36% | 4.04% | 189 / 323 | 1.12% |
| Fixed 0.40 | 33.64% | 21.68% | 18.55% | 579 / 841 | 6.95% |
| Provider-calibrated top-3, p50 top-score gate | 49.61% | 34.90% | 29.49% | 292 / 311 | 14.57% |
| Provider-calibrated top-3, p25 top-score gate | 73.44% | 50.20% | 41.73% | 294 / 320 | 25.78% |
| **Top-3, no absolute floor** | **100%** | **64.65%** | **52.08%** | **291 / 320** | **31.17%** |
| Top-5, no absolute floor | 100% | 73.76% | 59.90% | 450 / 491 | 36.77% |
| Fixed 0.00 | 100% | 84.38% | 70.51% | 931 / 970 | 44.17% |

Fixed 0.00 truncates 99.48% of answerable contexts and reaches 1,002 exact `o200k_base` tokens. The renderer's 3,072-character budget is therefore not a reliable 768-token hard cap when JSON punctuation and escaping fill the payload.

### Recommendation before paid Sol calls

Advance **rank-only top-3 with no universal absolute score floor** as the primary paid-validation candidate. It restores answerable evidence-any by 60.29 percentage points over fixed 0.45, keeps exact returned tokens at p95 291 / p99 320 / max 353, and caused no truncation. It is invariant to monotonic score-scale changes because it depends on rank, not one backend's numeric calibration.

This is a candidate, not approval for universal shipping. Category-5 adversarial-answer exposure rises to 31.17%, so the paid matrix must explicitly grade false-premise handling/refusal; retrieval confidence alone cannot solve that generation behavior. Keep the calibrated top-3 p25 gate as a conservative comparator, but do not hardcode its artifact-derived numeric gate (`0.341478`): on this corpus it injected 73.44% of answerable prompts and 80.49% of category 5, so top score did not distinguish false premises.

Before broad production rollout, enforce a true model-tokenizer hard cap. The character/4 estimate can exceed its nominal budget once contexts fill.

### Frozen ten-prompt paid-manifest check

For the exact frozen conversation-0 QA indices `3, 4, 0, 1, 2, 14, 82, 83, 152, 153`, a literal `k=3`, `min_score=0` profile injects on all ten prompts.

| QA index | Category | Evidence any in top-3 | Evidence all in top-3 |
|---:|---:|---:|---:|
| 3 | 1 | yes | yes |
| 4 | 1 | no | no |
| 0 | 2 | yes | yes |
| 1 | 2 | no | no |
| 2 | 3 | yes | yes |
| 14 | 3 | yes | no |
| 82 | 4 | yes | yes |
| 83 | 4 | yes | yes |
| 152 | 5 | yes | yes |
| 153 | 5 | no | no |

Across the eight answerable prompts, evidence-any is **6/8** and evidence-all is **5/8**. Across the two adversarial prompts, one surfaces its annotated adversarial evidence. Therefore `k=3/min_score=0` is suitable as a paid candidate, but the manifest must not assume it is already retrieval-safe: two answerable cases have no annotated evidence in top-3, a third has only partial evidence, and false-premise behavior still needs generation-level grading.

Increasing the same frozen cases to top-5 or capped top-20 does not change any evidence-any/all result. QA4's evidence ranks 88, QA1's ranks 30, and QA14's missing second record ranks 75. Top-5 returns 278–453 exact tokens without truncation; top-20 truncates all ten contexts and returns 772–864 exact tokens. For this paid slice, raising `k` adds tokens without adding annotated evidence.

## Evidence and limitations

- Categories 1–4 use the archived prediction files directly. All 30,800 examined top-20 records mapped uniquely to their LOCOMO dialogue IDs.
- Category 5 was excluded from the upstream judged archive. Its 446 prompts use the original read-only SQLite contents, cached Snowflake Arctic document/query embeddings, and the checked-in deterministic ranking recipe. On 39 stratified category 1–4 checks against the archive, replay achieved 72.31% exact positional top-20 agreement, 85.64% top-20 set overlap, and 65.38% positional score agreement at six decimals. Category-5 results are therefore complete but not artifact-exact.
- The 0.45 threshold was applied arithmetically exactly, but the July artifact/replay scores are not proven to share the current SDK's absolute score calibration. The low injection rate is an artifact-profile warning, not a current live-corpus production claim. A freshly migrated current-SDK corpus is required before setting one universal absolute threshold.
- The current production `render_context` function was executed for every prompt, at renderer SHA-256 `8941916055126206f4c662b8a69206b8a2301c08b25965cd1988a8a76eee2843`; threshold, cap, injection, and returned-context measurements are exact for that revision.
- Direct current-SDK recall against the July checkpoints was not claimed. A read-only schema preflight found the old stores lack `system_valid_from`, `system_valid_to`, and `governance_status`. The evidence databases were not mutated to make the test pass.

The machine-readable report, including per-question results and provenance hashes, is [`locomo-production-profile-corpus-2026-08-08.json`](./locomo-production-profile-corpus-2026-08-08.json). Reproduce without model calls:

```powershell
.\.venv\Scripts\python.exe agentmem\benchmarks\locomo_production_profile_eval.py
```
