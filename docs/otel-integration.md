# Lians OpenTelemetry integration

Lians accepts authenticated OTLP/HTTP traces at `POST /v1/traces`. It supports
OTLP JSON in the base installation and protobuf with the `otel-receiver`
optional dependency.

The receiver stores an append-only normalized span, detects GenAI operations,
groups them by trace, and creates one idempotent Lians decision. The decision
contains the model identity, historical knowledge cutoff, evidence-memory
references, capture status, trace correlation, and an inference ledger event.

## Recommended topology

```text
Application -> Grafana Alloy -> Lians
                            \-> Grafana Cloud
```

Fan out before sampling. Configure queues, retries, batching, and memory
limiting in Alloy. Protect the Lians exporter with a write-scoped,
tenant-bound API key.

## Data policy

The default profile is metadata-only. Prompt, completion, memory, retrieval,
and tool-result content is opt-in because telemetry pipelines frequently have
broader readership and retention than the originating AI system.

See `integrations/grafana-lians-app/README.md` for the attribute contract,
deployment examples, dashboard, alerts, and packaging instructions.
