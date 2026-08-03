# Production operations

This is the operator runbook for Lians' API, Decision Evidence graph, Universal
Recorder, runtime Gate, Investigator workflow, PostgreSQL ledger, Redis cache, and
OpenTelemetry recorder gateway. It complements
[backup and restore](backup-restore.md) and [SLO/alerting](slo-alerting.md).
Stripe-billed deployments must also follow the
[durable metering runbook](durable-metering.md).

## Operating principles

- PostgreSQL is authoritative. Redis and process memory are disposable acceleration
  layers. Collector PVCs are temporarily authoritative for spans not yet accepted by
  Lians and must not be deleted during an outage. They contain raw producer OTLP
  before Lians minimization and are a sensitive-data store, not merely a retry cache.
- Preserve evidence before changing state. Record UTC timestamps, image digest,
  migration revision, database resource/timeline identity, object versions, queue
  depth, active key IDs, and the human/workload identities taking each action.
- Stop side effects before debugging corrupted evidence. Disable tool execution,
  webhooks, ticket automation, SCIM changes, and model calls on an isolated recovery
  candidate.
- Prefer forward repair for database migrations. An application rollback is safe only
  when the deployed schema is explicitly backward compatible with the old image.
- Never claim WORM, zero RPO, or an RTO from configuration alone. Those properties
  require provider contracts and successful drills at representative scale.
- Do not paste prompts, tool payloads, credentials, decrypted content, or subject data
  into alerts, chat, tickets, or incident notes. Use immutable IDs and hashes.

## Production launch gate

Every item needs attached evidence and an owner:

- pinned application and collector image digests, SBOM/provenance, vulnerability
  review, and a rollback image still present in the registry;
- production configuration passes startup validation; full Recorder payload capture
  remains disabled unless encrypted-content policy and retention are approved;
- managed PostgreSQL meets the HA/PITR contract and a representative PITR drill passes;
- latest logical bundle verifies, has a provider-attested immutable copy, and a monthly
  logical restore passes;
- at least two ready API replicas and two collector replicas are distributed across
  fault domains; PodDisruptionBudgets and queue PVC topology match the cluster;
- the named collector StorageClass and every snapshot/replica/backup path have
  encryption/key-custody evidence, and the raw queue has an owner, maximum custody
  time, access inventory, attachment alerts, incident-hold rule, and deletion proof;
- external `/readyz`, authenticated `/metrics`, collector `:8888`, Kubernetes, database,
  PITR, backup, KMS, and certificate signals reach the monitoring account;
- Alertmanager page/ticket/security routes and runbook URLs have been exercised;
- the API login is a non-owner, non-superuser/non-`BYPASSRLS` member of the fixed,
  non-owner NOLOGIN `lians_runtime` capability role; the separately held migrator owns schema,
  is also non-superuser/non-`BYPASSRLS`, does not inherit the runtime capability,
  neither runtime identity owns application objects or can assume an owning role,
  RLS is forced where required, and append-only tables deny update/delete/truncate,
  including `trg_decision_record_immutable` and
  `trg_decision_record_reject_truncate` on DecisionRecord;
- OIDC issuer/JWKS trust, SCIM bearer rotation, API-key rotation, receipt issuer keys,
  Gate approval roles, and break-glass administration have named owners;
- Stripe-billed deployments show a fresh durable metering worker, zero unreconciled
  dead letters, an oldest-due age inside the delivery SLO, and a tested Stripe thin-
  event destination for asynchronous meter validation failures;
- egress policy, data residency, retention/legal hold, SIEM/WORM destinations, support
  access, and incident notification obligations are documented per environment; and
- capacity/load results show at least 30% steady-state headroom in API, database,
  connection pool, WAL/archive throughput, Redis, collector memory, queue capacity,
  and PVC bytes.

For the supported Helm deployment, record the API-only PostgreSQL allocation and
prove `(DATABASE_POOL_SIZE + DATABASE_MAX_OVERFLOW) * HPA maxReplicas` does not
exceed it. The allocation must already exclude migrator, backup/restore,
observability, break-glass, provider, and failover reserves. Runtime and migration
URLs each require exactly one `sslmode=verify-full`; Redis requires `rediss://` and
certificate-hostname verification.

