# RIAD-1 comparison

_Generated from commit `e5c899ae3ade0e98410eddc4e016a284344f8479` at 2026-07-25T04:38:07.067526+00:00._

| Check | Lians | Mem0 OSS | Graphiti OSS | Letta |
|---|:--:|:--:|:--:|:--:|
| Decision-time reconstruction | PASS | N/A | PARTIAL | N/A |
| Required decision provenance | PASS | N/A | N/A | N/A |
| Hashed evidence-pack export | PASS | N/A | N/A | N/A |
| Authenticated GenAI OTLP ingestion | PASS | N/A | N/A | N/A |
| Audit-payload tamper detection | PASS | N/A | N/A | N/A |
| Evidence-pack replay latency | PASS | NOT RUN | NOT RUN | NOT RUN |

**Evidence mode:** Lians was executed end-to-end. Mem0 OSS, Graphiti OSS, and Letta were capability-assessed against their public product surfaces. N/A means the RIAD operation is not exposed; NOT RUN means a latency number would be meaningless without that operation.

## Per-cell evidence

### Lians

- **Decision-time reconstruction — PASS (executed):** Executed by RIAD-1 against the public Lians API on ephemeral SQLite. [Source](https://github.com/Lians-ai/Lians/blob/master/agentmem/benchmarks/decision_reconstruction_eval.py)
- **Required decision provenance — PASS (executed):** Executed by RIAD-1 against the public Lians API on ephemeral SQLite. [Source](https://github.com/Lians-ai/Lians/blob/master/agentmem/benchmarks/decision_reconstruction_eval.py)
- **Hashed evidence-pack export — PASS (executed):** Executed by RIAD-1 against the public Lians API on ephemeral SQLite. [Source](https://github.com/Lians-ai/Lians/blob/master/agentmem/benchmarks/decision_reconstruction_eval.py)
- **Authenticated GenAI OTLP ingestion — PASS (executed):** Executed by RIAD-1 against the public Lians API on ephemeral SQLite. [Source](https://github.com/Lians-ai/Lians/blob/master/agentmem/benchmarks/decision_reconstruction_eval.py)
- **Audit-payload tamper detection — PASS (executed):** Executed by RIAD-1 against the public Lians API on ephemeral SQLite. [Source](https://github.com/Lians-ai/Lians/blob/master/agentmem/benchmarks/decision_reconstruction_eval.py)
- **Evidence-pack replay latency — PASS (executed):** Executed by RIAD-1 against the public Lians API on ephemeral SQLite. [Source](https://github.com/Lians-ai/Lians/blob/master/agentmem/benchmarks/decision_reconstruction_eval.py)

### Mem0 OSS

- **Decision-time reconstruction — N/A (capability_assessed):** No OSS as-of recall or exhaustive historical decision snapshot API. [Source](https://github.com/mem0ai/mem0)
- **Required decision provenance — N/A (capability_assessed):** No consequential-decision record with model, policy, cutoff, and cited evidence. [Source](https://github.com/mem0ai/mem0)
- **Hashed evidence-pack export — N/A (capability_assessed):** No decision evidence-pack export API. [Source](https://github.com/mem0ai/mem0)
- **Authenticated GenAI OTLP ingestion — N/A (capability_assessed):** No authenticated OTLP trace receiver. [Source](https://github.com/mem0ai/mem0)
- **Audit-payload tamper detection — N/A (capability_assessed):** No append-only payload hash-chain verifier. [Source](https://github.com/mem0ai/mem0)
- **Evidence-pack replay latency — NOT RUN (capability_assessed):** Not comparable because the evidence-pack operation is unavailable. [Source](https://github.com/mem0ai/mem0)

### Graphiti OSS

- **Decision-time reconstruction — PARTIAL (capability_assessed):** Bitemporal graph edges support historical graph state, but not a complete decision-time evidence boundary or cited decision snapshot. [Source](https://github.com/getzep/graphiti)
- **Required decision provenance — N/A (capability_assessed):** No first-class consequential-decision record with the RIAD provenance contract. [Source](https://github.com/getzep/graphiti)
- **Hashed evidence-pack export — N/A (capability_assessed):** No decision evidence-pack export API. [Source](https://github.com/getzep/graphiti)
- **Authenticated GenAI OTLP ingestion — N/A (capability_assessed):** No authenticated OTLP trace receiver. [Source](https://github.com/getzep/graphiti)
- **Audit-payload tamper detection — N/A (capability_assessed):** No append-only payload hash-chain verifier. [Source](https://github.com/getzep/graphiti)
- **Evidence-pack replay latency — NOT RUN (capability_assessed):** Not comparable because the evidence-pack operation is unavailable. [Source](https://github.com/getzep/graphiti)

### Letta

- **Decision-time reconstruction — N/A (capability_assessed):** Passage search has no as-of validity filter or exhaustive historical snapshot. [Source](https://docs.letta.com/api/typescript/resources/agents/subresources/passages)
- **Required decision provenance — N/A (capability_assessed):** No RIAD-compatible consequential-decision record and cited-evidence contract. [Source](https://docs.letta.com/api/typescript/resources/agents/subresources/passages)
- **Hashed evidence-pack export — N/A (capability_assessed):** No decision evidence-pack export API. [Source](https://docs.letta.com/api/typescript/resources/agents/subresources/passages)
- **Authenticated GenAI OTLP ingestion — N/A (capability_assessed):** No authenticated OTLP trace receiver. [Source](https://docs.letta.com/api/typescript/resources/agents/subresources/passages)
- **Audit-payload tamper detection — N/A (capability_assessed):** No append-only payload hash-chain verifier. [Source](https://docs.letta.com/api/typescript/resources/agents/subresources/passages)
- **Evidence-pack replay latency — NOT RUN (capability_assessed):** Not comparable because the evidence-pack operation is unavailable. [Source](https://docs.letta.com/api/typescript/resources/agents/subresources/passages)

