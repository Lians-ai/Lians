# Runtime scale and completeness contracts

## Recorder evidence back-link snapshots

Decision creation synchronously indexes at most 500 Recorder events that were
committed before the DecisionRecord. Above that exact count, the authoritative
decision commits with one durable `recorder_evidence_index_jobs` row over the
frozen terminal `(recorded_at, event_id)` keyset. The possible Recorder-derived
evidence kinds remain `partial` with `recorder_index_pending`; signed receipts
and Gate evaluation therefore cannot interpret the not-yet-indexed prefix as a
complete normalized boundary.

Every worker page is bounded (100 events by default), batch-verifies event hashes
and original audit bindings, bulk-upserts artifacts and direct links, and commits
the page cursor, exact counters, and coverage hash chain in the same transaction.
PostgreSQL `SKIP LOCKED` leases, deterministic backoff, lease expiry accounting,
stable error codes/digests, attempt limits, and manual authenticated retry make
the queue recoverable across replica or process failure. A decision-scoped
database trigger fence orders concurrent DecisionRecord and RecorderEvent
inserts: events before the decision fall within the snapshot; later events see
the committed decision and index synchronously without moving the frozen bound.

Operators should alert on `lians_recorder_evidence_index_jobs{status="failed"}`,
a stale `lians_recorder_evidence_index_worker_healthy`, or sustained growth in
`lians_recorder_evidence_index_oldest_active_age_seconds`. Inspect exact status
through `GET /v1/recorder/indexing/jobs/{job_id}` and retry only after repairing
the stable integrity, capacity, or database cause. Error responses and metrics
never contain tenant identifiers or Recorder payloads.

Lians bounds hot-path database work and states when a response is only a page,
candidate window, or budget-capped search. A caller must preserve these fields
with any exported or signed evidence. Absence from a partial response is not
evidence of absence.

Recorder run-event arrays expose an exact filtered total and page truth in
`X-Lians-*` headers. Traverse the descending `(recorded_at, id)` keyset with
paired `before_recorded_at` and `before_id`; each page verifies event hashes and
all original audit bindings in bounded bulk queries rather than one query per
event. The Python and TypeScript SDK `recorder_run_events_page` /
`recorderRunEventsPage` methods validate those headers before returning items.
The page first selects only its immutable IDs, timestamps, and exact collection
total. Before any normalized payload or extension JSON is hydrated, portable
400-ID queries inventory its stored JSON character lengths. A conservative
UTF-8/Python-materialization multiplier plus fixed scalar-row overhead must fit
`CONTENT_EXPORT_PAGE_BYTES_LIMIT`; otherwise the whole request returns HTTP 413
`recorder_event_page_capacity_exceeded` with `events_materialized=false`.
Accepted pages are then hydrated and integrity-verified in bounded chunks while
retaining the same keyset order, total, and completeness semantics.

Decision evidence creation is complete-or-error. Candidate extraction stops
before exceeding either `DECISION_EVIDENCE_CANDIDATE_LIMIT` (5,000 by default)
or `DECISION_EVIDENCE_CANDIDATE_BYTES_LIMIT` (16 MiB by default), then returns
HTTP 413 `decision_evidence_candidate_capacity_exceeded` and commits no
decision, evidence prefix, coverage claim, audit entry, or derived usage event.
Accepted candidate sets are deduplicated and written in bounded bulk pages
under the namespace evidence-registration fence; source memory hydration loads
only the five fields required for normalization. Rejection diagnostics report
only the observed count/byte lower bounds at the first exceeded ceiling, never
candidate values or raw metadata.

## Recall

Semantic recall considers a bounded ANN window. Lexical fallback is also
bounded and deterministically ordered. Responses disclose:

- `candidate_window_complete`, `candidates_considered`, `candidate_limit`, and
  `candidate_mode`;
- `retrieval_degraded` when embedding generation or ANN discovery degraded.

Recall cache keys include the candidate-window contract. A degraded fallback is
not cached as an exact ANN response. Subject keys are fetched only for the
bounded rows being decrypted.

## Knowledge snapshots and signed decision artifacts

