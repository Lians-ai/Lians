# Provider usage-extension validation

**Date:** 2026-08-08

## Target and decision rule

The product target is **85% more same-budget usage**, or 1.85 times as many
comparable tasks. That is not the same as an 85% token reduction. A candidate
meets the economic threshold when its measured per-task cost is no more than
`1 / 1.85 = 54.05%` of baseline, a reduction of at least 45.95%, and only when
the protected quality checks pass first.

`agentmem/benchmarks/provider_usage_extension.py` applies this rule to
provider-returned credits or cost, explicitly sourced cost estimates, or a
weighted token proxy. Reported and estimated values remain separate in its
output. Its verdict is scoped to the named provider and workload.

## Measured results

| Host | Comparison | Protected result | Per-task economic result | Same-budget usage | Target |
| --- | --- | --- | ---: | ---: | --- |
| Codex, `gpt-5.6-sol`, all six efforts/default | Ten frozen prompts, balanced A/B at each effort; 120 turns | primary matrix failed: 21/60 cells passed exact quality plus economics | pooled ratio 0.4502; worst cell ratio 4.0616 | pooled 2.22x (+122.1%) | pooled pass; every-prompt fail |
| Codex, `gpt-5.6-sol`, Ultra/default | Full conversation vs. Lians pre-model hook recall, ABBA | all four exact; real retrieval receipts; no tools/delegation | 3.249 to 1.5965 estimated credits, down 50.9% | 2.04x (+103.5%) | pass for this repeat (+80% and +85%) |
| Codex, GPT-5.6 Luna | Full conversation vs. Lians recall, same repeated LOCOMO question | both correct | 0.101385 to 0.048275 estimated credits, down 52.4% | 2.10x (+110.0%) | pass for this repeat |
| Claude Code, `fable` alias | Full conversation vs. manually injected top-20 artifact, tools/MCP disabled | both correct | $0.296701 to $0.029340 provider-reported cost, down 90.1% | 10.11x (+911.3%) | context-isolation pass only |
| Gemini CLI 0.54.4 | Checked-out Lians stdio connection and three-tool discovery | protocol/config only | no signed-in usage measurement | not measured | pending credentials |

The Codex credit values are local calculations using the documented Sol or Luna
token rates, not credits returned by a provider API. The machine-readable cases
label them `estimated_credits` and record the source, date, and rates used. Claude's
cost values are provider-reported by the signed-in CLI and remain labeled
`reported_cost`.

The six-effort Sol matrix is the broader Codex result and keeps its failed
primary verdict despite passing pooled economics. All 60 candidate retrievals
used a valid non-degraded prewarmed-daemon receipt; the maximum receipt latency
was 1.631 seconds. Candidate exact checks passed 35/60 versus 30/60 for baseline,
but only 24/60 paired cells passed the predeclared exact quality gate and only
21/60 passed both quality and the +80% economic threshold. The report therefore
does not support an every-prompt or universal quota claim. See the
[matrix summary](codex-sol-matrix-bge-onnx-v2-2026-08-08.md).

The posthoc semantic audit is intentionally non-qualifying and incomplete: it
resolved 87/120 answers (82 pass, 5 fail) and left 33 unresolved. It preserves the
primary exact-gate failure and must not be used to promote the pooled result.

The signed-in Sol Ultra run used the exact `gpt-5.6-sol` model, `ultra`
reasoning, and default service tier. Both baseline repeats reported 25,938
uncached input and 9 output tokens (3.249 estimated credits). Both hook
candidate repeats reported 12,718 uncached input and 9 output tokens (1.5965
estimated credits). No cache reads were observed, so the all-input-uncached
sensitivity is identical to the measured result. The verdict also requires the
selected repeat, pooled repeats, and every individual repeat to pass. All four
answers exactly matched `7 May 2023`; each candidate had one hash-only receipt
for real k=20, 768-token-bounded retrieval and made no model-facing tool call.
No delegation occurred, so this is evidence for a single non-delegated memory
task with Ultra selected, not aggregate subagent economics.

The hook candidate was slower locally: 20.5 seconds versus 3.5 seconds for the
selected baseline, with roughly 17 seconds spent loading the local BGE retrieval
runtime in a fresh process. This result supports usage extension, not a latency
improvement. Hosted or persistent prewarmed retrieval is the production latency
path.

That production path was subsequently implemented with a persistent local
daemon and a zero-output `SessionStart` prewarm. In the six-effort matrix, all 60
fresh candidate hook processes called the prewarmed daemon and the maximum
receipt latency was 1.631 seconds. Full daemon cold startup remained 6.574 to
12.152 seconds in the recorded runs, so the sub-3.5-second result applies after
session prewarm, not to true process-cold startup.

The preceding model-facing MCP ABBA was not promoted: its selected candidate
reached only +36.0% same-budget usage and its two-candidate mean reached +64.0%.
One repeat individually cleared the target only because prompt-cache reuse was
more favorable. The MCP path paid the large Codex prefix twice and generated
373–544 output tokens to produce a nine-token answer. The pre-model hook removes
that model-orchestration turn; the qualified hook result did not depend on cache
reads.

