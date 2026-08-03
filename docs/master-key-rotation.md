# Master encryption key rotation

Lians rotates its master encryption key with a bounded, two-key rollout, a
persistent database write fence, and an offline transactional rewrite. New
values are self-identifying v2 envelopes. Readers accept the configured current
key and at most one previous key; legacy v1 values try those two candidates in
that order. The operator never prints plaintext, ciphertext, or key material.

This is an operationally destructive boundary even though the database update
is transactional. Rehearse it against a restored production backup before the
first production rotation. Never delete provider key material merely because it
has been removed from the live application configuration: old backups still
depend on the key that was current when they were created.

## Encrypted-field inventory

The operator inventories, authenticates, and (when needed) rewrites:

- `subject_keys.enc_key` (wrapped per-subject DEKs);
- `pending_admissions.content`;
- `webhook_endpoints.secret`;
- `gate_approval_attestations.statement_encrypted`;
- `decision_review_events.note_encrypted`;
- `integration_destinations.secret_config_encrypted`;
- `integration_outbox_events.payload_encrypted`; and
- `control_closure_attestations.statement_encrypted`, including verified
  conversion of legacy plaintext closure statements.

Approval, review, closure, and integration outbox rows remain logically
immutable. The operator checks the exact expected append-only and fence
triggers, function signatures, function/schema ownership, validated
constraints, and table-owner privileges. It disables only the four named
append-only update triggers inside its transaction, verifies the original
immutable hashes, rewrites only encryption storage, verifies the hashes again,
and re-enables the triggers before commit. The persistent fence triggers remain
enabled throughout. A failed transaction rolls back data, fence state, and
transactional trigger state together.

## Configuration contract

`MASTER_KEY_ID` is a non-secret, stable identifier embedded in new envelopes.
It must contain 1–64 ASCII letters, numbers, `.`, `_`, or `-`, beginning with a
letter or number. Production refuses a missing or unsafe identifier.

During rotation, set `MASTER_KEY_PREVIOUS_ID` and exactly one previous-material
slot for the selected `KMS_PROVIDER`:

| Provider | Current material | Previous material during overlap |
|---|---|---|
| AWS KMS | `KMS_AWS_ENCRYPTED_KEY`, optional `KMS_AWS_KEY_ID`/region | `KMS_AWS_PREVIOUS_ENCRYPTED_KEY`, optional previous key ID/region |
| Azure Key Vault | `KMS_AZURE_VAULT_URL` + `KMS_AZURE_SECRET_NAME` | `KMS_AZURE_PREVIOUS_SECRET_NAME`, optional previous vault URL |
| Vault KV v2 | `KMS_VAULT_PATH` + mount/address/token | `KMS_VAULT_PREVIOUS_PATH`, optional previous mount/address |
| Environment (development only) | `MASTER_ENCRYPTION_KEY` | `MASTER_ENCRYPTION_KEY_PREVIOUS` |

Do not overwrite an Azure secret or Vault path in place. Create a distinct
current version/name/path so both materials remain independently addressable.
Lians rejects a previous ID without material, material without an ID, material
for a provider other than `KMS_PROVIDER`, identical IDs, and identical key bytes.
When a verified database checkpoint exists, startup also requires its last
current key ID to be present as either the loaded current or previous key and
requires every recorded remaining count to be zero. When a database fence is
prepared, startup permits only keyrings contained in its explicit two-ID set.
After narrowing, the process's current key ID must be the fence's sole write ID.
This blocks an accidental single-key jump and makes an undrained old-current
replica fail closed at the database even if it was already running.

## Prerequisites

1. Deploy the release containing Alembic revision
   `0037_master_key_write_fence` while the old key remains current. Set a real
   `MASTER_KEY_ID` for that old key. Do not configure a previous slot yet. The
   migration installs dormant triggers; no fence is active until the offline
   operator writes the singleton state row.