## Change and deployment sequence

For the 0.4.2 to 0.5.0 schema transition, the normative mixed-version behavior,
write probes, reconciliation gates, and explicitly deferred removals are in the
[0.5 expand/contract rolling-upgrade runbook](rolling-upgrade-0.5.md). Run the
migrations before the canary; do not remove its compatibility paths in the 0.5
change.

1. Open a change record with image digest, current/target migration revision, config
   diff, compatibility statement, risk, owner, rollback decision, and observability
   links. Freeze unrelated migrations.
2. Confirm the latest-restorable-time, recent provider backup, logical backup/WORM
   copy, and previous restore drill. Create a named provider restore point if supported.
3. Classify the schema change as expand, migrate/backfill, or contract. A release must
   not both remove an old schema and depend solely on an unproven rollback.
4. Inspect locks and long transactions. Set finite migration lock/statement timeouts
   appropriate to the reviewed operation. Cancel the change rather than letting DDL
   wait behind production traffic indefinitely.
5. Confirm `lians_runtime` already exists as non-owner NOLOGIN/NOSUPERUSER/NOBYPASSRLS and the
   runtime login inherits it but owns no application database, schema, relation,
   function, or type and cannot assume an application-owner role. Confirm the
   production `/readyz` role-posture check remains healthy. Run one migration job
   under a different NOSUPERUSER/NOBYPASSRLS identity
   that does not inherit `lians_runtime`. Capture complete logs,
   start/end time, database ID, and resulting Alembic revision. Do not let every API
   replica race `upgrade head`.