The Claude calls used 22,382 versus 2,144 input tokens, a 90.4% reduction. The
benchmark manually injected a stored retrieval artifact and invoked Claude with
`--tools ""`; it excludes plugin schemas, tool selection and call traffic,
runtime recall, and MCP output framing. Its 10.11x result is a context-isolation
upper bound, not end-to-end evidence for the installed Claude plugin. The
reduced call was also slower (5.4 seconds versus 3.3 seconds), so it does not
support a Claude latency-improvement claim. The Codex candidate used 67.9% fewer
uncached input tokens, but 3.4% more total input because its larger tool prefix
was mostly cached. Provider economics therefore cannot be inferred from raw
prompt length alone.

The full evidence is in:

- `docs/benchmarks/codex-sol-ultra-hook-ab-2026-08-08.json`
- `docs/benchmarks/codex-sol-matrix-bge-onnx-v2-report-2026-08-08.json`
- `docs/benchmarks/codex-sol-matrix-bge-onnx-v2-2026-08-08.md`
- `docs/benchmarks/codex-sol-ultra-usage-extension-case-2026-08-08.json`
- `docs/benchmarks/codex-sol-ultra-usage-extension-report-2026-08-08.json`
- `docs/benchmarks/codex-mcp-local-2026-08-08.md`
- `docs/benchmarks/codex-usage-extension-case-2026-08-08.json`
- `integrations/lians-plugin/benchmarks/results/claude-locomo-ab-2026-08-08.json`
- `integrations/lians-plugin/benchmarks/results/claude-usage-extension-case-2026-08-08.json`

## Product changes behind the target

- Codex can recall before the first model request with a score-gated
  `UserPromptSubmit` hook. The hook bounds context, labels memory as untrusted
  data, skips degraded retrieval, emits an optional hash-only receipt, and
  removes the second model turn required by model-selected MCP recall.
- An optional `<lians-query>...</lians-query>` hint separates the retrieval
  question from answer-format instructions without hiding either from Codex.
- MCP recall now compiles through `/v1/context` and returns at most 2,650
  estimated tokens by default after considering up to 50 memories.
- `LIANS_MCP_ENABLED_TOOLS` gives hosts a provider-neutral server-side allowlist;
  the ordinary profile exposes only `remember`, `recall`, and `recall_at`.
- Context metadata filters now work through the HTTP, async SDK, local SDK, and
  MCP paths.
- The exact-token context compiler accepts
  `target_usage_extension_ratio=1.85`, derives an effective context budget, and
  reports whether that context-only target was met.
- Local MCP startup imports the Windows-sensitive runtime before AnyIO, then
  warms the model/query path in the background. A direct cold stdio probe
  discovered the three tools in 11.6 seconds and completed its first real
  bounded recall in 4.53 seconds; a previously recorded warm recall was 246 ms.
- Claude and Gemini ship host-specific low-context instructions and configuration
  while using the same provider-neutral memory protocol.

The 2,650-estimated-token default was chosen near the recorded top-50 LOCOMO
mean (2,656 exact retrieval tokens; median 2,660; p95 3,150). The recorded 90.0%
judged score and 85.4% mean context reduction used all 50 retrieved memories,
not the production `/v1/context` renderer and cap. Those aggregate results
motivate the bounded profile but do not quality-validate it; the exact capped
profile needs its own representative answer-and-judge rerun.

## Integration status

Claude Code's official MCP support loads plugin `.mcp.json` servers
automatically, and its default Tool Search defers schemas until they are needed.
Gemini CLI supports stdio MCP servers and a per-server `includeTools` allowlist.
The Gemini examples were checked against the current published settings schema,
and an ephemeral CLI connected to the checked-out Lians server with only the
three core tools visible.

No Gemini binary, Gemini API key, Google API key, ADC identity, project, or
location was present on this machine. A Gemini model A/B would therefore be
fabricated if reported now. The readiness gate is one harmless authenticated
recall followed by the same paired quality-and-usage benchmark.

The checked-out SDK contains the bounded context and server-side filtering
changes, but the public `lians-sdk` 0.5.0 package predates them. Publish and pin
the new SDK before treating `uvx --from "lians-sdk[mcp]"` marketplace installs as
fully enforced. Gemini's `includeTools` still provides host-side three-tool
filtering with the older package.

Official host references:

- [Claude Code MCP and Tool Search](https://code.claude.com/docs/en/mcp)
- [Gemini CLI MCP configuration](https://geminicli.com/docs/tools/mcp-server/)
- [OpenAI Codex pricing and usage guidance](https://learn.chatgpt.com/docs/pricing)

## Reproduce

```console
python agentmem/benchmarks/provider_usage_extension.py \
  docs/benchmarks/codex-sol-ultra-usage-extension-case-2026-08-08.json

python agentmem/benchmarks/provider_usage_extension.py \
  docs/benchmarks/codex-usage-extension-case-2026-08-08.json

python agentmem/benchmarks/codex_sol_ultra_ab.py \
  --db /absolute/path/to/lians-locomo-codex.sqlite \
  --retrieval-path hook --dry-run

python agentmem/benchmarks/provider_usage_extension.py \
  integrations/lians-plugin/benchmarks/results/claude-usage-extension-case-2026-08-08.json

python integrations/lians-plugin/benchmarks/claude_locomo_ab.py --dry-run
```

## Claim boundary

Supported: Lians is a provider-neutral, token-bounded memory and governed
improvement layer that integrates with Codex, Claude, Gemini, and other MCP or
SDK hosts, and it can extend same-budget usage on measured workflows while
protected quality passes.

Not supported: Lians always extends account quota by 85%, always improves answer
quality or recall, always reduces total input tokens, or always improves latency.
Each provider/workload pair must pass the protected quality gate and the measured
economic threshold before promotion.