`GET /v1/snapshot` returns an exact `total` and a deterministic `(event_time,
id)` keyset page. `complete=true` means the response contains the entire
snapshot. Otherwise continue with `next_event_time` and `next_id`, retaining the
returned `recorded_as_of` on every page. That transaction-time watermark keeps
later-ingested backdated facts from moving an in-progress traversal.

Snapshot JSON and Markdown pages are measured with a bounded database aggregate
before ORM hydration, subject-key loading, or plaintext decryption. Content
pages use `CONTENT_EXPORT_PAGE_BYTES_LIMIT` (32 MiB by default); a page that
cannot fit returns HTTP 413 `snapshot_page_byte_capacity_exceeded` or
`snapshot_markdown_byte_capacity_exceeded` without returning a prefix or writing
an export audit event.

Decision Receipts and evidence packs currently embed at most 10,000 snapshot
facts. If the exact boundary is larger, Lians returns HTTP 413 with code
`knowledge_snapshot_requires_paged_export`. This is an intentional fail-closed
compatibility change: Lians never signs or labels a truncated snapshot as a
complete decision boundary. Use `/v1/snapshot` to export the larger boundary in
pages.

Complete receipts and evidence packs also have aggregate byte ceilings. The
safe default receipt is hash-only: its snapshot projection does not load subject
keys or decrypt content and uses `HASH_ONLY_EXPORT_PAGE_BYTES_LIMIT` (16 MiB by
default). `include_source_content=true` and evidence packs use the content
budget. Snapshot, normalized evidence-graph, and audit-verification estimates
must fit one combined budget before signing; final canonical output size is
checked again before an audit export event commits. Capacity failures use stable
`decision_receipt_byte_capacity_exceeded`,
`evidence_pack_byte_capacity_exceeded`, or
`audit_verification_byte_capacity_exceeded` codes.

Markdown snapshot exports include `snapshot_total` and `snapshot_complete` and
describe the included rows as a snapshot page.

## Graph and decision-evidence traversal

Memory lineage is a caller-visible supersession DAG: several older facts may
legitimately converge on one successor. `GET /v1/memories/{memory_id}/lineage`
uses an indexed recursive weak-component walk capped by `max_nodes` (hard maximum
5,000), then hydrates selected rows and latest audit bindings in portable bind
batches rather than issuing one query per hop or edge. Nodes are returned in
deterministic topological order; explicit `from_id`/`to_id` edges, plural
`root_ids`/`tip_ids`, and `shape` are authoritative. Singular root/tip fields
remain compatibility aliases.

`has_more` means the recursive inventory exceeded the selected node ceiling;
`reachable_nodes` is then a disclosed lower bound. `complete` additionally
requires a closed visible tip rather than a dangling or barrier-hidden successor.
Each edge identifies its immutable audit event and chain position and reports a
separate `audit_binding_status`; `audit_binding_complete` does not claim that the
namespace hash chain itself was re-verified by this endpoint. Cycles fail with
HTTP 409 instead of being flattened. Before ciphertext hydration or subject-key
loading, the response is conservatively measured under
`LINEAGE_RESPONSE_BYTES_LIMIT` (16 MiB by default); excess returns HTTP 413
`lineage_response_byte_capacity_exceeded` without a partial response.

Relationship neighbors, paths, and entity distances use indexed frontier
queries with caller-visible node and edge budgets. Responses include
`search_complete`, `truncated`, `nodes_examined`, and `edges_examined`. A path
response uses:

- `connected=true` when a path was found;
- `connected=false` only after a complete negative search;
- `connected=null` when budgets prevented a conclusive negative result.

Opt-in graph-proximity recall carries `graph_search_complete=false` and marks
retrieval degraded when its internal distance traversal hits a budget; omitted
distances are not described as unreachable.

Decision evidence graphs are keyset-paginated by `(relation, link_id)`. Exact
link, artifact, direct, and reachable counts are separate from page counts.
Decision review history is paginated by sequence. `page_chain_verified` covers
the returned page and its cursor anchor; `chain_scope_complete=true` only when
the request verified and returned the entire chain from sequence one.

Routine decision integrity checks read the persisted review projection and the
latest immutable review event in one database statement. Review appends are
serialized, predecessor-validated, and append-only, so that fixed head is a
transitive checkpoint without hydrating an unbounded history. The history
endpoint remains the bounded surface for page-by-page chain verification.

