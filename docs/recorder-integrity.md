# Universal Recorder integrity boundary

Migration `0042_recorder_integrity` installs the expand-safe defaults and
projection boundary. Online revision `0042a_recorder_backfill` adds
historical run sentinels in committed pages and builds established-table
indexes concurrently with invalid-index recovery.

Universal Recorder events separate two identities that are easy to conflate:

- `agent_id` and envelope `actor` fields are caller-reported labels. The API
  marks them `claimed_unverified` or `not_supplied`.
- `ingested_by_principal_ref`, `ingested_by_auth_method`, and the opaque
  `ingested_by_credential_id` are derived from the credential Lians actually
  authenticated. The credential ID is not a key, token, or secret.

Every new event uses canonical hash version 2. Its SHA-256 commitment covers all
immutable event fields, including tenant and barrier scope, run identity,
source and normalized data, caller claims, capture diagnostics, both timestamps,
and authenticated ingestion provenance. Lians then records the event ID, run
ID, and event hash in exactly one immutable `recorder_ingest` core-audit entry.
Reads that return or promote event evidence verify both commitments.

## Historical events

The migration does not invent authenticity for existing history. Pre-migration
events keep their original v1 hash and receive these explicit markers:

```text
event_hash_version: 1
ingested_by_principal_ref: lians:principal:v1:legacy-unverified
ingested_by_auth_method: legacy_unverified
ingested_by_credential_id: null
```

Legacy actor attribution is conservatively marked `claimed_unverified`. A v1
event can still be useful historical evidence, but it is not proof of who sent
it and must never satisfy a control requiring authenticated provenance.

These constant legacy defaults also accept the 0.4.2 event insert shape during
the 0.5 rolling window. A database projection trigger adds the legacy principal
and authentication method to the run's producer summaries, so mixed-version
runs remain conservatively labelled. The 0.5 writer supplies verified v2
provenance explicitly. The defaults and compatibility projection are removed
only in a future contract release after old writers have been retired and the
run summaries reconciled.

## Database enforcement

PostgreSQL rejects every `UPDATE`, `DELETE`, and `TRUNCATE` of
`recorder_events`, even if a grant is accidentally broadened. `PUBLIC` and the
fixed `lians_runtime` capability role are denied those operations;
`lians_runtime` retains `SELECT` and `INSERT`. The migration asserts that the
role is `NOLOGIN`, `NOSUPERUSER`, and `NOBYPASSRLS`, and that both Recorder
tables still have forced row-level security. Existing namespace and restrictive
information-barrier policies are left in place.

The producer lists on `recorder_runs` are mutable denormalized summaries used
for readiness and discovery. They are not integrity proofs. Consult immutable
events and their audit bindings when authenticating evidence.

## Trust boundary

The runtime keeps direct `INSERT` permission because it is the service's append
capability. A database owner, superuser, or compromised runtime process remains
inside the trusted computing base and could manufacture a new internally
consistent row. The API rejects rows without their exact core-audit binding on
authoritative reads, but operators must still protect database administration,
runtime-role membership, migration credentials, and backups. This boundary is
append-only evidence integrity, not protection from a fully compromised
database control plane.
