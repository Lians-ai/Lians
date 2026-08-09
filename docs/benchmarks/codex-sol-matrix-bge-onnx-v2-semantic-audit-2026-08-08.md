# Codex Sol matrix secondary semantic audit

> **Secondary/posthoc only.** This audit does not replace, revise, or qualify the primary predeclared exact-match verdict, which remains failed.

## Outcome

- Primary verdict: `declared_matrix_not_qualified` (`qualified: false`).
- Primary exact-match snapshot: 65/120 (54.167%).
- Secondary semantic audit: incomplete: 87/120 runs resolved; 82 pass, 5 fail, 33 unresolved.
- All semantic answers passed: `false`.
- Frozen rubric SHA-256: `bd03bb21fa3bca7b007197a45c3c1d707fab954adc54ac2c5f2a8cfca4564a64`.
- Raw judge artifact SHA-256: `b7bea73d0d528335fd658b50279b8110968f9a3133971a4115c85fae3869c92e`.

No overall secondary rate is reported while cases remain unresolved. `semantic_qualification` is deliberately `not_applicable`; the original matrix remains not qualified.

## Per-prompt results

| Prompt | Cat. | Exact | Semantic | Semantic rate |
| --- | ---: | ---: | ---: | ---: |
| `conv0-cat1-first` | 1 | 6/12 | 6 pass, 0 fail, 6 unresolved | incomplete |
| `conv0-cat1-second` | 1 | 6/12 | 10 pass, 0 fail, 2 unresolved | incomplete |
| `conv0-cat2-first` | 2 | 12/12 | 12/12 | 100.000% |
| `conv0-cat2-second` | 2 | 5/12 | 12/12 | 100.000% |
| `conv0-cat3-first` | 3 | 0/12 | 0 pass, 0 fail, 12 unresolved | incomplete |
| `conv0-cat3-second` | 3 | 0/12 | 0 pass, 5 fail, 7 unresolved | incomplete |
| `conv0-cat4-first` | 4 | 12/12 | 12/12 | 100.000% |
| `conv0-cat4-second` | 4 | 0/12 | 6 pass, 0 fail, 6 unresolved | incomplete |
| `conv0-cat5-first` | 5 | 12/12 | 12/12 | 100.000% |
| `conv0-cat5-second` | 5 | 12/12 | 12/12 | 100.000% |

## Blinding and reproducibility

- 120 run answers were reduced to 27 unique answer strings and 28 unique question/answer units.
- 16 units used frozen deterministic rules; 12 units remain unresolved.
- Arm, profile, reasoning effort, repetition, run ID, and sequence metadata are absent from the prepared blind judge packet.
- Deterministic shuffle seed SHA-256: `860f9ea021ab20b5221aabc2d98c089b418778a411187c43fd9db54e2bd38b3f`.
- Every raw JSONL SHA-256 and extracted answer matched the primary report.

## Judge cost

- Deterministic rules: `$0`.
- Accepted audit external-judge status: `exact_zero_no_call`.
- Accepted audit Claude CLI reported cost: `$0`.
- Prepared judge configuration: pinned `claude-haiku-4-5-20251001`, with tools and MCP servers disabled; it was not invoked for the accepted audit.

The accepted deterministic audit has exact external model cost of $0. Earlier discarded/failed judge attempts are disclosed separately and prevent exact aggregate experiment-cost reporting. Original Sol credits remain estimates from the primary report and are not recast as billed cost here.

### Discarded-attempt disclosure

Discarded/failed attempts: 3; exact aggregate provider telemetry was not retained, so the only defensible numeric bound is $0 to $0.15 from the sum of hard caps.

## Limitations

- This is a secondary, posthoc semantic audit and was not predeclared as the matrix qualification criterion.
- The primary exact-match failure remains authoritative and unchanged; the semantic result must not be substituted into the original qualification.
- Deterministic rules intentionally cover only narrow surface aliases and explicit category-5/uncertainty cases; unresolved cases are not coerced to pass or fail.
- The independent CLI judge result was not accepted because its raw telemetry was not retained after a local validator error; later attempts failed or were stopped, so this final artifact is intentionally incomplete.
- The accepted rubric-only audit made no external model call and therefore has exact $0 external-model cost. Aggregate cost across discarded/failed attempts is unknown and only bounded by the disclosed hard caps.
- The benchmark ground truth is treated as authoritative even when a counterfactual could reasonably be described as uncertain outside the benchmark.
- No independent-model decision is part of the accepted audit artifact.
- A finite 10-question, 120-run matrix does not establish universal semantic quality.