The compatibility array endpoints for decision records, ledger events, and
evidence artifacts expose exact filtered totals and stable descending
timestamp/UUID keysets in response headers. `X-Lians-Page-Complete` describes
exhaustion after the current cursor; only `X-Lians-Collection-Complete=true`
asserts that this single, un-cursored array contains the whole collection.
Append-only pages are read before their exact count, and collection completeness
also requires `total == returned`, preventing a concurrent append from producing
`total < returned` or a false whole-collection claim.
Absence from a response with either a next cursor or collection-complete false
is not evidence of absence.

Interactive and durable impact analysis refuses to hydrate more than 2,000
matching evidence links for one decision or 50,000 links in one worker page.
The operation fails closed instead of computing risk from a silent prefix.

Exclusive relationship writes lock and invalidate the complete prior-edge set
or commit nothing. The default atomic ceiling is 500 edges
(`GRAPH_EXCLUSIVE_INVALIDATION_LIMIT`, hard maximum 5,000). Crossing it returns
HTTP 503 with `graph_exclusive_invalidation_capacity_exceeded`; duplicate live
triplets return `graph_live_edge_invariant_violation` for reconciliation instead
of choosing an arbitrary row. Multi-edge extraction uses one transaction, so a
later capacity failure cannot leave an earlier extracted prefix committed.
Before that transaction begins, extraction validates the complete candidate
set under both a row ceiling (250 by default, hard maximum 5,000) and a
serialized-byte ceiling (2 MiB by default, hard maximum 64 MiB). The byte
estimate includes every visible triplet plus a conservative per-edge response
and audit-envelope reserve. Exclusive extraction additionally rejects multiple
destinations for one `(source, relation)` pair, inventories the complete prior
edge set under the agent mutation lock, and applies the same 500-edge default
cumulative invalidation ceiling plus a 4 KiB audit/integration reserve per
invalidation. Duplicate normalized triplets are collapsed, but a
raw extractor response beyond `GRAPH_EXTRACT_CANDIDATE_LIMIT` is still refused
instead of spending request time normalizing an attacker-sized result. Field,
row, or byte capacity failures return HTTP 413 with a stable
`graph_extraction_candidate_*` code before any edge or audit row is written.
Configure `GRAPH_EXTRACT_CANDIDATE_LIMIT` and
`GRAPH_EXTRACT_CANDIDATE_BYTES_LIMIT` together.

## Supersession writes

Supersession candidates are isolated to the incoming memory's exact information
barrier. Keyed decisions narrow the database partition by the complete incoming
structured-key shape and then perform authoritative adapter normalization.
Unkeyed PostgreSQL decisions apply the semantic threshold in the database;
non-PostgreSQL development stores scan only when the entire live partition fits.

Candidate hydration is complete-or-error under both a row ceiling (500 by
default) and a materialized byte ceiling (32 MiB by default). The byte budget
includes encrypted content, metadata, and conservative row/embedding overhead.
Configure `SUPERSESSION_CANDIDATE_LIMIT` and
`SUPERSESSION_CANDIDATE_BYTES_LIMIT` together. If either complete candidate set
does not fit, the memory transaction returns HTTP 503 with
`supersession_candidate_capacity_exceeded`; no memory, derived clause,
supersession closure, cache generation, or audit prefix commits.

Before applying a verdict, the write path rejects duplicate, overlapping, or
self-referential candidate identifiers, then bulk-locks the complete bounded
identifier set in deterministic pages of at most 400. Every superseded,
conflicting, and later-successor row must still be live, unerased, unsuperseded,
and in the exact namespace, agent, and information-barrier partition selected
by candidate discovery. Its one-to-one `live_facts` projection is locked and
verified at the same boundary. Drift fails closed with
`supersession_snapshot_changed`; an internally inconsistent verdict fails with
`supersession_decision_invalid`. No candidate prefix is mutated.

