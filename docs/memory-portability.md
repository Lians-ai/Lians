# Lians memory portability

Lians portable memory is a user-controlled boundary for device migration,
encrypted backup, and future zero-knowledge cloud synchronization. It is not a
plaintext export and it does not make the local device key portable.

## Format and privacy contract

`*.liansbackup` version 1 is a bounded JSON envelope containing only:

- the format and version;
- fixed scrypt parameters plus a random 128-bit salt;
- AES-256-GCM plus a random 96-bit nonce; and
- one authenticated ciphertext value.

Memory content, source metadata, project IDs, activity, receipts, counts,
profile name, backup ID, and creation time are all inside the ciphertext. The
portable key is derived from the user's passphrase with scrypt (`N=32768`,
`r=8`, `p=1`). The device's local root key is never placed in the bundle.

The first format is capped at 128 MiB and 100,000 records per collection. Lians
rejects unknown envelope fields, altered cipher/KDF parameters, invalid base64,
an incorrect passphrase, authenticated-ciphertext changes, malformed records,
content-hash or token-count mismatches, broken or cyclic lineage, missing
references, credential-like memory, and invalid Ed25519 receipt signatures.

## Export and verification

```bash
lians backup export --output "Lians Memory.liansbackup"
lians backup verify --input "Lians Memory.liansbackup"
```

Export requires a passphrase of at least 12 characters and confirmation. Output
uses an atomic no-overwrite publish by default. Use `--overwrite` only after
deliberately choosing to replace that exact backup path. Verification decrypts
and validates the full bundle without changing local memory.

The local Lians App exposes this as **Move memory safely**: download an
encrypted backup, or choose a backup and review its record counts before a
separate import action. App uploads are capped at 32 MiB to bound loopback
request memory; the CLI accepts the format-wide 128 MiB limit.

The passphrase is read from the terminal without echo and is deliberately not a
CLI option. This keeps it out of shell history and process listings. Lians has
no recovery copy, so users must keep the passphrase separately from the backup.

Automation may use `--passphrase-file /protected/path` instead of an interactive
prompt. On macOS and Linux Lians rejects a file readable by group or other
users. The secret itself is still never placed in the process arguments.

## Import and conflict behavior

```bash
lians backup import --input "Lians Memory.liansbackup" --yes
```

Import is a merge, never an implicit replacement:

1. The complete bundle and every receipt are authenticated before a database
   write starts.
2. Existing memory, activity, and receipt IDs are compared with the incoming
   history.
3. Identical IDs are skipped, making repeat import idempotent.
4. Any different existing ID rejects the entire import and rolls back all new
   records.
5. New memory content is encrypted again with the destination device's locally
   protected root key. Historical signed receipts stay byte-equivalent and
   independently verifiable.

This preserves exact correction, scope-change, pause, and forgetting lineage
without transferring the source device's DPAPI or owner-file key.

## Cloud boundary

Cloud storage may hold a `.liansbackup` object as opaque ciphertext, but this
format alone is not live multi-device sync. Production sync still needs user
identity, device enrollment, recovery and rotation policy, concurrency and
conflict UX, object versioning, deletion propagation, billing entitlements, and
managed-connector controls. Those services must not receive the passphrase or a
decrypted profile as part of this contract.
