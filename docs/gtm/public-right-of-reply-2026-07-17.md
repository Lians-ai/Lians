# Public vendor right of reply, July 17, 2026

These posts ask each evaluated vendor to check its own column in the Lians
regulated-memory evaluation. The response window closes July 31, 2026.
Corrections remain welcome after that date.

Common links:

- Results and methodology: <https://github.com/Lians-ai/Lians/blob/master/docs/regulated-eval-results.md>
- Runnable adapters: <https://github.com/Lians-ai/Lians/tree/master/agentmem/benchmarks/adapters>

## Mem0

Title: `Fact check request for Mem0's regulated-memory evaluation results`

We executed Mem0 OSS in our open regulated-memory evaluation and would like
the Mem0 team to review its column before we promote the results more widely.

The evaluation tests five properties: stale-revision suppression,
point-in-time recall, provable erasure, lookahead protection, and historical
audit-state reconstruction. Lians runs through the same harness.

Mem0's results, methodology, and per-cell evidence are public:

- https://github.com/Lians-ai/Lians/blob/master/docs/regulated-eval-results.md
- https://github.com/Lians-ai/Lians/blob/master/agentmem/benchmarks/adapters/mem0_adapter.py

We used Mem0 OSS 2.0.11 with the documented OpenAI configuration. We pinned
`gpt-4o-mini` after reporting a default-model compatibility issue in #6085,
so the evaluation would not score Mem0 on a failed setup.

If we missed an API or configuration that changes a cell, please share it.
We will rerun it, publish the result wherever it lands, and credit the
correction. Adapter pull requests are also welcome.

Please respond by July 31, 2026 if possible. Corrections remain welcome after
that date.

## Graphiti

Title: `Fact check request for Graphiti's regulated-memory evaluation results`

We executed Graphiti OSS in our open regulated-memory evaluation and would
like the Graphiti team to review its column before wider promotion.

The live run credited Graphiti's contradiction invalidation and backdated
`invalid_at` behavior. The main question is whether default search should
exclude invalidated edges when results are used as agent context, and whether
we have fairly distinguished validity windows from two-axis point-in-time
recall.

- https://github.com/Lians-ai/Lians/blob/master/docs/regulated-eval-results.md
- https://github.com/Lians-ai/Lians/blob/master/agentmem/benchmarks/adapters/graphiti_oss_adapter.py

The run used Graphiti OSS 0.29.2 with OpenAI clients and embedded Kuzu. The
Kuzu accommodations are documented in #1258.

If a supported API or configuration changes any cell, please share it. We
will rerun it, publish the result wherever it lands, and credit the
correction. Adapter pull requests are welcome.

Please respond by July 31, 2026 if possible. Corrections remain welcome after
that date.

## Letta

Title: `Fact check request for Letta's regulated-memory evaluation column`

We included Letta in an open evaluation of five regulated-memory properties:
stale-revision suppression, point-in-time recall, provable erasure, lookahead
protection, and historical audit-state reconstruction. Lians runs through the
same harness.

Letta's current column is capability-assessed from its public API rather than
executed live. We would like the Letta team to check that assessment:

- https://github.com/Lians-ai/Lians/blob/master/docs/regulated-eval-results.md
- https://github.com/Lians-ai/Lians/blob/master/agentmem/benchmarks/adapters/letta_adapter.py

The evaluation is intentionally about turnkey compliance primitives, not a
general ranking of agent architectures. If we missed an API or mischaracterized
agent-managed memory, please share the correction. We will update the column,
credit the correction, and run a vendor-supplied configuration where possible.

Please respond by July 31, 2026 if possible. Corrections remain welcome after
that date.

## Hindsight

Title: `Fact check request for Hindsight's regulated-memory evaluation column`

We included Hindsight in an open evaluation of five regulated-memory
properties and would like the Hindsight team to review its capability-assessed
column:

- https://github.com/Lians-ai/Lians/blob/master/docs/regulated-eval-results.md
- https://github.com/Lians-ai/Lians/blob/master/agentmem/benchmarks/adapters/hindsight_adapter.py

The specific question we most want checked is erasure. Our review found no
public deletion API, so the current adapter scores that primitive as absent.
If that API exists or has since been added, please point us to it. We will
correct the adapter, rerun the evaluation, publish the result wherever it
lands, and credit the correction.

Any other per-cell corrections or vendor-supplied configurations are welcome.
Please respond by July 31, 2026 if possible. Corrections remain welcome after
that date.

## Supermemory

Title: `Fact check request for Supermemory's regulated-memory evaluation column`

We included Supermemory in an open evaluation of five regulated-memory
properties: stale-revision suppression, point-in-time recall, provable
erasure, lookahead protection, and historical audit-state reconstruction.
Lians runs through the same harness.

Supermemory's current column is capability-assessed from its public API. Its
profile consolidation receives partial credit for supersession, while the
remaining cells reflect the public API surface we found:

- https://github.com/Lians-ai/Lians/blob/master/docs/regulated-eval-results.md
- https://github.com/Lians-ai/Lians/blob/master/agentmem/benchmarks/adapters/supermemory_adapter.py

If we missed an API or configuration that changes a cell, please share it.
We will update the adapter, run a vendor-supplied configuration where
possible, publish the result wherever it lands, and credit the correction.

Please respond by July 31, 2026 if possible. Corrections remain welcome after
that date.

## Publication log

| Vendor | Public channel | Sent | Response | Action |
|---|---|---|---|---|
| Mem0 | [Discussion 6373](https://github.com/mem0ai/mem0/discussions/6373) | 2026-07-17 | Pending | Pending |
| Graphiti | [Issue 1664](https://github.com/getzep/graphiti/issues/1664) | 2026-07-17 | Pending | Pending |
| Letta | [Issue 3402](https://github.com/letta-ai/letta/issues/3402) | 2026-07-17 | Pending | Resubmitted through the required feature form after issue 3401 was automatically closed for missing disclosure fields |
| Hindsight | [Discussion 2790](https://github.com/vectorize-io/hindsight/discussions/2790) | 2026-07-17 | Pending | Pending |
| Supermemory | [Issue 1303](https://github.com/supermemoryai/supermemory/issues/1303) | 2026-07-17 | Routed to internal ticket ENG-1070 | Publicly acknowledged the handoff and offered to rerun a vendor-supplied configuration |
