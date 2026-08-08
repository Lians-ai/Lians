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
| Codex, GPT-5.6 Luna | Full conversation vs. Lians recall, same repeated LOCOMO question | both correct | 0.101385 to 0.048275 estimated credits, down 52.4% | 2.10x (+110.0%) | pass for this repeat |
| Claude Code, `fable` alias | Full conversation vs. manually injected top-20 artifact, tools/MCP disabled | both correct | $0.296701 to $0.029340 provider-reported cost, down 90.1% | 10.11x (+911.3%) | context-isolation pass only |
| Gemini CLI 0.54.4 | Checked-out Lians stdio connection and three-tool discovery | protocol/config only | no signed-in usage measurement | not measured | pending credentials |

The Codex credit values are local calculations using the documented Luna token
rates, not credits returned by a provider API. The machine-readable case labels
them `estimated_credits` and records the source, date, and rates used. Claude's
cost values are provider-reported by the signed-in CLI and remain labeled
`reported_cost`.

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

- `docs/benchmarks/codex-mcp-local-2026-08-08.md`
- `docs/benchmarks/codex-usage-extension-case-2026-08-08.json`
- `integrations/lians-plugin/benchmarks/results/claude-locomo-ab-2026-08-08.json`
- `integrations/lians-plugin/benchmarks/results/claude-usage-extension-case-2026-08-08.json`

## Product changes behind the target

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
  docs/benchmarks/codex-usage-extension-case-2026-08-08.json

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