Memory and current-fact mutations use portable bind-size pages. Immutable audit
events remain sequential because the namespace hash chain is order-sensitive.
Atomic batch ingestion acquires canonical subject locks before canonical agent
locks, matching single-write and subject-erasure ordering.
Derived-clause parent rows and their read-model identifiers are batch-loaded;
parent metadata is updated with bounded executemany operations, while one
`derived_stale_mark` event remains available for each closed clause. A parent
may retain at most 5,000 stale-clause markers before the write fails closed with
`supersession_parent_capacity_exceeded`. The complete parent metadata inventory
and the post-update canonical JSON must also fit
`SUPERSESSION_CANDIDATE_BYTES_LIMIT`.
Backdated derived clauses emit the same successor-bound `supersede` evidence as
top-level memories, with both `backdated_arrival=true` and `derived=true`.

## Recorder evidence indexing

OTLP ingestion is all-or-nothing and bounded by both serialized request bytes
and configured cardinality. The default request admits at most 2,000 spans and
500 distinct GenAI traces; crossing either ceiling returns HTTP 413 with no
committed spans so a collector can split and retry without an ambiguous prefix.

Decision creation atomically back-links at most 500 pre-existing Recorder
events. If the exact count is larger, the authoritative decision and a durable
fixed-snapshot job commit together; affected normalized coverage remains
partial rather than describing an indexed prefix as complete. The job advances
bounded `(recorded_at, id)` pages until its exact count and terminal cursor both
match. Recorder events arriving after the decision are indexed individually on
ingest and cannot fall through the frozen-boundary race because decision and
event INSERTs take the same database fence.

## Backtest contamination

Backtest cleanliness and contamination rate use exact database-side counts.
Detailed flags are a bounded `(event_time, id)` page with `flags_total`,
`flags_returned`, `flags_complete`, `has_more`, and a continuation cursor.

`is_clean=true` means no recorded Lians memory visible inside the authenticated
namespace and information barrier violates the supplied cutoff. It does not
prove that an external simulation used no unrecorded or out-of-band future
input.

## Audit, retention, and administration

Audit export is keyset-paginated by `chain_position` and reports exact totals,
returned rows, `has_more`, collection completeness, and `next_chain_position`.
`total_rows` always describes the filtered collection before the cursor;
`complete=true` only when the uncursored page contains that whole collection,
so the last continuation page is not mislabeled as a one-page export. Event
traversal is frozen at `snapshot_max_chain_position`; callers retain that value
as `through_chain_position` on every continuation, so concurrent appends cannot
change the exported collection. An ahead-of-head watermark is rejected with
`audit_snapshot_watermark_invalid`. Event
hydration is preflighted under `AUDIT_EXPORT_PAGE_BYTES_LIMIT` (16 MiB by
default) and fails with `audit_export_page_byte_capacity_exceeded`. Chain
verification uses the same aggregate byte guard and remains explicitly
`partial` when its row ceiling is reached. Embedded verification discloses
`chain_rows_checked`, `chain_truncated`, and `chain_tip`; it never presents a
bounded prefix as a namespace-wide verdict. Audit reconstruction separately
discloses memory/event totals and completeness; ranked-query memory results are
not described as exhaustive.

Retention pruning operates in batches of at most 1,000 candidate rows (500 by
default), locks only candidate agents, and returns `remaining`, `complete`, and
`batch_limit`. The scheduler drains multiple bounded batches rather than one
namespace-wide transaction. Eligible tenants are enumerated in keyset pages
(64 by default, hard maximum 256), and a leader cycle attempts at most 512
tenants by default (hard configurable maximum 5,000). The singleton database
cursor advances only after an attempted page, so a crash or leader change can
repeat idempotent work but cannot permanently starve later tenant ranges.
Scheduler logs identify tenants only by fixed-length domain-separated hashes.

Administrative identity-provider, identity-binding, break-glass API-key,
barrier-group, SCIM tenant, SCIM credential, and SCIM entitlement lists use
bounded keyset pages and expose exact filtered totals, page limits, returned
counts, page/collection completeness, and paired `X-Lians-Next-*` cursors while
retaining their legacy array bodies. Original cursor query and response-header
aliases, including entitlement `after_group_id` and
`X-Lians-Next-Group-Id`, remain during the rolling compatibility window.
Standard SCIM `startIndex` remains offset-based for protocol compatibility but
is capped at 100,000; `count` remains bounded.