2. Wait until every API, worker, and one-off job runs a v2-capable release. A
   missed legacy writer will be rejected after fence preparation, but draining
   it first avoids an availability incident.
3. Run a dry inventory and retain its JSON report:

   ```bash
   python ops/keys/rotate_master_key.py --report-file rotation-preflight.json
   ```

4. Create a new logical backup after revision `0037` with a stable
   `LIANS_DATABASE_ID`, verify it with
   `ops/backup/verify_backup.py`, and complete an isolated restore drill. The
   apply command independently re-verifies every bundle checksum and requires
   the backup to contain the same single Alembic head as the live database (with
   the rotation schema present), the same database ID, database name, and
   endpoint fingerprint and, when the source role can observe it, PostgreSQL
   system identifier. By default the
   backup must be no more than 24 hours old.
5. Preserve the old key and its provider access policy for the full retention
   life of all pre-rotation backups. Confirm deletion protection, two-person
   recovery authorization, monitoring, and a tested way to restore the old
   configuration.
6. Provision a new independent 32-byte AES key in the same selected provider.
   Record provider object versions and access-policy changes in the change case;
   never record plaintext key material.

## Envelope contracts

New sealed strings use
`lians-sealed:v2:<key-id>:<base64url(nonce || ciphertext || GCM-tag)>`.
The AES-256-GCM key is derived with HKDF-SHA256 using
`lians/<purpose>/v2` as the info value. The authenticated data binds the full
version/key prefix, a NUL separator, and the field-specific row context.

Wrapped subject DEKs use the binary header
`"lians-dek:v2\0" || uint8(key-id-length) || key-id`, followed by a 12-byte
nonce and AES-GCM ciphertext/tag. The entire header is authenticated data.
The v2 wrapping key is purpose-separated with HKDF-SHA256 info
`lians/subject-dek-wrap/v2`.
Readers treat values without that magic as legacy `nonce || ciphertext/tag`
wrappers and try only current then previous material. A v2 value naming any
other key fails immediately; it never falls back across candidates.

## Fenced rolling lifecycle

Suppose `mk-2026-01` is the old key and `mk-2026-08` is the new key.

1. In the protected operator environment, configure the new key as current:

   ```text
   MASTER_KEY_ID=mk-2026-08
   MASTER_KEY_PREVIOUS_ID=mk-2026-01
   ```

   Point the current provider fields at the new material and the matching
   previous fields at the old material.
2. Prepare the persistent fence using the verified post-`0037` backup:

   ```bash
   python ops/keys/rotate_master_key.py \
     --prepare-write-fence \
     --backup-bundle /secure/backups/<backup-id> \
     --database-id <stable-production-database-id> \
     --report-file /secure/reports/master-key-fence-prepared.json
   python ops/keys/rotate_master_key.py --assert-write-fence-prepared
   ```

   Preparation accepts no key-ID arguments. It derives the only allowed pair
   from the loaded, validated current/previous keyring. Before activating the
   row, it takes `SHARE ROW EXCLUSIVE` locks in a fixed order on all eight
   protected tables. Those locks wait for pre-existing writes to finish and
   block new writes until commit. The persistent triggers then reject plaintext,
   v1, malformed v2, and any v2 identifier outside exactly
   `{mk-2026-08, mk-2026-01}`. Existing legacy rows may remain for the apply
   transaction to authenticate and rewrite.
3. Roll API and worker replicas to the same new-current/old-previous keyring.
   New writes name `mk-2026-08`; an old-current v2 replica can temporarily write
   `mk-2026-01`, while any still-v1 process fails closed at the database.
4. Inspect the state and run another dry inventory:

   ```bash
   python ops/keys/rotate_master_key.py --write-fence-status
   python ops/keys/rotate_master_key.py --report-file rotation-preflight.json
   ```

   Investigate any `unknown` value or verification error. Drain old replicas
   for availability; correctness no longer depends on perfect drain detection.
