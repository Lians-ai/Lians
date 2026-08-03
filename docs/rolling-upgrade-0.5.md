# Lians 0.5 expand/contract rolling-upgrade runbook

This runbook is the compatibility contract for upgrading a live PostgreSQL
deployment from 0.4.2 to 0.5.0. Apply additive revisions through
`0054_otel_barrier` while 0.4.2 pods still serve, then use the explicit OTLP
ingest fence below before advancing to the release head
`0063_admin_identity_indexes`. Do not run `upgrade head` across the `0054a`
boundary while an old OTLP writer can still reach the database, or across the
`0056b` boundary while an old direct-table authentication caller can serve.

The data/index companions `0039a`, `0041a`, `0042a`, `0046a`, `0053a`,
`0054a`, `0056a`, `0058a`, `0060`, `0061`, `0062a`, and `0063` require an
online migration connection. They commit bounded pages or concurrent index
operations independently, detect invalid interrupted indexes, and resume from
durable database state. A generated offline script intentionally stops before
these revisions rather than pretending their transaction boundaries are safe.

## Mixed-version contract

| Surface | 0.4.2 write after migration | 0.5.0 write | Deferred contract removal |
|---|---|---|---|
| Core audit | Historical direct `INSERT` is accepted, but PostgreSQL replaces all ordering and integrity fields with canonical v3 values under a namespace lock and advances the protected head atomically. | Uses the append function, which reaches the same trigger-backed primitive. | Revoke runtime `INSERT` and remove the legacy insert shape only after no old writers remain. |
| DecisionRecord | Missing provenance and authorization columns receive constant v1 `legacy_unverified` and empty-snapshot markers. No claimed agent label is promoted to authenticated identity. | Supplies verified provenance plus principal type, optional role, and the complete bounded effective-scope snapshot in hash v3. Existing verified v2 records remain valid but explicitly have no authorization snapshot. | Remove compatibility defaults only after old writers and old background jobs are gone. Never backfill v1/v2 authorization from current identity state. |
| Recorder | Missing event provenance receives constant v1/legacy markers; the run summary is conservatively projected from the inserted event. | Supplies verified provenance and hash v2 explicitly. | Remove defaults and the compatibility projection only after mixed runs are reconciled. |
| Recorder decision back-links | `0058` adds a decision-scoped database fence that old event and decision INSERTs also take. Old application code retains its historical synchronous behavior until drained. | At most 500 prior events index atomically with the decision. Larger exact boundaries commit the decision plus a forced-RLS, fixed-snapshot job and hold affected coverage partial until exact completion. Events after the frozen boundary index in their own ingestion transaction. | Keep the queue, fence, worker, and `recorder_index_pending`/`recorder_index_failed` truth labels; they are production contracts, not temporary compatibility objects. |
| Memory idempotency | Existing/raw admitted-memory claims remain readable and are mirrored into `operation_idempotency`; conflicting representations are rejected. The old binary's resource-before-claim commit gap cannot be retroactively made atomic. | Admitted-memory completion writes the hashed claim first and the exact legacy mapping in the same transaction. | Route keyed single-memory writes only to 0.5 during the mixed window. Retain `idempotency_keys` through 0.5; drop it only in a later contract release. |
| ValidMind model links | After `0053a`, a uniquely resolvable namespace-wide 0.4.2 model ID and its 0.5 scoped ID are synchronized in both directions under one `(namespace, legacy ID)` transaction lock. Unique aliases mirror whichever side exists, reconcile equal mappings to the latest timestamp, fail closed on conflicting mappings, and do nothing when neither side exists. A unique-to-ambiguous transition deletes only the legacy row; an ambiguous old-ID read or write therefore fails closed while every scoped row remains. | Reads exact opaque-scope inventory rows and writes the scoped ID; the unique legacy mirror remains visible to old readers. When ambiguity returns to one survivor, the transition trigger recreates either missing side from the other. Final inventory/alias removal deletes the retired scoped row and legacy mirror. | Remove legacy link mirrors only in a later contract release after zero old callers and exact pair reconciliation. Never guess a scope for an ambiguous old ID. |
| OTLP span barrier | After `0054`, a PostgreSQL trigger copies the old writer's already-authenticated namespace/barrier GUC into explicit trusted provenance before RLS and inventory triggers run. Only pre-expand history remains untrusted and hidden from scoped RLS contexts. | Writes the authenticated barrier plus `barrier_scope_trusted=true`; an explicit NULL means intentionally shared within the namespace. | `0054a` conservatively moves every historical untrusted row to `__legacy_restricted__`, validates the writer fence, creates scope-aware uniqueness, and makes old OTLP writes fail closed. |
| Authentication bootstrap | Through `0056a`, an old binary may still perform its exact indexed table lookup while 0.5 uses the PUBLIC-revoked exact lookup functions. | Uses the exact API-key-digest or verified provider/subject function and establishes namespace/barrier RLS before any direct metadata update. | `0056b` enables direct-table RLS and therefore requires all old authentication callers to be drained; old pods are not compatible after this boundary. The two auth tables omit FORCE only for the reviewed table-owner functions; runtime remains a non-owner without BYPASSRLS. |
| SCIM membership | Through `0056a`, service validation remains the compatibility boundary. | Enforces complete Group and per-User bounds before reconciliation. | `0056b` refuses legacy over-capacity state, then serializes and enforces at most 1,000 Groups per User in PostgreSQL. Deletes remain complete and never truncate membership lists. |

