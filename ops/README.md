# Lians operations assets

- `backup/create_logical_backup.py` creates an atomic, checksummed PostgreSQL
  logical-backup bundle.
- `backup/verify_backup.py` verifies the sealed bundle and custom archive offline.
- `backup/prepare_worm_handoff.py` creates a still-pending immutable-storage handoff
  request. It deliberately does not claim that an upload is WORM.
- `backup/restore_drill.py` restores only into an explicitly identified, isolated,
  empty, nonproduction database and emits a checksummed report.
- `backup/schemas/` contains the JSON Schema contracts for generated documents.
- `prometheus/lians-rules.yaml` contains API SLO, Recorder persistent-queue,
  durable integration/impact inventory, retention, and audit-boundary rules.

Run Python tools by path so their dependency-free shared module resolves from the
same directory. Database tools require PostgreSQL 16 client binaries and standard
libpq `PG*` environment variables. Operational procedures and safety boundaries are
in [`docs/production-operations.md`](../docs/production-operations.md),
[`docs/backup-restore.md`](../docs/backup-restore.md), and
[`docs/slo-alerting.md`](../docs/slo-alerting.md).

These assets are not a substitute for a managed PostgreSQL PITR service, immutable
object-storage policy, monitoring deployment, or successful recovery drill.
