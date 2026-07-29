# Migration 0028 staging-data rehearsal

Migration `0028_decision_envelopes` must pass against a sanitized copy of
staging before deployment. The rehearsal never mutates staging itself. It
restores the supplied dump into a disposable local PostgreSQL 16 plus pgvector
container.

## Required input

Provide a custom-format dump produced with `pg_dump --format=custom`. The dump
must be sanitized, approved for local use, and at Alembic revision
`0027_agent_experiences`. Do not place database credentials or the dump in Git.

If the only available input is a staging database URL, have an authorized
operator create and sanitize the dump first. Do not point Alembic at the live
staging database for the downgrade test.

## Run the rehearsal

From the repository root:

```powershell
.\scripts\rehearse_0028_staging_dump.ps1 `
  -DumpPath C:\secure-path\lians-staging-sanitized.dump `
  -ConfirmSanitized
```

The script:

1. Starts a uniquely named disposable `pgvector/pgvector:pg16` container.
2. Restores the dump without restoring ownership or privileges.
3. Assigns the restored schema to a non-superuser migration role so forced RLS
   remains active during the test.
4. Refuses to proceed unless the restored revision is exactly 0027.
5. Upgrades the restored schema to Alembic head.
6. Checks for missing and orphaned Decision Envelope relationships.
7. Downgrades migration 0028 to revision 0027.
8. Upgrades to head again and repeats the integrity checks.
9. Removes the disposable container, including the restored data.

Use `-KeepContainer` only when a failed rehearsal needs manual inspection. The
script prints the exact container name and never removes a container outside
the `lians-migration-rehearsal-*` namespace.

## Pass criteria

- Restore completes without ignored SQL errors.
- Upgrade, downgrade, and second upgrade all complete.
- Final Alembic revision is `0028_decision_envelopes`.
- Every decision record has a valid Decision Envelope.
- Every evidence link points to a valid Decision Envelope.
- The script exits with code 0.

Record only aggregate row counts and the final revision in the release ticket.
Do not attach the dump or row contents.