`legacy_unverified` is a durable truth label, not a temporary error state.
Legacy DecisionRecords and Recorder events remain queryable historical evidence,
but cannot satisfy verified-provenance controls or be signed/exported as verified.
An opaque legacy/scoped identifier collision or a disagreeing `vm_cuid` pair is
a hard migration/transition failure: Lians preserves the scoped rows and requires
operator reconciliation instead of choosing a target.

## Required rollout order

1. Record current image digests, database identity/timeline, Alembic revision,
   runtime and migrator roles, and the exact target revision. Freeze unrelated
   schema work.
2. Prove current PITR coverage and a recent restorable backup. Preserve a copy
   of the raw `idempotency_keys` table because it still contains client keys.
3. Confirm `lians_runtime` is `NOLOGIN`, non-superuser, non-`BYPASSRLS`, the API
   login is only a member, and the separate migrator owns the schema. Inspect
   long transactions and set reviewed finite DDL timeouts.
4. Before the migration job enters `0053a_validmind_backfill`, quiesce
   `PUT /api/v1/models/*` with bounded `503` plus `Retry-After` and drain its
   in-flight requests. ValidMind reads may continue. Run one migration job
   through `0054_otel_barrier` while every application pod is still 0.4.2.
   `0053a` commits source-marker and link-reconciliation pages independently.
   After source counting finishes, it installs the dual-ID/alias transition
   triggers before the first link page, so concurrent Decision/OTLP activity
   cannot reopen a cardinality gap. The write quiesce protects the earlier trigger
   installation boundary and the still-unreconciled historical rows. The final
   reconciliation removes every pre-existing ambiguous legacy row and proves
   that alias counts match exact inventory membership and every one-sided unique
   pair was mirrored. Reopen ValidMind writes only after `0053a` is stamped and
   those invariant checks pass.
   Unique legacy IDs are then mixed-version compatible; ambiguous legacy IDs
   deliberately return a conflict and must be sent to a 0.5 pool with the scoped
   ID. Do not let API replicas race Alembic and do not advance to `0054a` yet.
5. Before deploying 0.5.0, execute approved old-image write probes in a
   dedicated probe namespace: direct audit append, DecisionRecord create,
   Recorder event ingest, and memory create/replay. Verify canonical audit v3
   positions/hashes, explicit legacy provenance, Recorder run projection, and
   one matching claim in both idempotency representations. Retain immutable
   probe evidence; do not delete or rewrite it.
6. At the edge, temporarily quiesce `POST /v1/memories` requests that contain
   an `Idempotency-Key` (bounded `503` plus `Retry-After`). Do not log or hash the
   header value in the router. Drain old in-flight keyed requests for at least
   the enforced request timeout and confirm the old-pool in-flight count is
   zero. A 0.4.2 binary commits an admitted memory before inserting its raw
   claim and never carried the key on the memory row, so no additive database
   migration can make that already-running transaction atomic.
