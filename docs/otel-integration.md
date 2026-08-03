# Lians OpenTelemetry integration

Lians accepts authenticated OTLP/HTTP traces at `POST /v1/traces`. The base
installation supports both standard OTLP JSON and protobuf so a stock
OpenTelemetry Collector can export without a custom adapter.

The receiver stores an append-only normalized span, detects GenAI operations,
groups them by trace, and creates one idempotent Lians decision. The decision
contains the model identity, historical knowledge cutoff, evidence-memory
references, capture status, trace correlation, and an inference ledger event.
The derived DecisionRecord stores the authenticated OTLP credential provenance;
the telemetry agent name remains a claimed label. Its v2 record hash is bound
to a minimal `decision_recorded` audit event in the same transaction.

## Recommended topology

```text
Application -> Lians Collector gateway -> Lians
                                    \-> optional observability backend
```

Never sample the evidence branch. `k8s/otel-collector.yaml` supplies a
two-replica gateway with batching, memory limiting, indefinite exponential
retry, and a persistent file-backed sending queue. Protect its exporter with a
dedicated, write-only, tenant-bound API key; do not reuse an application key.

The supplied collector queues the received OTLP request before the Lians API can
apply capture policy. Its PVC can therefore contain raw prompts, completions,
tool arguments/results, attributes, identifiers, and accidentally supplied
secrets. `OTLP_CAPTURE_MODE=hash_only` protects Lians database persistence; it
does **not** retroactively minimize collector queue bytes.

Send standard OTLP to the in-cluster service:

```bash
export OTEL_EXPORTER_OTLP_ENDPOINT=http://lians-otel-collector.agentmem.svc:4318
export OTEL_EXPORTER_OTLP_PROTOCOL=http/protobuf
```

Namespaces that may send telemetry must be explicitly labeled
`lians.ai/telemetry-access=true`. Collector metrics are exposed on port 8888
only to the `monitoring` namespace by the supplied NetworkPolicy.

## Queue confidentiality and custody

The production Helm chart refuses to enable this gateway unless the operator:

- names a non-default StorageClass whose CSI/provider configuration is verified
  to encrypt volumes, snapshots, replicas, and backups with approved key custody;
- explicitly acknowledges raw pre-minimization payload custody; and
- records a non-secret policy/control reference covering access, retention,
  incident hold, snapshot/backup scope, attachment monitoring, and deletion.

Those values are attestations by the operator, not cloud evidence. Restrict
RBAC/IAM for PVC read, attach, snapshot, restore, backup, and delete operations.
The StatefulSet retains claims on scale-down/deletion to avoid destroying
unaccepted evidence during an outage; an owner must drain, hold, or securely
delete each orphaned claim within the approved maximum custody period. Monitor
queue age, bytes, enqueue failures, unexpected attachments, snapshot creation,
and encryption-key changes. Treat support bundles and node/CSI access as part of
the same raw-data boundary.

For stricter confidentiality, minimize or encrypt sensitive attributes in the
producer before OTLP transport. A future pre-queue minimizing gateway could
reduce this boundary, but the supplied generic collector does not make that
claim.

## Lians persistence policy

`OTLP_CAPTURE_MODE=hash_only` is the deployment default. Prompt, completion,
message, input/output, artifact, tool-argument, and tool-result values are
replaced by deterministic SHA-256 references before persistence and before
decision correlation inside Lians. Secret-shaped fields are redacted before
hashing there; raw bytes may already exist in the collector queue as described
above.

`metadata_only` omits recognized content rather than hashing it. `full` remains
blocked unless `RECORDER_ALLOW_FULL_CAPTURE=true`; production startup fails
closed on an invalid or contradictory capture configuration. Full capture
should only be enabled when the storage, retention, access, and encryption
policy has been reviewed for the actual payloads.

The application endpoint applies the same policy even when clients bypass the
gateway, so direct OTLP cannot become a privacy-policy escape hatch.

The endpoint also applies two all-or-nothing transaction ceilings:
`OTLP_MAX_SPANS_PER_REQUEST` (2,000 by default) and
`OTLP_MAX_GENAI_TRACES_PER_REQUEST` (500 by default). The second ceiling bounds
the number of authoritative decision records that correlation may derive in one
request. A request above either limit receives HTTP 413 with
`spans_committed=false`; the collector must split and retry the export. Together
with `MAX_REQUEST_BODY_BYTES`, these limits bound both serialized input and
database fan-out without silently sampling spans.

The authenticated credential also supplies the span's namespace and information
barrier; clients cannot assert either in OTLP attributes. PostgreSQL combines
application filtering with forced namespace RLS, a restrictive barrier policy,
and scope-aware `(namespace, barrier, trace_id, span_id)` deduplication. An
explicit trusted NULL is shared only within its namespace. Pre-barrier historical
rows are conservatively labeled `__legacy_restricted__` and remain visible only
to an explicit unbarriered compliance/admin context. Do not infer or rewrite a
legacy scope without immutable evidence of the exact ingestion-time boundary.

See `integrations/grafana-lians-app/README.md` for the attribute contract,
deployment examples, dashboard, alerts, and packaging instructions.
