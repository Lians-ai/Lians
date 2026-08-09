# Codex GPT-5.6 Sol effort matrix

**Date:** 2026-08-08

**Primary verdict:** not qualified as an every-prompt guarantee
**Pooled economic result:** 2.22x same-budget usage (+122.10%) on this frozen workload

## What was tested

The signed-in Codex CLI ran the only user-visible Sol model, `gpt-5.6-sol`, at
all six supported reasoning efforts: low, medium, high, xhigh, max, and ultra.
Each effort ran the same ten frozen LOCOMO prompts with a balanced baseline and
Lians-candidate order, for 120 top-level turns. The service tier was `default`.

The candidate used the exact production pre-model hook, a separately prewarmed
daemon, the pinned FP32 BGE ONNX provider, `k=20`, rank admission, and a 768
estimated-token context budget. Every candidate run required a successful,
non-degraded daemon receipt. No failed invocation was dropped or cherry-picked.

The finite live matrix is deliberately not described as testing every possible
prompt. A separate offline audit covered all 1,986 available LOCOMO prompts,
including 446 adversarial category-5 prompts, but offline evidence coverage is
not equivalent to model answer quality.

## Primary quality-first result

The predeclared primary grader required an exact accepted answer or exact alias
before economics could qualify a cell. Only 24 of 60 paired cells passed that
quality gate, 49 of 60 passed the +80% economic threshold, and 21 of 60 passed
both. The candidate passed 35 of 60 individual exact-answer checks versus 30 of
60 for baseline, but neither arm passed every prompt. Therefore the declared
matrix is **not qualified**, even though its pooled economics passed.

A secondary, explicitly posthoc semantic rubric later resolved 87/120 answers:
82 passed, 5 failed, and 33 remained unresolved. Because that audit is incomplete
and was not predeclared, it cannot revise the primary failure. Earlier independent
judge attempts were discarded or failed without complete aggregate cost telemetry;
the final accepted artifact made no external judge call and reports that limitation.

| Effort | Baseline estimated credits | Lians estimated credits | Same-budget extension | Exact quality, baseline / Lians | Qualified cells |
| --- | ---: | ---: | ---: | ---: | ---: |
| low | 28.393525 | 13.612575 | +108.58% | 5/10 / 6/10 | 4/10 |
| medium | 28.384525 | 13.613200 | +108.51% | 5/10 / 6/10 | 3/10 |
| high | 32.525875 | 13.615200 | +138.89% | 5/10 / 6/10 | 4/10 |
| xhigh | 28.884175 | 13.615700 | +112.14% | 5/10 / 6/10 | 2/10 |
| max | 30.113825 | 11.135775 | +170.42% | 5/10 / 5/10 | 4/10 |
| ultra | 27.630525 | 13.618950 | +102.88% | 5/10 / 6/10 | 4/10 |

Across all efforts, the candidate-to-baseline estimated-credit ratio was
`0.450237577`, or 2.22x same-budget usage (+122.10%). Treating all input as
uncached produced ratio `0.494470756`, or +102.24%. The worst individual cell
ratio was `4.061632602`, which is why the pooled result must not be promoted as
an every-prompt claim. Credits were calculated from complete host token
telemetry and documented Sol rates; they were not provider billing debits.

## Retrieval latency

Every one of the 60 candidate cells satisfied the retrieval contract and the
3.5-second fresh-hook-to-prewarmed-daemon target. The maximum recorded hook
receipt was 1.631 seconds. This is user-prompt latency after prewarm, not true
process-cold latency.

The exact dependency-light BGE ONNX runner measured 2.815 seconds p95 and 2.852
seconds maximum across ten fresh processes, with ordered top-20 parity against
PyTorch. Full daemon startup remained slower: 6.574 to 12.152 seconds across the
recorded product and matrix runs. A synchronous, zero-output `SessionStart`
hook moves that work ahead of the first user prompt.

## Evidence and boundaries

- [Machine-readable matrix report](./codex-sol-matrix-bge-onnx-v2-report-2026-08-08.json)
- [Secondary semantic audit](./codex-sol-matrix-bge-onnx-v2-semantic-audit-2026-08-08.md)
- [Frozen manifest](./manifests/codex-sol-locomo-10-case-bge-onnx-v2.json)
- [BGE ONNX provider parity](./codex-bge-onnx-provider-parity-2026-08-08.json)
- [BGE ONNX production hook latency](./codex-bge-onnx-hook-daemon-latency-2026-08-08.json)
- [Full-corpus retrieval audit](./locomo-production-profile-corpus-2026-08-08.md)

The result supports a workload-scoped pooled efficiency claim. It does not
support “+80% on every prompt,” a universal account-quota guarantee, a true
sub-3.5-second full cold start, or a conclusion that exact string grading fully
captures semantic answer quality.