7. At the edge, quiesce both `POST /v1/traces` and
   `PUT /api/v1/models/*` with bounded `503` plus `Retry-After`; confirm
   collector persistent queues are healthy. Drain every 0.4.2 OTLP request,
   old trace-producing job, and in-flight model-link write, then run only
   `0054a_otel_barrier_contract` with the online migrator. It commits 1,000-row
   legacy recategorization pages, fences omitted provenance, builds repairable
   concurrent indexes, drops the old namespace-wide dedup index, and validates
   the dynamic namespace/barrier RLS inventory. Then immediately advance to
   `0055_retention_cursor`, `0056_auth_lookup_expand`, and
   `0056a_admission_index`; they seed the singleton durable keyset cursor,
   install exact authentication lookups, and build the resumable
   `pending_admissions(namespace,status,barrier_group,created_at,id)` index
   concurrently. `0056a` intentionally refuses offline SQL. Do not advance to
   `0056b` yet. Keep the trace quiesce rule in place until a ready 0.5
   pool exists; old binaries cannot write traces after this contract. Keep
   ValidMind PUTs quiesced until scoped/legacy link pairs reconcile exactly,
   then reopen them. A concurrent PUT is expected to abort a recategorization
   page with retryable SQLSTATE `40001`, never to be ignored or deadlocked.
8. Quiesce every public and private API route that authenticates with an API key
   or OIDC bearer, return bounded `503` plus `Retry-After`, drain in-flight
   requests, and stop every 0.4.2 API pod and direct-table authentication job.
   Run `0056b_auth_lookup_contract`, followed by
   `0057_decision_auth_snapshot`, `0058_recorder_index_jobs`, and the online-only
   `0058a_live_supersession_indexes`, `0059_subject_erasure_jobs`,
   `0060_lineage_graph_indexes`, `0061_inventory_page_indexes`,
   `0062_scim_reconciliation_jobs`, `0062a_scim_reconcile_indexes`, and the
   release head `0063_admin_identity_indexes`. The auth contract refuses any User with more than
   1,000 Group edges, installs the serialized database capacity trigger, enables
   namespace and restrictive barrier RLS on `api_keys` and `identity_bindings`,
   and verifies the exact lookup function ownership, fixed settings, PUBLIC
   revocation, and runtime grants. The DecisionRecord revision preserves v1/v2
   rows with an empty authorization snapshot and enables verified v3 only when
   principal type, credential reference, and unique valid scopes (at most 50,
   including `write`) are present. `0058` installs the durable Recorder queue,
   forced namespace and restrictive barrier RLS, monotonic job guards, and the
   shared decision/event advisory fence. `0058a` repairs or concurrently builds
   the Recorder snapshot/audit-binding indexes plus the partial live-memory and
   live-exclusive-edge supersession indexes; it refuses offline SQL. `0059`
   installs the fixed-snapshot, resumable subject-erasure queue and its forced
   tenant isolation. `0060` concurrently builds the reverse supersession and
   latest audit-binding indexes required by bounded lineage DAG traversal and
   also refuses offline SQL. `0061` concurrently builds the stable workload-
   credential and metering-event inventory page indexes and likewise refuses
   offline SQL. `0062a` builds the SCIM activation-fence and fixed-snapshot page
   indexes; `0063` builds exact admin API-key and identity inventory page indexes
   with the same resumable online contract. Start one
   0.5.0 canary with side effects
   constrained while production keyed memory creation remains quiesced.
   Exercise the same writes and their retries, verified reads/exports, audit verification,
   readiness, scoped/shared OTLP ingest, and representative failure paths.
   Route traces only to this ready pool and verify queued collector data drains
   without cross-barrier deduplication.
9. Atomically replace the quiesce rule with a header-presence route: every
   `POST /v1/memories` carrying `Idempotency-Key` goes only to the ready 0.5
   pool, regardless of key value or admission outcome. It must fail closed with
   `503` if that pool is unavailable and must never fall back to 0.4.2. Route
   unkeyed and otherwise backward-compatible traffic only to ready 0.5 pods too.
10. Keep only 0.5.0 live for an observation window; 0.4.2 must not return after
   the authentication contract. Pause on any integrity
   conflict, audit boundary rejection, idempotency disagreement, unexpected
   legacy-marker rate, RLS/readiness failure, lock-wait growth, or error-budget
   burn.
