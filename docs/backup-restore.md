# Backup, restore, and disaster-recovery contract

This document defines Lians' recoverability boundary. A backup is not considered
recoverable because a job reported success; it is recoverable only after an isolated
restore has passed structural, migration, RLS, index, constraint, and audit-topology
checks and the result has been retained as evidence.

## Recovery layers

| Layer | Purpose | Can provide PITR? | Minimum production posture |
|---|---|---:|---|
| Managed PostgreSQL HA | Survive an instance or availability-zone failure | No | Multi-AZ standby, automatic failover, provider-documented durability semantics |
| Managed base backups + continuous WAL archive | Recover from operator error, destructive migration, or logical corruption | Yes | PITR enabled continuously, restore window at least 35 days, latest-restorable-time monitored |
| Lians logical bundle | Portable, inspectable, independently checksummed copy | No | Daily `pg_dump` custom archive; isolated restore monthly |
| Object-locked copy | Prevent alteration or early deletion of backup evidence | No | Separate security boundary, locked retention, object-version evidence |
| Configuration/key escrow | Recover dependencies that PostgreSQL does not contain | No | Versioned infrastructure configuration, secret metadata, KMS recovery controls |

PostgreSQL explicitly distinguishes logical dumps from continuous archiving:
`pg_dump` creates a consistent single-database snapshot, while PITR requires a base
backup and a continuous sequence of WAL. See the PostgreSQL documentation for
[`pg_dump`](https://www.postgresql.org/docs/16/app-pgdump.html) and
[continuous archiving/PITR](https://www.postgresql.org/docs/16/continuous-archiving.html).

HA replicas are not backups. Replicas normally reproduce an accidental `DROP`, bad
write, or compromised credential quickly and correctly.

## Managed PostgreSQL production contract

The chosen service must meet this contract in writing. Record the provider, plan,
region, database resource ID, and evidence link in the change record.

- PostgreSQL 16 or a tested newer major and the required `vector` extension.
- At least two fault domains with provider-managed automatic failover. Record whether
  acknowledged commits are synchronously durable across fault domains; do not infer
  zero RPO from the words “high availability.”
- Continuous PITR with a documented latest-restorable-time signal and at least a
  35-day window. Alert when archiving or backup freshness falls outside the target.
- Encrypted storage and backups under a customer-controlled key where required.
  Backup-key deletion protection and recovery procedures must outlive the backups.
- Automated daily backup status exported to the monitoring account. Provider console
  screenshots alone are not adequate monitoring.
- A second-region or second-account recovery copy for workloads whose regional RTO
  cannot be met by restoring the primary-region service.
- A connection endpoint that follows the writer after failover, TLS verification,
  connection limits, maintenance-window controls, and advance major-version notices.
- Provider audit logs for restore, snapshot deletion, retention changes, failover,
  database parameter changes, and KMS administration sent to the security archive.

Provider labels such as “multi-AZ” and “continuous backup” are not interchangeable
across vendors. Validate the actual replication mode, WAL loss boundary, restore
granularity, backup retention, and regional-failure behavior before accepting the
service.

## Service recovery objectives

These are engineering targets, not claims about the current deployment. A production
launch gate must attach drill evidence showing the selected provider and team can meet
them at representative data volume.

| Failure | Target RPO | Target RTO | Recovery path |
|---|---:|---:|---|
| API pod/node failure | 0 committed DB transactions | 10 minutes | Kubernetes reschedule/rollout |
| Primary database instance/AZ failure | Provider durability contract; target 0 acknowledged transactions | 15 minutes | Managed failover |
| Bad application release | 0 committed DB transactions | 15 minutes | Schema-compatible image rollback |
| Accidental write or destructive migration | 5 minutes | 60 minutes | Isolated PITR, verify, controlled cutover |
| Primary region unavailable | 15 minutes | 4 hours | Cross-region replica or restore copy |
| Redis loss | 0 authoritative records | 15 minutes | Replace cache; warm from PostgreSQL |
| Collector-to-API outage | 0 queued spans while queue has capacity | 30 minutes | Disk-backed queue drains after API recovery |
| Queue overflow or producer refusal | Not guaranteed | Immediate incident | Preserve evidence, quantify affected spans, reconcile producer retries |

If a contracted customer needs tighter objectives, the architecture must change to
meet them before the contract is signed; changing this table does not change physics.

## Create a logical backup

The operator scripts use standard libpq variables. Do not put a connection URL on the
command line. Mount a short-lived `PGPASSFILE` with mode `0600`, or use the platform's
workload identity/database authentication mechanism.

```bash
export PGHOST='writer.db.internal'
export PGPORT='5432'
export PGDATABASE='agentmem'
export PGUSER='lians_backup'
export PGPASSFILE='/run/secrets/pgpass'
export PGSSLMODE='verify-full'
export DEPLOYMENT_ENVIRONMENT='production'
export LIANS_DATABASE_ID='provider-resource-id-of-primary'

install -d -m 0700 /var/lib/lians-backups
python ops/backup/create_logical_backup.py \
  --output-dir /var/lib/lians-backups \
  --retention-until 2033-08-02T00:00:00Z \
  --include-globals
```

The backup role needs `CONNECT`, visibility of every Lians schema object, and read
access that bypasses tenant RLS for backup purposes. It must not be the application
role and must not own application tables. Scope and audit its use. `pg_dumpall
--globals-only` can require elevated catalog visibility on managed services; if the
provider forbids it, preserve declarative role/grant definitions separately and omit
`--include-globals`.

The tool:

- refuses an implicit host or database and refuses an unidentified production source;
- checks local free space before starting;
- fails rather than wait indefinitely for an incompatible DDL lock;
- creates a consistent custom-format archive without subscriptions;
- confirms `pg_restore` can read the archive table of contents;
- records migration, table, RLS, extension, integrity, WAL-position, and audit-topology
  observations without recording a password or connection URI;
- hashes every artifact and the canonical manifest; and
- atomically publishes the bundle only after it is complete.

`manifest.json` and `SHA256SUMS` detect corruption after the bundle leaves the host.
They are not signatures and cannot defeat an attacker who can replace the entire
bundle. The create-only, object-locked copy of the canonical provider attestation,
addressed by its digest and revalidated through the provider API, supplies the
external integrity and authenticity anchor within the provider's audited IAM
boundary.

Verify any bundle before transport or restore:

```bash
python ops/backup/verify_backup.py /var/lib/lians-backups/lians-20260802t020000z
```

Store the job result, bundle checksum root, size, duration, source database ID, and
WORM handoff status as monitoring data. Never log `PGPASSWORD`, `PGPASSFILE` content,
database URLs, decrypted evidence, or encryption keys.

## WORM/object-lock handoff

Generate a handoff request only after the local bundle verifies:

```bash
python ops/backup/prepare_worm_handoff.py \
  /var/lib/lians-backups/lians-20260802t020000z \
  --destination s3://lians-immutable-prod/logical \
  --retention-until 2033-08-02T00:00:00Z \
  --output /var/lib/lians-handoffs/lians-20260802t020000z.json
```

The handoff remains `pending_provider_attestation`. An uploader running under a
separate workload identity must upload every listed object with the requested locked
retention, then write a provider response containing:

- immutable bucket/container policy revision and owning account/project/tenant;
- object version ID or generation for every artifact;
- provider-reported checksum and effective retain-until timestamp for every object;
- effective retention mode and legal-hold state; and
- verifier workload identity and verification time.

Use the fail-closed uploader and provider setup in the
[verified WORM provider handoff runbook](worm-provider-handoff.md). It supports S3
Object Lock, GCS Object Retention Lock/holds, and Azure version-level immutable
blobs. It emits a schema-validated core provider attestation and hash sidecar,
uploads the exact canonical core bytes create-only under a deterministic
digest-derived name in the same locked/versioned prefix, and emits a derived anchor
record plus its hash sidecar. The anchor records the exact provider object ID,
checksums, retention, and hold state. A pending handoff or a successful local backup
job is never equivalent to a four-file result that has passed standalone provider
verification.

Revalidate the core attestation, both local JSON/checksum pairs, and the immutable
provider object whenever the result is moved or used as recovery evidence. The
standalone verifier uses the provider SDK default workload identity and exposes no
credential options:

```bash
/opt/lians-worm-venv/bin/python /opt/lians-backup/verify_worm_attestation.py \
  /var/lib/lians-handoffs/lians-20260802t020000z.provider-attestation.json
```

The provider object anchors the exact core bytes without introducing an application
signing secret. Its assurance still depends on independently governed provider IAM,
audit logs, retention administration, and control-plane monitoring; retain the core,
`<core>.sha256`, `<core>.anchor.json`, and `<core>.anchor.json.sha256` together.

Do not treat an ETag as SHA-256; multipart object-store ETags are commonly not content
hashes. Re-download a scheduled sample through a read-only identity and compare the
Lians SHA-256. Keep upload, retention administration, deletion, and verification in
separate roles. Deny retention shortening and version deletion. Keep a duplicate in a
different account/project and preferably a different region.

AWS S3 Object Lock, Azure immutable Blob storage, and Google Cloud locked retention
policies have different enablement and governance behavior. Use the provider API to
verify the effective object version, not merely the upload request. `WORM_MODE=true`
is an attestation by the operator; set it only after these external controls are
actually verified. Consult the primary service documentation for
[S3 Object Lock](https://docs.aws.amazon.com/AmazonS3/latest/userguide/object-lock.html),
[Azure immutable Blob policies](https://learn.microsoft.com/azure/storage/blobs/immutable-policy-configure-version-scope),
and [Google Cloud Bucket Lock](https://cloud.google.com/storage/docs/bucket-lock).

## Isolated restore drill

Provision a new PostgreSQL cluster, not a new database on the primary cluster. Give
it a unique managed-service resource ID, create an empty database named
`lians_restore_<identifier>`, install only server-level prerequisites, and set an
explicit nonproduction environment. The restore program has no `--clean`, overwrite,
same-cluster, or production mode.

```bash
export PGHOST='ephemeral-restore.db.internal'
export PGPORT='5432'
export PGDATABASE='lians_restore_20260802'
export PGUSER='lians_restore_owner'
export PGPASSFILE='/run/secrets/restore-pgpass'
export PGSSLMODE='verify-full'
export LIANS_TARGET_DATABASE_ID='provider-resource-id-of-ephemeral-cluster'
export LIANS_PRIMARY_DATABASE_ID='provider-resource-id-of-primary'

python ops/backup/restore_drill.py \
  /var/lib/lians-backups/lians-20260802t020000z \
  --target-environment restore-drill \
  --acknowledge-target lians_restore_20260802 \
  --report-dir /var/lib/lians-restore-reports
```

Before restoring, the tool verifies all bundle hashes and refuses:

- `prod` or `production` targets;
- a target database ID matching the backup source or configured primary;
- an endpoint fingerprint matching the source/configured primary;
- the same PostgreSQL system identifier when catalog permission exposes it;
- an acknowledgement different from `PGDATABASE`;
- a target name outside the restore-only naming convention;
- a standby/read-only target; or
- a target containing any user relations.

The restore is a single transaction with `--exit-on-error`, without source ownership,
ACLs, subscriptions, or tablespace bindings. PostgreSQL warns that restoring a dump
executes code selected by the source database's privileged users. Restore only bundles
from a trusted Lians source and inspect a suspect archive as SQL in a quarantined
environment before execution. See the official
[`pg_restore` safety and transaction behavior](https://www.postgresql.org/docs/16/app-pgrestore.html).

Automated post-restore checks compare migration revisions and table/RLS inventory,
reject invalid indexes or unvalidated constraints, enforce the pre-dump audit-event
lower bound, and detect audit-chain forks/orphans. They do not recompute Lians'
versioned event hashes. Before a drill passes, also:

1. Start the exact backed-up application version against the isolated target with
   outbound webhooks, email, ticketing, SCIM, and model/tool execution blocked.
2. Run application-level audit verification for every namespace, with no truncation.
3. Reconstruct a stratified sample of Decision Receipts and verify issuer signatures,
   evidence links, Gate decisions, immutable approvals, and review history.
4. Confirm subject-key decryption with a drill-scoped KMS grant; never export plaintext
   keys into the report.
5. Compare critical namespace/record counts against a source observation captured at
   the recovery point.
6. Measure restore start, database-ready, application-ready, and verification-complete
   timestamps against RTO.
7. Send the restore report and checksum to the immutable evidence archive.
8. Revoke the drill KMS grant and destroy the entire ephemeral cluster under the
   provider's audited deletion workflow.

Run a logical restore monthly and a PITR restore quarterly. Alternate full-region and
operator-error scenarios. A failed drill opens a production-severity remediation item;
do not wait for the next scheduled drill.

## PITR recovery runbook

1. Declare an incident, appoint incident commander and scribe, freeze deploys,
   migrations, retention jobs, SCIM writes, and key rotation.
2. Record the suspected corruption interval in UTC, primary resource ID, current
   database timeline/LSN when available, and latest-restorable-time. Preserve logs and
   object versions.
3. Choose a recovery target before the first known bad transaction with a documented
   safety margin. Never restore over the primary.
4. Restore the managed snapshot/WAL stream into a new isolated cluster. Record the
   provider restore job ID, target timestamp, resulting database ID, and timeline.
5. Apply the same verification sequence used by a restore drill. If the bad event is
   still present, destroy the candidate and retry from an earlier target.
6. Stop or fence writers. Capture a final forensic backup of the old primary. Decide
   how post-target legitimate writes will be reconciled; do not silently discard them.
7. Rotate database credentials for the new writer, update the secret reference, and
   cut over through the stable writer endpoint. Keep outbound side effects disabled
   until identity, Gate, audit, and Recorder checks pass.
8. Watch error budget, audit-chain append behavior, replication, queue drain, and
   decision reconstruction. Keep the old primary read-only and quarantined until the
   incident owner authorizes its deletion.
9. Seal the timeline, evidence, data-loss statement, and recovery measurements in the
   incident record.

## Capacity and backup-window planning

- Track database bytes, daily growth, WAL bytes/hour at p50/p95/peak, largest tables,
  dead tuples, backup duration, archive size, restore duration, and index-build time.
- Keep primary storage below 70% in normal operation and preserve headroom for VACUUM,
  index creation, and migration rewrites. Alert at provider-specific thresholds before
  storage auto-growth or IOPS ceilings become the incident.
- Reserve logical-backup scratch space at least `max(1 GiB, 1.2 × database size)` by
  default. Measure actual compression and revise upward; compressed dumps can still be
  large for encrypted or already-compressed payloads.
- Size PITR retention from `base-backup bytes + peak WAL bytes/hour × retention hours`,
  then add provider and regional-copy overhead. WAL spikes during backfills and index
  builds must fit.
- Benchmark restores at 1× and projected 2× six-month volume. RTO includes provisioning,
  transfer, replay, index creation, application startup, validation, and cutover—not
  only the provider restore job.
- A custom-format archive restores serially when `--single-transaction` is required.
  If it cannot meet RTO, keep PITR as the primary path and evaluate a directory-format
  logical archive with parallel restore as a separately tested profile. PostgreSQL
  does not permit parallel jobs together with single-transaction restore.
