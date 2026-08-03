# Partner integration readiness

## Grafana Labs

Lians now implements authenticated OTLP/HTTP trace ingestion at `POST /v1/traces`.
It accepts OTLP JSON in the base install and OTLP protobuf when the
`otel-receiver` extra is installed. Spans containing OpenTelemetry GenAI
semantic attributes are identified and indexed by model; all spans are retained
without application-side sampling. GenAI spans are grouped by trace and
correlated automatically into idempotent decision records containing capture
status, model, historical cutoff, evidence references, and an inference ledger
event.

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

Model records come from an exact, transactionally maintained inventory of model
IDs observed in decision records and GenAI spans; API reads never rescan source
telemetry. One model ID may produce multiple records when it was observed behind
different information barriers. Each record exposes a random opaque
`metadata.lians_scope_id`; neither the resource ID nor metadata reveals the raw
barrier name. Lians agents are exposed as `agent` resources. Memory conflicts
are exposed as finding/ticket records. Every request uses the existing
`X-API-Key` authentication system and requires an unbarriered key to prevent
incomplete or cross-barrier synchronization.

`GET /models` applies `resource_type`, deterministic
`(resource_type, name, opaque scope)` ordering, `offset`, and `limit` (default
100; maximum 250; maximum offset 50,000) in SQL before records are hydrated.
Opaque single-ID reads and write-back existence checks use indexed inventory and
alias predicates instead of enumerating the tenant catalog. Model version
metadata is bounded to the first 100 lexical values and reports the exact
`versions_total`, `versions_limit`, and `versions_complete`; decision and span
counts are exact.

The 0.5 model ID includes the opaque scope. A namespace-wide 0.4.2 model ID is
accepted only when it resolves to one current scope; if the same legacy ID spans
multiple barriers, lookup and write-back return `409` and the caller must use the
scoped ID. During the 0.4.2/0.5 rolling window, uniquely resolvable legacy and
scoped `vm_cuid` rows are synchronized in both directions. Agent IDs retain their
0.4.2 form.

This is a custom integration implementation, not a claim that ValidMind ships a
built-in Lians connector.