11. Scale out 0.5.0 with zero unavailable replicas. Confirm all 0.4.2 API pods,
   workers, scheduled jobs, and one-off scripts remain terminated, then repeat
   reconciliation and hold the previous image for investigation only; it is not
   an authentication-compatible rollback after `0056b`.

An application rollback during this window leaves the expanded schema in place.
Quiesce keyed memory creation first; do not route it back to 0.4.2 after 0.5 has
accepted claims. Keep a minimal known-good 0.5 keyed-writer pool or forward-fix
before reopening that route. Other explicitly backward-compatible traffic may
return to the pinned 0.4.2 image only before `0056b`; disabling auth-table RLS
to revive an old binary after that boundary is not an approved rollback. Do not
downgrade or remove compatibility objects as a rollback technique.

`__legacy_restricted__` means the historical barrier cannot be proven. It is
not a placeholder to replace from model name, trace ID, service name, API-key
label, or operator memory. Leave it restricted by default. Reclassification is
allowed only when an authorized operator can attach immutable historical proof
of the exact namespace and barrier that governed ingestion, preserve a backup
and change record, and perform the reviewed owner/migrator update; the ValidMind
trigger then moves the contribution between opaque scopes synchronously.

Once `0054a` has classified any row, migration downgrade deliberately refuses:
dropping the barrier or scope-aware unique index could merge protected scopes or
turn unverifiable evidence into shared evidence. Roll application code forward,
or restore a pre-0054 backup into a new cluster and validate it before cutover.
Do not bypass the refusal by clearing the sentinel.

## Reconciliation gates

Attach query output and timestamps to the change record. At minimum verify:

- every audit namespace has exactly one contiguous position sequence, every
  predecessor equals the prior canonical hash, the protected head equals the
  last row, and the full v3 chain verifier succeeds;
- every DecisionRecord and Recorder event has a recognized hash version and an
  explicit integrity/provenance classification; no legacy row is accepted by a
  verified export path;
- every DecisionRecord v1/v2 row has an empty authorization snapshot; every v3
  row has a valid principal type, allowed optional role, credential reference,
  and 1-50 unique valid scopes containing `write`;
- Recorder run principal/auth-method summaries contain the conservative legacy
  sentinels for every mixed-version run with legacy events;
- every row in `idempotency_keys` has one `memory.create` hashed claim naming
  the same memory ID, and no hashed claim disagrees with its legacy mapping;
- every DecisionRecord and OTLP source row has
  `validmind_inventory_counted=true`; inventory decision/span totals and
  per-version reference counts equal the source rows in the same namespace and
  opaque scope; the stored 100-version sample and exact distinct total agree;
- every ValidMind legacy alias has the exact current target count. A unique alias
  has matching legacy/scoped link rows with identical `vm_cuid` and
  `updated_at`; an ambiguous alias has no inferred canonical target and old-ID
  writes fail closed;
- mutation guards, forced RLS, grants, security-definer ownership/search paths,
  and both runtime and platform readiness checks match the reviewed posture;
- `api_keys` and `identity_bindings` have enabled namespace and restrictive
  barrier RLS, deliberately do not FORCE owner RLS, and their two exact lookup
  functions share the table owner, fix `search_path`, set `row_security=off`,
  deny PUBLIC, and grant only the reviewed runtime capability;
- no SCIM User has more than 1,000 Group edges, the capacity trigger is present,
  and the valid concurrent pending-admission index has the exact five-key shape;
- migration/backfill lock time, replication lag, WAL growth, invalid indexes,
  database errors, and latency remained inside the approved envelope.

Run reconciliation from the migrator or a dedicated read-only audit role, not
by broadening API privileges. Never log or export raw retry keys as evidence;
record counts and domain-separated digests where correlation is required.

## ValidMind capacity and write amplification

Budget the 0053 phases before rollout. They add one nullable-then-required marker
to each DecisionRecord and OTLP row, one private scope row per namespace/barrier
ever observed, one inventory row per namespace/scope/model, one version row per
distinct non-null model version, one alias row per legacy ID, and—only for a
uniquely resolvable linked model—at most one synchronized legacy/scoped link
pair. Raw barrier names remain confined to the forced-RLS private scope table.