Tenant-managed workload credentials and durable metering events additionally
expose exact filtered totals and the complete compatibility-page header contract.
Workload credentials traverse descending `(created_at, id)`; metering events use
descending `(updated_at, id)`. `X-Lians-Collection-Complete=true` is possible
only for an uncursored page containing the whole filtered inventory. Canonical
SDK page methods validate every required header before returning a result;
legacy array methods remain one-page compatibility views and must not be treated
as exhaustive.

A SCIM Group resource contains either its complete membership or no resource:
writes and hydration are capped at 1,000 members. A legacy over-limit Group
returns SCIM HTTP 413 with `scimType=tooMany`; Lians never silently truncates the
`members` attribute.
The Group list batch-loads memberships for its at-most-100 Group page. It
preflights exact per-Group and cumulative counts, enforces a default 10,000-row
page ceiling, and measures the compact UTF-8 response against an 8 MiB default
budget. A changing membership snapshot or either capacity breach fails the
whole read; no Group receives a partial `members` array.
The inverse relation is equally bounded: one User may belong to at most 1,000
Groups, and the reconciled authorization may contain at most 50 distinct scopes.
Tenant-row serialization plus a PostgreSQL trigger prevents concurrent creates,
replaces, or PATCH operations from exceeding the per-User limit. User and Group
deletion load the complete bounded edge set or return `tooMany`; they never
remove/reconcile an arbitrary prefix.

Tenant enable, disable, and revoke freeze an exact User snapshot ordered by
`(created_at, id)` and enqueue a forced-RLS durable job. Each leased page locks
the tenant version before the job and User rows, commits its cursor with binding
changes and audit events, and is safe to replay after lease expiry. Disable and
revoke bulk-disable every linked binding in their configuration transaction;
the worker adds bounded per-User reconciliation evidence without creating an
access-revocation window. Exact progress and completeness are available through
the administrative status/retry/one-page-advance API. A newer tenant version
terminally supersedes stale work.
SCIM-managed identity bindings persist the tenant/version fence. Both the
PostgreSQL SECURITY DEFINER bootstrap lookup and SQLite's indexed fallback deny
fenced bindings until the corresponding job reaches `completed`; activation of
the whole version occurs atomically with final progress and audit evidence.
Legacy/manual bindings have null fence fields and remain compatible.

## Integration fan-out

Durable integration events fan out to at most 100 active destinations by
default (hard configurable maximum 1,000). Active registration/enabling is
serialized per tenant boundary, and enqueue performs a `LIMIT + 1` recheck so
legacy or concurrently invalid configurations reject the entire event instead
of creating a partial delivery set. Idempotent event reads use the same fence.
Destination revocation cancels pending/retry rows with one bounded-memory bulk
statement; immutable attempts and leased worker semantics are unchanged.

Integration metadata pages defer encrypted destination credentials and event
payload blobs, so a metadata-only list or point read never hydrates secret
ciphertext it will discard. An explicit `include_payload=true` event page or
point read first reads only bounded metadata and performs a database-side
sealed-byte inventory. The conservative estimate covers the final serialized
metadata, JSON payload expansion, and array framing where applicable against
`CONTENT_EXPORT_PAGE_BYTES_LIMIT`. A breach returns HTTP 413 with
`integration_payload_export_capacity_exceeded` and no partial response;
payloads are fetched, authenticated, and decrypted only after that preflight.

## Admission review decryption

Admission review pages load only the durable subject keys referenced by their
at-most-500 selected rows, in portable bind batches. The initial page defers
content and unused metadata; database-side sealed-content and visible-JSON
lengths, conservative serialization expansion, and fixed row overhead must fit
`CONTENT_EXPORT_PAGE_BYTES_LIMIT` before any content-bearing row is hydrated.
Capacity failure returns HTTP 413 `pending_admission_page_capacity_exceeded`
with `content_materialized=false`. Reads never call the create-key path: a
destroyed key produces the explicit `[ERASED]` tombstone, while a missing,
corrupt, or unavailable active key fails the whole page as HTTP 409
`pending_admission_content_integrity_failed`.
This removes per-row key queries and prevents a read from silently minting a
replacement key for ciphertext it cannot authenticate.
