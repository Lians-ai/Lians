# ContinuityBench v0.1

Status: proposed public contract, 2026-08-19.

ContinuityBench measures whether useful, current project state survives a fresh
AI coding task or a switch between tools. It is deliberately narrower than a
general conversational-memory benchmark.

## Questions it tests

1. Can a fresh agent recover the facts required to continue?
2. Does it exclude a decision that has been corrected or superseded?
3. Can the user inspect and repair saved state?
4. Does confirmed deletion remove the selected content from future recall?
5. Can the system identify the records that shaped the context?
6. Is the handoff bounded rather than a replay of the full transcript?

## Core scenarios

| ID | Scenario | Required observation |
|---|---|---|
| `CB-01` | Fresh session, same tool | Required current facts recovered without transcript replay |
| `CB-02` | Fresh session, different tool | Required current facts recovered across supported clients |
| `CB-03` | Superseded decision | Replacement returned; stale value not presented as current |
| `CB-04` | Unfinished work | Completed work, open work, and next action distinguished |
| `CB-05` | Correction | User can replace one selected memory and inspect the result |
| `CB-06` | Confirmed deletion | Deleted content is absent from later current recall |
| `CB-07` | Provenance | Returned context identifies its source records or stable hashes |
| `CB-08` | Boundedness | Context size and full-replay baseline are both reported |

## Scoring

Report every dimension separately before reporting a total:

| Dimension | Weight | Measurement |
|---|---:|---|
| Continuity accuracy | 30 | Required facts correctly recovered |
| Freshness | 25 | Stale facts presented as current |
| User repair | 15 | Inspect and correction workflow completes |
| Deletion | 15 | Confirmed target is absent from future current recall |
| Provenance | 10 | Selected records have stable source evidence |
| Boundedness | 5 | Selected context versus full replay |

An unsupported feature is `UNSUPPORTED`. A scenario that was not executed is
`NOT_RUN`. Neither may be silently converted to zero or a failed result.

## Fair-run protocol

- Use one synthetic repository, task, transcript, and expected-answer file.
- Freeze product, model, adapter, operating-system, and dependency versions.
- Give every system its documented recommended configuration.
- Separate deterministic memory-layer results from nondeterministic model output.
- Run model-mediated conditions at least five times and publish every run.
- Report latency and token counts as observations, not universal promises.
- Preserve raw machine-readable output and the commands required to reproduce it.
- Allow vendors and independent reviewers to submit corrections or adapters.

The benchmark must not infer product behavior from marketing pages. A competitor
result can be published only after running its supported path or receiving a
reproducible result from its maintainer.

## Current Lians fixture

The existing synthetic Claude-to-Codex fixture reports 10/10 expected continuity
facts, zero stale facts presented as current, and a 231-token handoff. That result
is a deterministic fixture result, not a completed ContinuityBench leaderboard
and not a guarantee for arbitrary live sessions.

Reproduce it through
[`experiments/cross-agent-continuity`](../../experiments/cross-agent-continuity/README.md).
The next benchmark release must add versioned competitor adapters, repeated live
agent runs, machine-readable reports, and an independent methodology review.

## Corrections

Methodology or result corrections belong in the
[methodology correction issue form](https://github.com/Lians-ai/Lians/issues/new?template=methodology_correction.yml).
Every public comparison should include a last-run date and link to its raw report.
