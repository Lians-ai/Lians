# Lians Universal Recorder v0.2

Recorder v0.2 is an additive evolution of v0.1. It retains the provider-neutral
Lians, OTLP GenAI, MCP, and A2A envelope while adding `operational` observations
for provider/runtime attribution, immutable agent and configuration references,
token usage, latency, finish/error state, attributed cost, and outcome linkage.

Every numeric observation carries explicit provenance. Values are evidence, not
guarantees: provider-reported values remain distinct from client measurements,
deterministic computations, human labels, model judgments, and estimates.

The service accepts v0.1 and v0.2 envelopes. Current SDK builders emit v0.2.
Capture minimization, authenticated ingestion provenance, append-only integrity,
idempotency, correlation, and deferred evidence-index behavior remain as defined
by v0.1. The wire contracts are
[`envelope.schema.json`](./envelope.schema.json) and
[`event.schema.json`](./event.schema.json).
