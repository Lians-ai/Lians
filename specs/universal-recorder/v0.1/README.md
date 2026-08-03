# Lians Universal Recorder v0.1

The Universal Recorder turns heterogeneous AI execution telemetry into an
append-only stream of normalized evidence events and correlated run or decision
boundaries. Its wire contract is
[`envelope.schema.json`](./envelope.schema.json). Normalized API output follows
[`event.schema.json`](./event.schema.json).

## API

- `POST /v1/recorder/events` accepts one envelope.
- `POST /v1/recorder/batch` accepts 1–500 mixed-protocol envelopes. `atomic`
  defaults to `true`; when false, mapping failures are returned by input index.
- `GET /v1/recorder/runs/{run_id}/events` returns an integrity-verified page in
  descending immutable `(recorded_at, id)` order. The legacy JSON array is
  retained; exact collection/page totals, completeness truth, and the paired
  `before_recorded_at`/`before_id` continuation are carried in `X-Lians-*`
  response headers. Omitting a continuation MUST NOT be interpreted as proof
  that a run with more than the requested limit has no additional events.
- `GET /v1/recorder/runs/{run_id}/readiness` explains receipt completeness.
- `GET /v1/recorder/readiness` reports time and remaining gaps to the first
  receipt-ready run visible to the caller.
- `GET /v1/recorder/indexing/jobs/{job_id}` reports exact progress for a frozen
  decision back-link snapshot.
- `GET /v1/recorder/indexing/decisions/{decision_id}` discovers that job from
  the authoritative decision returned by decision or OTLP ingestion.
- `POST /v1/recorder/indexing/jobs/{job_id}/retry` requeues a failed snapshot
  after an authorized operator remedies its stable failure.

Every endpoint uses the existing `X-API-Key` namespace, scope, information
barrier, PostgreSQL RLS, and audit-chain controls.

## Authenticated provenance and integrity

Envelope `actor` values are caller-reported claims. They never authenticate an
agent or principal. A normalized event therefore returns both the claimed
`agent_id` and an `actor_attribution` of `claimed_unverified` or `not_supplied`.
The distinct `ingested_by_principal_ref`, `ingested_by_auth_method`, and opaque
`ingested_by_credential_id` fields are derived by the server from the API-key or
OIDC credential that passed authentication. A credential ID is an identifier,
not secret credential material.

New events use `event_hash_version: 2`. The v2 canonical SHA-256 document binds
the event ID, tenant and barrier scope, run, source and normalized fields, both
time axes, correlations, capture policy, caller claims, and authenticated
ingestion provenance. Each event is also committed by exactly one
`recorder_ingest` entry in the core audit chain. Authoritative reads verify the
self-hash and that exact audit binding before returning an event.

Events written before authenticated provenance was introduced remain v1 and
are backfilled with `lians:principal:v1:legacy-unverified`, authentication
method `legacy_unverified`, and a null credential ID. These markers are an
explicit lack of authenticity evidence; they MUST NOT be interpreted as a
verified producer identity or silently upgraded to v2.

On PostgreSQL, `recorder_events` rejects `UPDATE`, `DELETE`, and `TRUNCATE` with
database triggers. The `lians_runtime` capability role retains only `SELECT`
and `INSERT` on this table, and forced namespace plus restrictive barrier RLS
remain active. Recorder-run producer lists are mutable, derived aggregates for
discovery. Event-level provenance and its audit binding are authoritative.

## Correlation

The recorder chooses the first stable identifier in this order: Decision ID,
explicit run ID, trace ID, task ID, session ID, context ID, tool-call ID, event
ID, then the source-payload hash. Identical identifiers only correlate inside
the same namespace and information-barrier scope. Propagating an explicit run
or trace ID across OTLP, MCP, and A2A gives the strongest cross-protocol join.

## Idempotency

A caller-supplied `idempotency_key` takes precedence. Otherwise the recorder
uses `event_id` when supplied, then the strongest protocol identity available:

- OTLP: trace ID + span ID
- MCP: correlated boundary + JSON-RPC/tool-call ID + request/response phase
- A2A: task/message identity + event kind + state
- Native Lians: event ID
- Last resort: canonical source-envelope hash

Deduplication is enforced by a database unique constraint, not only by an
application pre-check, so concurrent retries remain exactly-once.

## Decision evidence back-linking

Recorder events may arrive before their authoritative DecisionRecord. A
decision with at most 500 matching prior events indexes them synchronously in
the decision transaction. A larger boundary MUST NOT be rejected solely due to
its event count and MUST NOT index an undisclosed prefix. Lians instead commits
the decision and a durable job over the exact terminal `(recorded_at, event_id)`
keyset and exact event count. Its public representation follows
[`evidence-index-job.schema.json`](./evidence-index-job.schema.json).

The five Recorder-derived evidence kinds (`model`, `policy`, `tool`, `input`,
and `output`) remain `partial` with `recorder_index_pending` until every event in
the frozen snapshot has passed event-hash and original audit-binding checks and
the terminal cursor equals the stored boundary. Each bounded page commits
idempotent artifacts, links, coverage watermarks, counters, and cursor together.
Crashes may replay a page but cannot skip one. A terminal integrity or snapshot
failure keeps coverage partial with `recorder_index_failed`; the API exposes
only stable error codes, never payload or exception text.

PostgreSQL takes the existing namespace evidence-registration fence before the
same decision-scoped transaction lock for DecisionRecord insertion, Recorder
event insertion, and worker pages. The fixed order also makes multi-decision
OTLP batches deadlock-safe. An event committed
before decision creation is inside the frozen snapshot. An event that waits
behind the decision observes the committed DecisionRecord and indexes itself in
its own ingestion transaction. Events after the frozen boundary are therefore
not skipped and do not extend the immutable job snapshot.

## Data minimization

The default capture mode is `hash_only`. Common content-bearing fields—prompts,
messages, arguments, results, outputs, artifacts, and content—are replaced with
SHA-256 references before persistence. `metadata_only` omits those values and
does not derive hashes (explicit caller-supplied hashes are retained); `full`
retains content. Secret-like fields are always redacted in every mode.
Deployments can add field names with `capture.sensitive_fields`.

A deployment MAY reject an envelope that requests `full`, even though `full`
is valid in the wire schema. Lians does so unless the operator explicitly
enables full capture. Secret-shaped fields MUST be redacted before an enclosing
content hash is computed so a digest cannot become an offline credential oracle.
Dotted protocol fields are classified by their leaf name, so
`gen_ai.input.messages` and `http.request.header.authorization` receive the same
treatment as native Recorder fields.

Hashes prove equality, not meaning or correctness. Full content capture should
only be enabled under an explicit retention, encryption, and access policy.

## Receipt readiness

Each correlated boundary accumulates ten equally weighted capture dimensions:
agent, source time, correlation, model, input, output, terminal outcome, policy,
principal, and evidence/trace. Minimum receipt readiness requires a score of at
least 70 plus agent, source time, correlation, output, and terminal outcome.
This means “enough evidence to issue a useful receipt,” not “Grade A.” Missing
dimensions remain explicit and continue to improve as later events arrive.

## Extension policy

`extensions` is the portability escape hatch. Use reverse-DNS or registered
protocol keys such as `com.example.guardrail.result`, `gen_ai.model.id`, or
`lians.policy.version`. Unknown extension values are preserved and do not alter
the normalized core unless a mapping document says otherwise.

## Protocol mappings

- [OpenTelemetry GenAI](./mappings/opentelemetry-genai.md)
- [Model Context Protocol](./mappings/mcp.md)
- [Agent2Agent Protocol](./mappings/a2a.md)