6. Verify the database revision against the release's expected revision. `alembic
   check` detects model operations not represented in migrations; it is not by itself
   proof that the database's current revision equals every expected head.
7. Canary the new application image with side effects constrained. Verify readiness,
   authentication, Recorder ingest, evidence links, signed receipts, Gate behavior,
   audit append, queue drain, and error-budget signals.
8. Roll out with zero unavailable replicas. Pause on any fast-burn, audit, Gate,
   identity, queue-loss, database, or degraded-retrieval alert.
9. Hold the previous image and backward-compatible schema through the observation
   window. Perform destructive contract migrations only in a later change after every
   reader/writer has stopped using the old shape and recovery evidence is current.

### Migration safety rules

- Never edit a released Alembic migration. Add a new revision.
- Keep every Alembic revision identifier at or below 32 characters unless an
  earlier deployed migration has explicitly widened `alembic_version.version_num`;
  the release checker enforces the default PostgreSQL capacity before packaging.
- Avoid table rewrites on the request path. Use nullable/default-free columns,
  concurrent indexes where PostgreSQL permits them, bounded backfills, validation,
  then contract.
- A PostgreSQL `CREATE INDEX CONCURRENTLY` cannot run inside the usual migration
  transaction. Give it an explicit reviewed migration strategy and detect invalid
  indexes after interruption.
- Do not mix irreversible data transformation with an application rollout. Preserve a
  source column/table until the new representation has been reconciled and backed up.
- Test RLS policy and `FORCE ROW LEVEL SECURITY` state after restore and migration;
  ownership changes can alter which sessions bypass policy.
- Downgrade functions are not a recovery plan. If a down migration deletes or
  transforms data, recover into a new cluster from PITR and cut over after validation.

### Resumable 0.5 data migrations

Alembic is configured with `transaction_per_migration=True`. The 0.5 scale
revisions also place committed data work in dedicated semantic phases so an
operator cancellation does not replay a database-wide transaction:

| Expand / online phase | Production behavior | Safe restart point |
| --- | --- | --- |
| `0025_system_time_validity` / `0025a_system_time_backfill` | Nullable transaction-time columns and a legacy-writer trigger are installed first. The online phase fills NULL rows in 5,000-row `SKIP LOCKED` pages, uses repairable temporary concurrent event indexes, builds the permanent memory index concurrently, validates a `NOT VALID` check, then contracts to `NOT NULL`. | Remaining NULL values are the progress record. An interrupted or invalid concurrent index is detected and repaired on rerun. |
| `0026_evidence_graph` / `0026a_evidence_graph_backfill` | Empty graph tables and a one-row immutable decision high-water mark are committed first. The online phase reads 25 decisions at a time with capped structured extraction and deterministic artifact/link IDs. | `lians_migration_0026_evidence_progress.last_decision_id` advances only after conflict-safe artifact/link writes and resolution of any concurrently existing artifact identity. It is dropped atomically with the revision stamp. |
| `0039_audit_append_boundary` / `0039a_audit_append_contract` | The expand phase adds a nullable audit position without blocking old inserts. The online phase advances at most 50 namespace frontiers by 100 links per committed statement, repairs the concurrent unique index, briefly fences the final tail, verifies every canonical predecessor/hash, and atomically installs the database append boundary. | Non-NULL positions and a valid index are durable progress. An interrupted final contract rolls back with its stamp; rerun fills only the remaining tail. |
| `0040_gate_execution_permits` / `0040a_gate_permit_contract` | The expand phase leaves hot Gate columns nullable and installs fail-closed compatibility triggers: old writers receive a derived or `lians:legacy-unbound` target, and policies without a mediator are preserved as retired evidence. The online phase fills/retire rows in 2,000-row pages, builds hot-table indexes concurrently, validates checks, then makes `target_ref` non-null. | Filled targets and retired policy status are durable progress. Invalid indexes are dropped concurrently and rebuilt. Locked rows cause an explicit failure before contract. |
| `0041_decision_record_integrity` / `0041a_decision_integrity_idx` | Constant legacy defaults and integrity guards commit atomically; the established-table provenance index builds separately and concurrently. | A valid index is accepted on rerun; an invalid index is dropped concurrently and rebuilt without replaying expand DDL. |
| `0042_recorder_integrity` / `0042a_recorder_backfill` | Event defaults and the rolling run-projection trigger land first. The online phase unions conservative legacy sentinels into 2,000 run summaries per commit and builds both Recorder/audit binding indexes concurrently. | Sentinel presence is the idempotent progress marker. Locked remnants fail explicitly; invalid indexes are repaired on rerun. |
| `0045_evidence_scope_identity` | PostgreSQL alone runs selective 1,000-row canonicalization calls. Each call stages and finalizes one page atomically under the table-owner migration boundary; already-canonical rows are never rewritten. A temporary concurrent unique expression index prevents canonical merges. Parent unique indexes are built concurrently and composite foreign keys are added `NOT VALID` before individual validation. | Every completed page is canonical. A locked mismatch, blank identity, or canonical collision fails explicitly; rerun resumes only remaining mismatches and repairs invalid indexes. |
| `0046_operation_idempotency` / `0046a_idempotency_backfill` | The new hashed ledger and synchronous raw-table mirror commit first. The online phase copies 2,000 legacy rows per transaction using a protected durable `(key, namespace)` cursor, then performs exact global reconciliation. | The cursor advances only after the page's hash/resource tuple agrees. It is dropped atomically with the revision stamp; rerun resumes from the last committed key. |
| `0053_validmind_inventory` / `0053a_validmind_backfill` | Source-row markers and synchronous opaque-scope inventory triggers land before historical DecisionRecord/OTLP rows are claimed in 1,000-row committed pages. A repairable concurrent DecisionRecord scope-boundary index is built. With ValidMind PUTs quiesced, idempotent link/alias transition triggers are installed before bounded pages remove ambiguous legacy rows and mirror one-sided unique 0.4/scoped pairs. Keep the quiesce through the exact final reconciliation and revision stamp. | The per-source counted marker and synchronized link pair are durable progress. New source inserts are counted synchronously, so no snapshot/high-water race can omit them; installing the transition trigger before reconciliation prevents concurrent Decision/OTLP activity from reopening an alias gap. A stable `(namespace, legacy ID)` transaction lock serializes pair changes. Unique aliases mirror either surviving side, reconcile timestamps, and reject conflicting `vm_cuid`; ambiguous/deleted aliases have no legacy row, while scoped rows survive until their exact inventory target is deleted. An invalid boundary index is dropped and rebuilt on retry. |
| `0054_otel_barrier` / `0054a_otel_barrier_contract` | Expand adds explicit OTLP barrier provenance plus forced namespace/barrier RLS; a compatibility trigger captures rolling writers' authenticated GUC boundary, while untrusted history is invisible to scoped callers. After OTLP ingress is quiesced and old writers drain, the online phase assigns the conservative `__legacy_restricted__` sentinel in 1,000-row committed pages, fences omitted provenance, builds scope indexes concurrently, and asserts the live RLS catalog. | `barrier_scope_trusted=true` is the per-row progress record. The constraint closes the final writer race; invalid concurrent indexes are repaired. Once rows are classified, downgrade refuses rather than merge protected scopes. |
| `0055_retention_cursor` | Creates and seeds one global retention-scheduler cursor. Each advisory-lock leader reads a bounded keyset page and advances the cursor only after that page is attempted, so restart or leader churn can repeat work but cannot starve later tenant ranges. | The singleton row and sweep generation are durable progress. Runtime receives SELECT and column-scoped UPDATE only; missing state fails the PostgreSQL scheduler closed. |
| `0056_auth_lookup_expand` | Adds exact API-key-digest and verified provider/subject SECURITY DEFINER lookups without changing old direct-table behavior. | Function ownership, fixed settings, PUBLIC revocation, and runtime execution grants are durable expand state. Old and new callers remain compatible through `0056a`. |
| `0056a_admission_index` | Builds `pending_admissions(namespace,status,barrier_group,created_at,id)` concurrently. NULL barrier entries are indexed for the shared branch. | A valid exact-shape btree is accepted; an interrupted invalid build is dropped concurrently and rebuilt. Offline SQL is refused. |
| `0056b_auth_lookup_contract` | After every old auth caller is drained, enables namespace/restrictive-barrier RLS on both auth tables and installs the serialized 1,000-Groups-per-User SCIM trigger. | The migration refuses existing over-capacity state. Exact function posture and RLS policies are rechecked; no membership is truncated or rewritten. |
| `0057_decision_auth_snapshot` | Adds nullable principal-type/role and a constant-empty scope snapshot for rolling v1/v2 writers, then admits DecisionRecord hash v3 only when the verified credential provenance and bounded unique effective scopes, including `write`, are complete. | Historical v1/v2 records stay byte-for-byte evidence and must keep an empty snapshot. PostgreSQL validates the new constraint and immutable scope validator; SQLite reconstructs the table while preserving every installed evidence trigger. Downgrade refuses once any v3 row exists. |
| `0062_scim_reconciliation_jobs` / `0062a_scim_reconcile_indexes` | Adds nullable SCIM tenant/version activation-fence fields to existing identity bindings, attaches their FK/check as PostgreSQL `NOT VALID` constraints before validation, installs the forced-RLS durable fixed-User-snapshot job table and fenced authentication lookup, then builds only the two established-table traversal indexes in the online companion. | Legacy/manual bindings retain all-null fence fields. Once a tenant-version job exists, both authentication lookup paths deny its bindings until the final page atomically activates the whole version and records completion. The companion accepts an exact valid btree, drops an interrupted invalid index concurrently, rebuilds it, and refuses offline mode. |
| `0063_admin_identity_indexes` | Builds exact keyset-page indexes for global and namespace-filtered admin API-key inventory, trusted identity-provider inventory, and global/namespace/provider-filtered identity-binding inventory without blocking established tables. | An exact valid btree is accepted on restart; an interrupted invalid index is dropped concurrently and rebuilt. A conflicting valid definition or offline execution fails explicitly. |

The data-bearing PostgreSQL phases intentionally refuse Alembic `--sql` mode.
Offline SQL cannot represent independently committed, data-dependent page loops or
repair an interrupted concurrent index truthfully. Generate reviewed DDL only to
the adjacent expand boundary, then run the named data revision with the dedicated
online migrator:

```bash
alembic upgrade 0025_system_time_validity --sql
alembic upgrade 0025a_system_time_backfill

