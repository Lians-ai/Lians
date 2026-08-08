# Codex MCP and PostgreSQL validation

**Date:** 2026-08-08

**Host:** Windows, local Codex desktop configuration

**Memory path:** checked-out `lians-sdk` MCP server, SQLite persistence,
Snowflake `snowflake-arctic-embed-l-v2.0` (1024 dimensions, offline cached)

**Tokenizer:** `o200k_base`, exact counts with `tiktoken` 0.13.0

## Result

The checked-out Lians server was installed in the user's global Codex MCP
configuration and exercised through the same stdio command, working directory,
environment, database, agent ID, and namespace that Codex will use after restart.

| Measurement | Result |
| --- | ---: |
| Cold MCP initialize + local runtime prewarm + tool discovery | 22.4 s |
| Warm semantic recall tool call | 246 ms |
| Core tools exposed to Codex | 3 |
| Canonical JSON size of the three core tool schemas | 514 tokens |
| Recalled persisted product-direction fact | pass |
| Codex app-server discovery and direct `lians.recall` | pass |
| GPT-5.6 Luna selected `lians.recall` and answered from it | pass |

The server itself provides eight tools. The installed Codex allow-list exposes
only `remember`, `recall`, and `recall_at` for ordinary work; reconstruction and
investigation tools remain available as an opt-in evidence profile. `remember`
advertises a write annotation, while all seven read tools advertise read-only,
non-destructive, idempotent annotations so hosts can apply appropriate approval
policy.

During the test, Windows revealed that lazy embedded-runtime imports after the
MCP host started its AnyIO workers could make the first tool call exceed its
timeout. The server now prewarms local memory before accepting protocol traffic
and shuts down its local executor cleanly. A deterministic test embedder was used
only to isolate that defect; the final recorded result above used the benchmarked
Snowflake model.

The restart test also exposed a host-readiness race: with the server marked
optional, a fresh Luna turn completed before the roughly 16-second local prewarm
and reported that recall was unavailable. Setting `required = true` made Codex
wait for tool discovery. The next turn called `lians.recall`, received the
persisted fact, and answered from it. The shipped Codex example now enables that
readiness gate.

## Context-token evidence

The existing LOCOMO token analysis was rerun over all 1,540 judged-run retrieval
artifacts. It compares the exact answer-context tokens read by the model with the
full conversation baseline.

| Answer context | Judged accuracy from the recorded run | Mean memory tokens | Share of 18,218-token full context |
| --- | ---: | ---: | ---: |
| Lians top-10 | 83.4% | 549 | 3.0% |
| Lians top-20 | 87.3% | 1,083 | 5.9% |
| Lians top-50 | 90.0% | 2,656 | 14.6% |
| Lians top-200 | 92.9% | 10,283 | 56.4% |

Adding the 514-token canonical core-tool schema payload gives a conservative
context-component comparison of 1,063 tokens at top-10, 3,170 at top-50, and
10,797 at top-200: respectively 94.2%, 82.6%, and 40.7% below the full-context
baseline. Codex may serialize tools differently, so these totals are not asserted
as its private internal prompt counts.

## Signed-in Codex credit A/B

A signed-in GPT-5.6 Luna test asked the same LOCOMO question in two modes. The
baseline injected the complete conversation. The Lians mode supplied no
conversation and called `recall(k=10)` against the persisted conversation. Both
returned the gold date, **7 May 2023**. Each row below is the second identical run
so both paths had an opportunity to use prompt caching.

| Mode | Total input | Cached input | Uncached input | Output | Estimated credits | Result |
| --- | ---: | ---: | ---: | ---: | ---: | --- |
| Complete conversation | 28,275 | 8,960 | 19,315 | 11 | 0.1014 | correct |
| Lians top-10 recall | 29,245 | 23,040 | 6,205 | 191 | 0.0483 | correct |

At the documented Luna rates of 5 credits per million uncached input tokens,
0.5 per million cached input tokens, and 30 per million output tokens, the Lians
run used **52.4% fewer estimated credits** and **67.9% fewer uncached input
tokens** for this answered prompt. The calculation treats reasoning tokens as
part of the reported output-token total.

This is promising but deliberately narrow evidence. Total reported input tokens
were 3.4% higher on the Lians path because Codex also sent a larger cached tool
prefix. The fresh-process Lians run took 23.4 seconds versus 4.1 seconds for the
full-context baseline because it cold-loaded the local embedding model; the
separate persistent-server measurement was 246 ms once warm. Prompt caching,
installed tool inventory, model choice, and server lifetime materially affect
the account result, so this one correct-pair A/B does not establish a universal
per-prompt credit or latency guarantee.

At the current [Codex pricing](https://learn.chatgpt.com/docs/pricing) rate for
GPT-5.6 Sol of 125 credits per million uncached input tokens,
the arithmetic input-context component is about 2.28 credits for the full 18,218
tokens versus 0.13 credits for top-10 plus the three canonical schemas. This is
not a full-message credit prediction: cached input, repository context, reasoning,
tool traffic, output tokens, model choice, and speed mode also contribute. Codex
does not expose a per-MCP-server credit counter. Codex loads local servers from
its [MCP configuration](https://learn.chatgpt.com/docs/extend/mcp), and the current
desktop process must restart before it can load a newly added server.

Reproduce the token analysis:

```console
uv run python agentmem/benchmarks/locomo_tokens.py \
  --pred memory-benchmarks/results/locomo/predicted_lians_arctic
```

## Real PostgreSQL gate

A separate fresh database on PostgreSQL 16.14 with pgvector 0.8.6 was migrated
through `0064_agent_improvement_plane`. Two new PostgreSQL-specific tests passed,
covering all 30 improvement tables, forced namespace/barrier RLS, append-only
triggers, least-privilege runtime grants, role attributes, and cross-barrier
isolation. The existing real-pgvector suite also passed 11/11.

The pre-existing persisted development database reported an unknown historical
Alembic revision (`0030_quota_policy_reservations`). It was left untouched; the
fresh validation database was used instead. This is evidence of a clean-install
gate, not an assertion that an unrelated divergent deployment can be upgraded
without reconciliation.

## Claim boundary

This evidence supports saying that Lians is an integratable, provider-neutral
memory and governed-improvement layer that can reduce context tokens while
meeting measured quality thresholds. It does not support a universal guarantee
that installing Lians improves every answer, lowers every prompt's credits, or
reduces end-to-end latency for every model and workload. Those outcomes must be
measured A/B on the protected customer workflow and promoted only when its
quality, safety, latency, and cost gates pass.
