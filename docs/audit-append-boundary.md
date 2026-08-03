# Database-owned audit append boundary

Production audit ordering and integrity are created by PostgreSQL, never trusted
from a runtime-supplied row. Expand migration `0039_audit_append_boundary` and
online contract revision `0039a_audit_append_contract` install a
`SECURITY DEFINER` append function and a single trigger-backed append primitive.
The latter backfills bounded committed frontier pages and repairs an interrupted
concurrent position-index build before briefly fencing the final write tail.
That primitive locks a per-namespace head, advances a monotonic chain position,
chooses the current predecessor and database time, canonicalizes JSONB, and
computes the v3 SHA-256 hash. Wall-clock time is never ordering authority.

During the documented 0.4.2 to 0.5 rolling window, `lians_runtime` temporarily
retains `INSERT` so an old pod can continue its historical direct-insert call.
The `BEFORE INSERT` boundary discards the caller's timestamp, predecessor,
position, hash version, and hash and replaces them under the same namespace
lock; an `AFTER INSERT` boundary advances the protected head in the same
transaction. Updates, deletes, and truncation remain forbidden. This is an
expand-phase compatibility path, not a second integrity implementation.

This control needs three distinct identities:

- a schema-owning migration login that runs reviewed Alembic migrations;
- `lians_runtime`, a fixed `NOLOGIN NOSUPERUSER NOBYPASSRLS` capability role;
- a non-owner runtime login that is a member of `lians_runtime`, has only the
  temporary trigger-mediated `INSERT` capability plus read access on
  `event_log`, and has no `UPDATE`, `DELETE`, or `TRUNCATE` privilege.

The cluster administrator must provision the capability role before migration:

```sql
CREATE ROLE lians_runtime NOLOGIN NOSUPERUSER NOBYPASSRLS;
CREATE ROLE lians_app LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOBYPASSRLS;
GRANT lians_runtime TO lians_app;
```

If `lians_runtime` is absent or has unsafe attributes, migration 0039 refuses to
run. The migration grants ordinary business-table DML and sequence access to
the capability role, configures matching migrator-owned default privileges,
then narrows `event_log` to read, trigger-mediated rolling `INSERT`, and exact
append/hash function execution. `PUBLIC` receives neither insert nor
audit-function execution.

The migration login and runtime login must use different credentials. The
migration login must not be placed in `lians_runtime`, and its credential must
not be mounted into an API pod. Do not make the API login a relation owner;
ownership, `SUPERUSER`, and `BYPASSRLS` defeat ordinary ACL checks. Any
pre-existing direct audit-table grant to that login must be revoked explicitly.

At startup, production inspects PostgreSQL catalogs and refuses traffic unless:

- the runtime is neither superuser, RLS-bypass, table owner, nor function owner;
- the fixed capability role exists with safe attributes and grants effective
  function execution to the runtime;
- `PUBLIC` cannot execute either audit function;
- rolling `INSERT` is present, mutation DML is unavailable, and all four
  boundary/mutation triggers are enabled;
- both hash columns and constraints are present;
- the insert and head-advance trigger functions remain `SECURITY DEFINER`; and
- `FORCE ROW LEVEL SECURITY` remains enabled on the protected identity,
  governance, subject-key, and audit tables.

`GET /v1/platform/readiness` repeats this live inspection as
`audit.append_boundary`. It returns only safe role/control state, never database
credentials.

The boundary prevents a compromised runtime process from rewriting the core
chain. It does not make a schema owner, cluster administrator, storage system,
or backup repository untrusted. Retain independently verified chain tips and
provider-native WORM backups as described in [worm-storage.md](worm-storage.md).

Process-local Merkle windows are intentionally rejected in production until
window membership and anchor publication are transactionally durable. The v3
per-event chain remains the authoritative production audit path.

Remove the compatibility `INSERT` grant and old-shape trigger support only in a
future contract release after every 0.4.2 writer is gone and the reconciliation
and observation gates in [the 0.5 rolling-upgrade runbook](rolling-upgrade-0.5.md)
have passed.
