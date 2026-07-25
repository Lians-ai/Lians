# RIAD-1: Reconstruction of Immutable Agent Decisions

[![RIAD-1 CI](https://github.com/Lians-ai/Lians/actions/workflows/riad-1.yml/badge.svg)](https://github.com/Lians-ai/Lians/actions/workflows/riad-1.yml)

RIAD-1 is Lians' first decision-evidence benchmark. It asks one concrete
question: after an AI system makes a consequential decision, can Lians produce
the evidence needed to reconstruct what happened and detect later tampering?

## Scenario

The fixture models a credit-underwriting agent and the technical record needed
for an ECOA/adverse-action review: what evidence was available to the system
when it made a specific credit decision, and what reason codes supported the
outcome. It records two facts available before approval, one fact learned
afterward, one OpenTelemetry GenAI span, and one decision linked to the
evidence, model, policy, session, and input/output hashes. This is an
engineering benchmark, not a legal-compliance determination.

The benchmark exports the decision evidence pack and checks:

| Check | Pass condition |
|---|---|
| Point-in-time reconstruction | Both prior facts appear; the later fact does not |
| Cited evidence fidelity | Exported evidence IDs exactly match the decision |
| Provenance coverage | 10/10 required decision fields are populated |
| Evidence-pack integrity | Recomputed SHA-256 equals the exported pack hash |
| OTLP GenAI ingestion | The authenticated receiver accepts the span |
| Tamper detection | Modifying an audit event payload changes verification to `tampered` |
| Replay latency | Local evidence-pack P95 is below 3 seconds |

## Run it

From the repository root:

```bash
python agentmem/benchmarks/decision_reconstruction_eval.py
```

The command emits a machine-readable JSON report and exits nonzero if any check
fails. It runs offline against an ephemeral SQLite database and does not alter
development or production data.

## Comparison

RIAD-1 distinguishes executed results from capability assessments. Lians is
executed end-to-end. Mem0 OSS, Graphiti OSS, and Letta are scored as N/A where
their public product surface does not expose the RIAD operation; Graphiti
receives partial credit for historical bitemporal graph state.

```bash
python agentmem/benchmarks/riad_comparison.py --write
```

See the [commit-linked comparison](riad-1-comparison.md) and
[machine-readable receipt](riad-1-results.json). CI reruns the benchmark on
relevant changes and every Monday; each run uploads receipts named for the
exact Git commit.

## Claims this benchmark supports

- Lians can reconstruct the recorded knowledge state for this fixture at the
  decision timestamp.
- Lians can export the decision, cited evidence, provenance fields, audit-chain
  verification, and a hash for the resulting evidence pack.
- Lians accepts authenticated OTLP/HTTP GenAI spans in this tested JSON fixture.
- The Lians v2 hash chain detects the deliberate audit-payload mutation in this
  test.

## Claims this benchmark does not support

- Certified WORM storage, legal attestation, or compliance certification.
- Historical v1 audit rows did not hash `EventLog.payload`. They remain
  verifiable under the versioned format; all newly written v2 rows hash a
  canonical JSON representation of the payload.
- Production latency, throughput, or durability; the reported latency is a
  local SQLite development measurement.
- Universal reconstruction of arbitrary model behavior. Exact model-output
  replay also requires the caller to preserve model/provider determinism and
  any artifacts not submitted to Lians.
- Automatic OTLP trace-to-decision linking. This fixture explicitly records the
  trace and span IDs in decision metadata.
