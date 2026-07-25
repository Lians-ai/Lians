# Partner integration readiness

## Grafana Labs

Lians now implements authenticated OTLP/HTTP trace ingestion at `POST /v1/traces`.
It accepts OTLP JSON in the base install and OTLP protobuf when the
`otel-receiver` extra is installed. Spans containing OpenTelemetry GenAI
semantic attributes are identified and indexed by model; all spans are retained
without application-side sampling.

The Grafana app source and Alloy fan-out configuration live in
`integrations/grafana-lians-app`. The app has **not** been reviewed, signed, or
listed by Grafana Labs. Accurate external wording is:

> Lians has a working authenticated OTLP/HTTP receiver and a Grafana Alloy
> fan-out configuration. We are preparing the Lians app for Grafana's plugin
> review and catalog process.

Lians' database records are append-only at the application API layer and
hash-addressed. That is tamper-evidence, not by itself legal attestation or
certified WORM storage. Do not describe the OTLP store as legally immutable
unless the deployment's storage controls and attestation process have been
separately verified.

## ValidMind

Lians implements ValidMind's custom-integration reference API under `/api/v1`:

- `GET /health`
- `GET /models` and `GET /models/{id}`
- `PUT /models/{id}` for `vm_cuid` write-back
- `GET /tickets` and `GET /tickets/{id}`
- `GET /schema`
- `GET /resource-types`

Model records are derived from model IDs observed in decision records and GenAI
spans. Lians agents are exposed as `agent` resources. Memory conflicts are
exposed as finding/ticket records. Every request uses the existing `X-API-Key`
authentication system and requires an unbarriered key to prevent incomplete or
cross-barrier synchronization.

This is a custom integration implementation, not a claim that ValidMind ships a
built-in Lians connector.
