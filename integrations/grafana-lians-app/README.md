# Lians for Grafana

Lians connects to an existing Grafana OpenTelemetry pipeline without replacing
Grafana, Tempo, or Application Observability.

```text
AI application -> Grafana Alloy -> Grafana Cloud / Tempo
                              \-> Lians decision evidence
```

Grafana remains the operational view for traces, latency, errors, and tokens.
Lians receives the unsampled evidence stream, correlates GenAI traces into
decision records, and preserves memory, policy, provenance, and historical
cutoff information.

## Included

- Grafana app with guided Alloy configuration
- Hardened Alloy fan-out with memory limiting, batching, retries, and queues
- Lians Evidence Capture dashboard and alert rules
- Docker Compose and Kubernetes deployment examples
- Instrumented Python GenAI trace example
- OTLP JSON and protobuf receiver support
- Automatic idempotent trace-to-decision correlation

## Quick start

```bash
npm install
npm run typecheck
npm run build
```

Copy `demo/.env.example` to `demo/.env`, set the required values, and run
`docker compose up` from `demo/`. Open Grafana at `http://localhost:3000`.

Emit a test trace:

```bash
pip install opentelemetry-sdk opentelemetry-exporter-otlp-proto-http
python examples/emit_genai_trace.py
```

Applications send OTLP/gRPC to port `4317` or OTLP/HTTP to port `4318`.

## Required environment

| Variable | Purpose |
| --- | --- |
| `LIANS_OTLP_ENDPOINT` | Lians origin; the exporter appends `/v1/traces` |
| `LIANS_API_KEY` | Write-scoped key for one Lians namespace |
| `GRAFANA_CLOUD_OTLP_ENDPOINT` | Grafana Cloud OTLP endpoint |
| `GRAFANA_CLOUD_INSTANCE_ID` | Grafana Cloud basic-auth username |
| `GRAFANA_CLOUD_API_KEY` | Token with metrics-publish permission |

For protobuf ingestion, install the server with
`pip install 'lians[otel-receiver]'`. OTLP/HTTP JSON works with the base install.

## Correlation contract

Use official OpenTelemetry `gen_ai.*` attributes for model operations. Add only
the Lians fields the application can establish:

| Attribute | Meaning |
| --- | --- |
| `lians.decision.id` | Optional UUID; otherwise derived idempotently from the trace |
| `lians.decision.type` | Business decision type |
| `lians.decision.outcome` | Short outcome label, not hidden model reasoning |
| `lians.workflow.id` | Stable workflow identifier |
| `lians.memory.ids` | Memory UUID array or comma-delimited list |
| `lians.evidence.ids` | Additional evidence UUIDs |
| `lians.policy.version` | Evaluated policy version |
| `lians.knowledge.as_of` | Historical knowledge cutoff in ISO 8601 |
| `lians.capture.status` | `complete`, `complete_with_exclusions`, `partial`, `failed`, or `unverifiable` |
| `lians.workspace.id` | Customer workspace identifier |
| `lians.grafana.trace_url` | Optional direct link back to the Grafana trace |

Lians creates one decision per GenAI trace, records an inference ledger event,
links valid memory IDs, preserves unresolved references, and returns
`decisionIds` for OTLP/JSON. Re-sending the same trace is idempotent.

For bidirectional navigation, emit `lians.decision.id` and configure a Tempo
span-field data link for that attribute:

```text
https://<lians-studio>/decisions/${__value.raw}
```

Set `lians.grafana.trace_url` on the root span when the application knows the
Grafana/Tempo trace URL. Lians preserves it in decision metadata for the return
link.

## Privacy and sampling

Do not emit raw prompts, completions, memories, retrieved documents, or tool
results by default. Send identifiers, hashes, versions, provenance, token
counts, and capture status. Content capture is an explicit customer opt-in.

The supplied pipeline fans out before sampling. Never place a sampling
processor before the Lians exporter when claiming complete evidence capture.

## Distribution

Build output is written to `dist/`. Package it with:

```bash
mkdir -p package/lians-lians-app
cp -R dist/. package/lians-lians-app/
cd package
zip -r ../lians-lians-app.zip lians-lians-app
```

The `grafana-plugin-release.yml` workflow builds an archive for `grafana-v*`
tags. Public catalog distribution still requires Grafana Labs review and plugin
signing. Do not describe the plugin as catalog-listed until review succeeds.