alembic upgrade 0025a_system_time_backfill:0026_evidence_graph --sql
alembic upgrade 0026a_evidence_graph_backfill

alembic upgrade 0038_gate_policy_routing:0039_audit_append_boundary --sql
alembic upgrade 0039a_audit_append_contract

alembic upgrade 0039a_audit_append_contract:0040_gate_execution_permits --sql
alembic upgrade 0040a_gate_permit_contract

alembic upgrade 0040a_gate_permit_contract:0041_decision_record_integrity --sql
alembic upgrade 0041a_decision_integrity_idx

alembic upgrade 0041a_decision_integrity_idx:0042_recorder_integrity --sql
alembic upgrade 0042a_recorder_backfill

alembic upgrade 0043_evidence_impact_jobs:0044_durable_metering --sql
alembic upgrade 0045_evidence_scope_identity

alembic upgrade 0045_evidence_scope_identity:0046_operation_idempotency --sql
alembic upgrade 0046a_idempotency_backfill

alembic upgrade 0052_api_scale_indexes:0053_validmind_inventory --sql
# Quiesce PUT /api/v1/models/* and drain in-flight ValidMind writes.
alembic upgrade 0053a_validmind_backfill
# Reopen writes only after the revision is stamped and the link-sync trigger exists.

alembic upgrade 0053a_validmind_backfill:0054_otel_barrier --sql
# Quiesce /v1/traces and PUT /api/v1/models/*; drain both writer sets.
alembic upgrade 0054a_otel_barrier_contract
# Reconcile exact ValidMind link pairs before reopening model PUTs.
# Install the durable retention cursor before the authentication phases.
alembic upgrade 0055_retention_cursor