Each model-bearing source insert synchronously touches the scope mapping and
inventory and may touch the alias and version tables. Updates/deletes move or
remove the old contribution before adding the new one; min/max boundary repair
uses scope-aware indexes and bounded index probes. The historical marker
backfill generates source-table WAL and dead tuples even though it runs in
1,000-row committed pages. Before `0053a`, reserve reviewed primary/replica WAL,
disk, autovacuum, and bloat headroom, then watch lock waits, replication lag,
dead tuples, and inventory-trigger latency. Pause between pages if the approved
envelope is exceeded; the marker and mirrored link pair are durable restart
state.

## Authentication and SCIM capacity

`0056a` adds one btree over
`pending_admissions(namespace,status,barrier_group,created_at,id)`. PostgreSQL
stores NULL btree entries, so both the shared (`IS NULL`) and exact-barrier
branches can use the index, while namespace/status prefix counts remain
indexable. Budget one additional index entry per admission plus build WAL,
temporary disk, and replica lag; an interrupted invalid build is detected,
dropped concurrently, and rebuilt on rerun.

`0056b` adds no membership rows. It takes the existing SCIM tenant row as the
serialization mutex for every new or moved edge and performs an indexed exact
per-User count. This deliberately reduces maximum membership fanout to a finite
contract: 1,000 Users per Group, 1,000 Groups per User, and 50 effective scopes.
The migration refuses a legacy over-capacity database; it never deletes edges
or guesses which authorization contribution to retain.

`0057` adds two nullable columns and one constant-empty JSON default. On
PostgreSQL the constant default avoids rewriting historical DecisionRecords,
but validating the widened provenance constraint still scans the table and the
constraint replacement takes brief DDL locks. Apply it during the existing
post-`0056b` drained window and watch lock wait, replica lag, and WAL. SQLite
must rebuild the table; the migration captures and restores all pre-existing
DecisionRecord triggers before installing the v3 scope and immutability guards.
No migration derives historical roles or scopes from present-day identity data.

`0058` creates a new empty queue table and adds two constant-time INSERT
triggers. They take the existing namespace evidence-registration fence first,
then the same transaction-scoped `(namespace, decision_id)` lock. This ordering
prevents multi-decision OTLP batches and worker pages from deadlocking while
serializing only transactions that register decision evidence in that namespace.
The new worker processes at most 100
events per page by default and commits each page independently; tune page and
claim counts against the database statement timeout, connection pool, WAL, and
replica-lag budget. Production must not disable the worker because signed
receipts and Gate coverage remain partial while a job is pending or failed.

`0058a` is online-only and resumable. PostgreSQL builds twelve btrees with
`CREATE INDEX CONCURRENTLY`, validates table, key order, predicate, access
method, uniqueness, and `indisvalid`, and drops only a matching interrupted
invalid index before rebuilding. The two partial indexes add write amplification
only for currently live memories or relationships; closing/erasing a row removes
its entry. The Recorder indexes cover `(namespace, decision_id, recorded_at,
id)`, `(namespace, run_id, recorded_at, id)`, and the exact audit payload
event-ID binding. Three additional indexes support stable barrier-aware keysets
for ledger events, decisions, and evidence artifacts without offset scans. Four
more cover the complete subject-table pages consumed by durable erasure.
Monitor lock waits, WAL, temporary disk,
replication lag, and invalid indexes before allowing the canary.

## Future contract release

The contract change is a later release with its own backup, canary, and
observation window. It may proceed only after deployment inventory proves zero
0.4.2 pods/jobs for the full rollback window, mixed-version write counters are
zero, all reconciliation gates pass, and the old image is no longer an intended
rollback target. That release may then:

- revoke direct runtime `event_log` insert and retire the old insert shape;
- remove DecisionRecord and Recorder legacy provenance defaults;
- remove the Recorder compatibility run-projection trigger; and
- remove `idempotency_keys` only after exact bidirectional reconciliation and
  a current recoverable backup.

If any precondition is uncertain, leave the expand objects in place. Their
continued presence is safer than guessing that a hidden old writer is gone.