5. Apply the rewrap from the protected operator environment:

   ```bash
   python ops/keys/rotate_master_key.py \
     --apply \
     --backup-bundle /secure/backups/<backup-id> \
     --database-id <stable-production-database-id> \
     --report-file /secure/reports/master-key-rotation.json
   ```

   The command takes the advisory lock and the same fixed-order
   `SHARE ROW EXCLUSIVE` table locks. It holds them through complete inventory,
   authenticated rewrite, immutable-hash verification, checkpoint, and fence
   narrowing. The transaction commits only if legacy, previous-key, unknown-key,
   and plaintext counts are zero. In that same commit, the fence changes from
   `{mk-2026-08, mk-2026-01}` to `{mk-2026-08}`. A racing write finishes before
   inventory or waits until commit; an undrained old-current replica's next
   write is rejected afterward.
6. Assert the persistent fence and independent removal inventory while both
   keys remain configured:

   ```bash
   python ops/keys/rotate_master_key.py --assert-write-fence-narrowed
   python ops/keys/rotate_master_key.py --assert-safe-to-remove-previous
   ```

7. Remove `MASTER_KEY_PREVIOUS_ID` and the selected provider's previous-material
   configuration from the live service, roll every replica, and verify
   `/v1/platform/readiness`. Do **not** destroy the provider key while any
   retained backup may require it.

The advisory lock serializes operators. The table locks serialize the rotation
boundary with application writers. Persistent triggers enforce the boundary
after commit. Replica draining remains important for availability and read
compatibility, but a missed old-current writer can no longer repopulate the old
key after the verified inventory.

## Bounded-maintenance alternative

For a database whose full inventory/rewrap cannot comfortably complete inside
one long transaction, prepare and assert the dual-ID fence first, then use a
change window after the dual-key release is fully deployed: drain write traffic,
pause delivery/admission workers, wait for active transactions to finish, run
dry-run then apply, assert the narrowed fence and zero remaining values, and
restore traffic. Do not split the rewrite into independently committed table
batches; that would weaken rollback and checkpoint guarantees. Reduce load or
increase the window instead.

## Failure and rollback

- A dry run, fence status, and fence assertions never write data or a checkpoint.
- Fence preparation writes only the bounded state row, after locking all value
  tables and verifying their inventory. It never accepts operator-supplied IDs.
- Any authentication, immutable-hash, schema, privilege, trigger, backup, count,
  or post-rewrite verification failure aborts the transaction. Keep both keys
  configured, preserve the failure report, and investigate before retrying.
- Before apply, keep both keys. To reverse target and previous roles, configure
  old-current/new-previous and run `--prepare-write-fence` again with a fresh
  verified backup. The operator permits that reversal only because the allowed
  two-ID set is unchanged; then roll replicas and apply the verified rewrap back.
- After a successful apply, every live value names the new key and the fence is
  narrowed. To roll back, keep the dual-key-capable release, configure old as
  current with new as previous, take a fresh verified backup, prepare the next
  fence generation, roll replicas, and apply the rewrap back to old. Never
  deploy a single-old-key release against data or a fence already narrowed to
  the new key.
- Restore the pre-rotation backup only through the isolated restore and incident
  procedures. It requires the old key as current (or in the bounded previous
  slot) and loses post-backup writes, so it is a disaster-recovery action, not a
  normal rotation rollback.

The readiness endpoint reports the current non-secret key identifier, whether a
previous slot is configured, inactive/prepared/narrowed fence signals, whether
the latest checkpoint matches the loaded keyring and narrowed fence, checkpoint
zero/nonzero remaining signals, and live per-namespace plaintext closure count.
Exact deployment-wide counts and the fence's bounded IDs remain in the protected
operator report. These are operational signals, not proof that provider-side
key deletion or backup expiration has occurred.

Machine reports conform to
`ops/keys/schemas/master-key-rotation-report-v1.schema.json`.