# Exact auth functions are rolling-compatible with old callers.
alembic upgrade 0056_auth_lookup_expand
# Build/repair the review index online while old callers may still serve.
alembic upgrade 0056a_admission_index
# Quiesce all authenticated routes, drain/stop old callers, then contract RLS.
alembic upgrade 0056b_auth_lookup_contract
# Install v3 authorization snapshots before starting the new writer pool.
alembic upgrade 0057_decision_auth_snapshot
```

Before each online phase, confirm the migrator can own or assume the application
table-owner role but cannot inherit `lians_runtime`. Watch `pg_stat_activity`, lock
waits, replica lag, WAL volume, dead tuples, temporary-file use, and disk headroom.
Cancellation is safe between committed pages. If the migration reports locked
remainders, canonical collisions, blank immutable identity fields, or invalid source
data, repair that condition explicitly and rerun; do not bypass the final validation.
Never infer a historical OTLP barrier from a model, service, trace, credential label,
or present-day configuration. `__legacy_restricted__` remains restricted unless an
authorized operator has immutable proof of the exact historical scope and records a
reviewed owner/migrator reclassification. Use PITR/forward repair after the contract;
do not treat Alembic downgrade as a way to erase the boundary.

## Key and credential rotation

Maintain an inventory of purpose, provider resource, active version/key ID, creation,
expiry, owner, consumers, and recovery dependency. Rotation changes are security
changes and follow the deployment sequence above.

### Decision Receipt signing keys

1. Generate Ed25519 key material inside the approved KMS/HSM or secret-generation
   boundary; never put the private key in a ticket, manifest, or backup.
2. Publish/register the new public issuer key before any receipt can name the new key
   ID. Verify consumers can refresh the registry.
3. Deploy the new `RECEIPT_SIGNING_KEY_ID` and private-key reference to a canary. Issue
   and independently verify a receipt.
4. Roll out, retain the old public key indefinitely for historical receipt validation,
   and remove old private signing access after the overlap window.
5. Revoke an old public key only for compromise or an explicit policy reason. Routine
   rotation should mark it retired for new signing but still valid for signatures made
   inside its validity interval.

### Master encryption/wrapping key

Lians uses self-identifying v2 envelopes, a bounded current/previous keyring, and an
offline advisory-locked operator that transactionally authenticates, rewraps, and
re-verifies every master-derived field. A persistent trigger fence and fixed-order
write-conflicting table locks close the undrained-replica write race. Production
rejects ambiguous key-version configuration. Follow
[the master-key rotation runbook](master-key-rotation.md), including verified
backup/restore, fence prepare/status/assert, and the independent zero-remaining
assertion before removing the previous slot. Preserve old provider key versions for
at least as long as encrypted backups that depend on them, and treat suspected
master-key loss as a potential permanent data-loss incident.

### API, SCIM, database, and metrics credentials

Create a second credential, grant the minimum role/scope/barrier, deploy consumers,
observe use of the new credential, then revoke the old credential. Do not mutate an
old secret in place when consumers cannot switch atomically. Database rotation must
respect pools and in-flight transactions. A break-glass credential stays offline,
requires two-person access, pages on use, and is rotated immediately afterward.

## Incident command

Declare severity based on impact, evidence loss, unauthorized access, or error-budget
burn—not the suspected component. A possible audit/receipt/queue data loss or key
compromise is severity 1 even if the API still returns 200.

For every incident:

1. Assign incident commander, operations lead, security/compliance lead when relevant,
   communications lead, and scribe. Use one UTC timeline.
2. Freeze deployments, migrations, retention, key rotation, and automation that could
   destroy or multiply evidence.
3. State customer/namespace scope, first/last known-good time, data categories,
   decision/Gate impact, and whether integrity or confidentiality is uncertain.
4. Preserve logs, audit exports, database/queue metadata, image/config digests, provider
   events, active credential/key IDs, and WORM object versions. Hash exports before
   handoff.
5. Mitigate with the smallest reversible action, then validate the exact safety
   invariant affected. Keep a decision log for every tradeoff.
6. Communicate at a fixed cadence. Do not promise no data loss until producer retries,
   collector counters, DB acceptance, and reconciliation establish it.
7. Close only after monitoring is stable, customer/compliance notices are decided,
   evidence is sealed, and remediations have owners/dates.

## API unavailable or readiness failing

1. Compare the external `/readyz` probe with API `/metrics` scrape, pod readiness,
   ingress, certificate, PostgreSQL, Redis, KMS, and DNS. Liveness must remain process
   only; dependency failure should drain traffic, not create a restart storm.
2. If a just-deployed image fails before any incompatible schema change, pause rollout
   and return to the pinned previous digest. Otherwise keep the new image stopped and
   use forward repair or isolated recovery.
3. For database saturation, stop backfills/exports first, identify blockers and pool
   exhaustion, and scale only within the provider's tested connection/IO limits.
4. For KMS failure, preserve encrypted records and restore KMS access. Never enable an
   unencrypted bypass in production.
5. Verify identity, receipt signing, Recorder ingest, Gate deny/allow, audit append, and
   a representative decision reconstruction after readiness returns.

## Collector or persistent-queue incident

The collector's `file_storage` PVC and sending queue are the outage buffer. The
OpenTelemetry project defines queue size/capacity, failed enqueue, failed send, and
receiver-refusal metrics in its
[internal telemetry guide](https://opentelemetry.io/docs/collector/internal-telemetry/).

1. Snapshot metrics per collector instance: accepted/refused spans, queue size and
   capacity, enqueue failures, send failures, sent spans, PVC free bytes, restarts,
   backend HTTP status, and first/last failure time.
2. Preserve every queue PVC. Do not delete the StatefulSet PVC, scale it down to zero,
   change queue storage format, or reduce configured queue capacity while data remains.
3. Restore API/auth/network flow. A `send_failed` increment means retries are occurring;
   it does not prove loss. `enqueue_failed` or `receiver_refused` can mean loss and
   requires producer retry/log reconciliation.
4. If disk approaches exhaustion, expand the PVC where supported and validated. New
   collector replicas provide new empty capacity but do not drain an existing pod's
   queue. Explicit upstream throttling is safer than uncontrolled disk exhaustion.
5. After recovery, watch queue size reach zero while `sent_spans` rises. Estimate and
   record the drain interval; avoid a rollout until all old queues are drained.
6. Correlate Recorder idempotency/deduplication results with producer retry records.
   Seal a time-range and affected-producer statement even when reconciliation finds no
   confirmed loss.

## Durable control-plane backlog or observability incident

The Recorder, integration outbox, exhaustive impact jobs, conflict review, and
retention scheduler publish tenant-free bounded metrics. Database-global inventory
gauges are repeated by API replicas; compare `max` across replicas and check
`lians_durable_inventory_refresh_healthy` plus its last-success timestamp before
trusting any backlog value.

1. If inventory refresh is stale, treat every durable gauge as unknown. Restore the
   replica's database/RLS access and refresher task; do not interpret an old zero as
   an empty queue. Compare replicas for refresh-time skew.
2. For Recorder rejections or waiting runs, preserve producer acknowledgements and
   collector queues, then use the authenticated readiness/event APIs to find capture
   gaps. Do not add tenant, error, event, or run identity to Prometheus labels.
3. For integration lag or dead letters, preserve outbox, delivery, and append-only
   attempt rows. Inspect authorized destination records, TLS/DNS/egress, retry timing,
   and receiver idempotency. Replay only through the audited replay API after the
   external outcome is reconciled.
4. For an old or failed impact assessment, compare the worker enabled, health,
   and heartbeat metrics with durable pending/running/failed gauges. Preserve
   the frozen coverage/link watermarks, exact snapshot decision count, scanned
   count, cursor, attempt count, lease timestamps, and bounded error code/digest.
   Restore database access or bounded worker
   capacity; do not clear leases with ad-hoc SQL, and never delete/recreate a job
   to reset its age.
5. Retention leadership contention is expected when another replica owns the cycle.
   A partial/failed cycle or stale heartbeat is not. Compare per-namespace committed
   prune audit rows with legal holds before a controlled retry; never issue a direct
   SQL delete.
6. Any audit append-boundary rejection is a data-integrity incident. Fence affected
   consequential mutations as necessary, preserve the failed transaction context,
   and inspect the database append function, grants, chain head, locks, and timeline.
   An `accepted` metric sample means boundary acceptance inside a transaction, not
   proof of outer commit; the database chain and source mutation are authoritative.
7. Close only after a successful inventory refresh, durable backlog reconciliation,
   worker/scheduler freshness, zero unexplained boundary rejections, and sealed UTC
   incident evidence. Scope affected tenants through authorized records, not metrics.

## PostgreSQL failover or corruption

- For infrastructure failover, use the managed service workflow, record old/new writer
  IDs and timelines, and let the stable endpoint move. Do not promote two writers.
- Measure the last acknowledged application write against the recovered writer. Verify
  audit append and chain topology before declaring zero loss.
- If logical corruption is possible, fence writers and follow the isolated
  [PITR runbook](backup-restore.md#pitr-recovery-runbook). Never restore over the
  primary and never delete the old cluster before investigation closes.
- Redis is not a source of recovery truth. Flush or replace it after database cutover
  to prevent cached pre-recovery objects from crossing timelines.

## Suspected audit, receipt, or approval tampering

1. Stop retention and any privileged mutation path; restrict administration while
   preserving read/export access.
2. Export affected namespace audit rows, receipt documents, issuer registry state,
   Gate records, immutable approval/review attestations, database timeline, and current
   chain tip. Hash and copy them to object-locked storage.
3. Verify every namespace without a row limit, recompute receipt signatures against the
   correct historical public key, and inspect chain forks/orphans and database audit
   events. A partial verifier result is not an all-clear.
4. Compare the database evidence with prior WORM exports/Merkle anchors. Escalate any
   mismatch to security/legal; do not “repair” history in place.
5. Recover into an isolated cluster if integrity is uncertain. Corrections must append
   a superseding record and incident attestation, never rewrite disputed evidence.

## Embedding provider or egress failure

1. Confirm `semantic_degraded` recall rates, provider status, DNS/TLS, quota, latency,
   and egress policy. Do not weaken TLS or open broad egress to restore service.
2. The lexical/degraded path preserves API availability but may change recall quality;
   notify affected decision owners when the degraded interval is material.
3. Restore the provider or approved alternate under the same data-residency policy.
   Validate a fixed recall corpus before closing and record the degraded time range.

## Certificate or trust-chain expiry

Renew through the managed certificate path, deploy the full chain, and verify SNI,
hostname, expiry, issuer, OCSP/CRL behavior where used, and client trust from outside
the cluster. Never set `insecureSkipVerify` as remediation. Rotate monitoring and OIDC
JWKS trust independently; a valid API certificate does not prove identity-provider
keys are current.

## Rollback decision matrix

| Condition | Action |
|---|---|
| Bad image, no schema change | Roll back to the pinned prior digest |
| Additive backward-compatible schema | Roll back app, leave additive schema, forward-fix later |
| Old app writes incompatible data under new schema | Stop writers; forward repair or isolated recovery |
| Contract/destructive migration ran | Do not run a destructive downgrade; PITR into a new cluster |
| Suspected credential compromise | Revoke/rotate credential; do not roll back to an image containing the same trust |
| Receipt/audit integrity uncertain | Preserve and isolate; never rewrite history to make verification pass |
| Collector queue schema/image incompatibility | Drain old queues on old pinned image before replacing |

After any rollback, verify the deployed image digest, current migration revision,
configuration checksum, active receipt key ID, identity/SCIM behavior, RLS, audit
append, Recorder deduplication, Gate decisions, queue drain, and SLOs. A Kubernetes
“rollout complete” message is not application recovery.

## Capacity management

Review weekly and before any large customer/backfill:

- API request rate, concurrency, CPU throttling, memory working set, event-loop delay,
  and database pool use per replica;
- PostgreSQL storage, IOPS/throughput, connections, query p95/p99, lock waits, dead
  tuples, autovacuum progress, largest indexes/tables, WAL/hour, archive lag, and
  replication/failover health;
- Redis memory, eviction, hit rate, connection saturation, persistence mode, and
  recovery time (while remembering Redis is disposable);
- Recorder spans/second, average and peak batch size, accepted/sent ratio, queue batches,
  queue bytes, PVC free space, collector memory limiter events, and drain rate;
- logical backup size/duration, PITR restore duration, WORM upload/verification lag,
  and restore validation duration; and
- external provider quotas for embeddings, KMS, object storage, OIDC/JWKS, SIEM,
  webhooks, ticketing, and network egress.

Keep at least 30% measured steady-state headroom. Load tests must include concurrent
Recorder ingest, evidence graph writes, Gate evaluation, investigation queries,
receipt signing, audit append, retention work, and backup/restore IO. Scale collector
PVC capacity from worst-case accepted spans/second × worst credible downstream outage,
using measured serialized batch bytes and at least a 2× uncertainty factor. The
configured `queue_size` counts batches, not spans or bytes, so both queue slots and PVC
bytes can be the first limit.

## Routine schedule

| Cadence | Control |
|---|---|
| Continuous | External readiness, API/collector/database/PITR/KMS metrics and paging |
| Daily | Logical backup, offline verification, WORM handoff/attestation, provider backup review |
| Weekly | Capacity review, full untruncated audit verification, credential/key-expiry review |
| Monthly | Isolated logical restore and application-level reconstruction sample |
| Quarterly | PITR and regional-recovery exercise; on-call and communications exercise |
| Before every release | Backup freshness, migration/rollback evidence, canary, alert coverage |
| After every incident | Evidence seal, RPO/RTO measurement, customer/compliance decision, remediations |
