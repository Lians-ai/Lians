# Evidence normalization coverage and exhaustive impact assessment

An evidence graph is sparse by design. A missing link can mean “the decision
did not use this dependency,” “the normalizer has not examined the decision,”
or “the input could not be normalized.” Lians never treats those states as
equivalent.

## Persisted per-kind coverage

Every DecisionRecord has a monotonic coverage registration and one coverage row
for each supported evidence kind: source, policy, model, tool, permission,
instruction, input, and output. Each row records:

- `unknown`, `partial`, or `complete` status;
- the normalizer version and normalization scope;
- a SHA-256 source watermark tied to the immutable DecisionRecord hash;
- bounded gap codes; and
- the number of normalized artifact identities observed for that kind.

`complete` means the named normalizer exhaustively inspected its declared
immutable input scope and persisted every recognized candidate. It does not
mean that an artifact exists, that the evidence is correct, or that no future
evidence link can be appended. `partial` names a concrete gap such as an
unresolved source reference or an unrecognized declared shape. `unknown` makes
no completeness claim.

New API-created decisions are normalized in the same transaction and receive
all eight explicit states. Database registration triggers first create
`registration-pending` unknown rows so a direct or interrupted insert cannot
silently disappear from coverage. Successful normalization replaces those
rows with versioned complete/partial watermarks.

Hash roles are explicit. Decision-level `input_hash` and `output_hash` produce
artifacts marked `decision_input` and `decision_output`; Recorder event input
and output hashes are event-scoped and marked `runtime_input` or
`runtime_output`, so tool arguments cannot be promoted into the decision's own
I/O boundary. Tool definition and result hashes are separate role-addressed
artifacts and are merged by tool/call identity only when unambiguous. Multiple
conflicting hashes remain candidates and keep receipt tool provenance
incomplete instead of selecting one silently.

Migration-era decisions are backfilled as `legacy-unassessed` / `unknown` with
the `legacy_backfill_unknown` gap. Existing graph links do not upgrade that
state. Only an explicit normalizer pass may make a later completeness claim.

Use `GET /v1/decisions/{decision_id}/evidence-coverage` for the coverage view.
The evidence-graph response includes the same persisted state. Its
`normalized_complete` field is true only when all eight rows are explicitly
complete; it is never derived from the absence of unindexed IDs or links.

## Fast impact assessment remains a lower-bound path

`POST /v1/decisions/impact` still returns an indexed result quickly. It runs a
bounded legacy fallback only for decisions whose requested kind is missing,
unknown, or partial and which do not already have an exact matching indexed
link. The response continues to disclose candidate counts, fallback
truncation, search truncation, and `total_is_lower_bound`. Source resolution is
also capped at 50,000 distinct legacy memory references; hitting that cap sets
the same lower-bound/truncation disclosures. Its system-change event stores at
most 100 example decision IDs and a truncation marker.

Use this endpoint for interactive investigation, not for an exhaustive
attestation when incomplete or legacy coverage exists.

## Durable exhaustive assessments

The exhaustive path is a persisted, autonomously processed job:

1. `POST /v1/decisions/impact-assessments` creates an idempotent assessment and
   freezes two monotonic high-watermarks: the maximum visible decision coverage
   registration and maximum visible evidence-link registration. PostgreSQL
   creation takes the same namespace-scoped transaction fence used by both
   registration triggers, so a lower sequence cannot commit behind the captured
   watermark. The job also persists the exact number of DecisionRecords in that
   tenant/barrier-visible scan relation; global sequence values are identifiers,
   never treated as row counts or percentages.
   Every API replica may claim due jobs with PostgreSQL row locks and
   `SKIP LOCKED`, then process bounded pages under the job's persisted namespace
   and exact barrier context. A durable lease makes interrupted work recoverable.
2. `POST /v1/decisions/impact-assessments/{id}/advance` processes 1–500
   decisions per page and at most 20 pages per call. The default is one page.
3. `GET /v1/decisions/impact-assessments/{id}` returns its durable cursor,
   snapshot, counts, and status.
4. `GET /v1/decisions/impact-assessments/{id}/results?after=...&limit=...`
   keyset-pages at most 200 idempotent matches.

The processor scans every visible DecisionRecord at or below the frozen
coverage sequence. For each page it checks indexed links at or below the frozen
link sequence and also examines the immutable legacy fields of every decision.
The second scan is intentionally unconditional: a coverage projection can be
reassessed after job creation, so using its later state to skip work would make
the frozen snapshot drift. Page matches are unique by job and decision. The
cursor and matches commit together, so a crash before commit replays safely and
a crash after commit resumes after the saved sequence.

Completion requires both the final coverage watermark and exact equality between
`decisions_scanned` and `snapshot_decision_count`. The database makes the frozen
count immutable and rejects over-scans or a completed job with a cardinality
mismatch. `lians_impact_scan_progress_ratio` is therefore the aggregate scanned
row count divided by the aggregate frozen row count for active jobs; interleaved
global sequence gaps cannot manufacture progress.

`POST /v1/decisions/impact-assessments/{id}/advance` remains a compatible
caller-directed path, but it uses the same lease and page-processing service and
returns `409` instead of racing a live worker. Claims, attempts, consecutive
failures, retry time, lease expiry, heartbeat, and bounded error code/digest are
durable job state. Retry delay uses capped exponential backoff with deterministic
jitter. Repeated failures reach the persisted attempt limit and terminate a
poison job; an expired lease counts as a failed attempt. Snapshot visibility
invariants fail terminally, so Lians never labels a job complete when its cursor
cannot reach the frozen coverage watermark.

For source fallback, memory rows are read only when their system-valid start is
at or before the job creation time and IDs are queried in bounded batches.
Subject erasure can intentionally clear source labels or version metadata while
retaining hashes and opaque IDs; such privacy mutations may make those erased
legacy labels unrecoverable and are not reversed for an assessment.

`completed` is scoped to those two explicit registration snapshots. Decisions
or links registered later are intentionally excluded and require another job;
the status and completion event say so. This is the stable snapshot boundary,
not a claim about an endlessly moving namespace.

## Isolation, eventing, and bounds

Jobs belong to the caller’s exact namespace and information-barrier context.
An unbarriered job cannot be read from a barrier-scoped credential, while a
barrier-scoped job may assess the same visible unbarriered decisions allowed by
the Decision API. PostgreSQL applies forced namespace RLS and an exact
job-barrier policy to jobs and results.

Queue discovery alone uses the internal `__admin__` RLS sentinel so replicas can
find work across tenants. Before any decision, evidence, memory, match, ledger,
or audit query, the worker switches to the claimed job's persisted namespace
and barrier context. Metrics use closed lifecycle/status vocabularies and omit
tenant IDs, job IDs, dependency values, and error text.

Completion creates at most one `system_change` Ledger event and one core audit
entry, inside the transaction that marks the job complete. The payload records
counts and snapshot watermarks, never an unbounded affected-ID array. Result
pages, match bases, gap lists, per-call work, and fast-path examples all have
explicit limits.

Evidence artifacts, evidence links, link registrations, and decision coverage
registrations are database-enforced append-only records. Coverage projections,
jobs, and result matches may advance but cannot be deleted or truncated. These
boundaries apply through PostgreSQL privileges and triggers and through SQLite
triggers, preventing a completed snapshot from being rewritten underneath its
watermarks.

On PostgreSQL, the runtime role can only read the two registration tables.
Security-definer triggers are their sole writer, and those triggers acquire the
namespace registration fence before allocating a sequence. Database owners and
roles with explicit RLS bypass remain part of the trusted computing base.
