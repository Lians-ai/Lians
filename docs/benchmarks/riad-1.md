# RIAD-1: Reconstruction of Immutable Agent Decisions

RIAD-1 is Lians' first decision-evidence benchmark. It asks one concrete
question: after an AI system makes a consequential decision, can Lians produce
the evidence needed to reconstruct what happened and detect later tampering?

## Scenario

The fixture models a credit-underwriting agent. It records two facts available
before approval, one fact learned afterward, one OpenTelemetry GenAI span, and
one decision linked to the evidence, model, policy, session, and input/output
hashes.

The benchmark exports the decision evidence pack and checks:

| Check | Pass condition |
|---|---|
| Point-in-time reconstruction | Both prior facts appear; the later fact does not |
| Cited evidence fidelity | Exported evidence IDs exactly match the decision |
| Provenance coverage | 10/10 required decision fields are populated |
| Evidence-pack integrity | Recomputed SHA-256 equals the exported pack hash |
| OTLP GenAI ingestion | The authenticated receiver accepts the span |
| Tamper detection | Modifying a hash-covered audit field changes verification to `tampered` |
| Replay latency | Local evidence-pack P95 is below 3 seconds |

## Run it

From the repository root:

```bash
python agentmem/benchmarks/decision_reconstruction_eval.py
```

The command emits a machine-readable JSON report and exits nonzero if any check
fails. It runs offline against an ephemeral SQLite database and does not alter
development or production data.

## Claims this benchmark supports

- Lians can reconstruct the recorded knowledge state for this fixture at the
  decision timestamp.
- Lians can export the decision, cited evidence, provenance fields, audit-chain
  verification, and a hash for the resulting evidence pack.
- Lians accepts authenticated OTLP/HTTP GenAI spans in this tested JSON fixture.
- The Lians hash chain detects the deliberate mutation to the protected audit
  operation field in this test.

## Claims this benchmark does not support

- Certified WORM storage, legal attestation, or compliance certification.
- Tamper detection for `EventLog.payload`. The current row hash covers identity,
  operation, memory/content references, timestamp, and chain linkage, but not
  the JSON payload.
- Production latency, throughput, or durability; the reported latency is a
  local SQLite development measurement.
- Universal reconstruction of arbitrary model behavior. Exact model-output
  replay also requires the caller to preserve model/provider determinism and
  any artifacts not submitted to Lians.
- Automatic OTLP trace-to-decision linking. This fixture explicitly records the
  trace and span IDs in decision metadata.
