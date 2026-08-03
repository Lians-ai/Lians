# Data retention and subject-erasure map

This document describes Lians's technical data lifecycle. It is not legal
advice. A controller must document its lawful basis, retention schedule,
jurisdictional exceptions, and response procedure before processing personal
data.

## Identifier boundary

`subject_id` is accepted as a transient routing input. Before new records are
persisted, Lians derives a tenant-scoped reference:

```
lians:subject:v2:hmac-sha256:<keyed-namespace-scope>:<digest>
```

Both fields use a dedicated 32-byte `SUBJECT_REFERENCE_KEY`; the subject digest
also binds the namespace and caller identifier. The keyed scope lets Lians
reject an opaque reference replayed into another namespace. The key must be
independent of the master-encryption key and retained across master-key
rotations. Identical identifiers therefore do not correlate across namespaces,
and a database-only attacker cannot test a low-entropy identifier without the
HMAC key.

This is pseudonymization, not anonymization. The reference, content hashes,
timestamps, and relationship structure can still be personal data when the
controller can relink them. Treat all retained evidence accordingly.

## Store-by-store lifecycle

| Store | New-write posture | Subject erasure | Residual evidence | Default retention / lawful-basis decision |
|---|---|---|---|---|
| Subject keys | Namespace-scoped reference and wrapped per-subject DEK; no raw identifier | DEK bytes are overwritten and marked destroyed; the reference tombstone prevents accidental re-creation | Keyed reference, creation/destruction time | Retain tombstone only as long as necessary to enforce the erasure and demonstrate completion |
| Memories and live facts | Content encrypted under the subject DEK; explicit identifier stored as the keyed reference | Ciphertext, embedding, metadata, and source are cleared; live-fact and cache copies are removed | Content hash, temporal lineage, opaque subject reference, erasure time | Configure namespace content retention; decide separately whether hash/lineage evidence is required by law or contract |
| Held admissions | Subject-bearing content is encrypted under the same subject DEK; non-subject content uses sealed storage | Live row is replaced with an erased marker; DEK destruction makes historical backup copies unreadable | Queue timing/status and opaque reference | Keep unresolved items briefly; define a short operational retention period |
| Relationships | Explicit subject link is keyed; arbitrary graph labels remain caller-controlled | Linked edge labels, type, metadata, and source are replaced with opaque tombstones and the edge is closed | Edge hash, opaque topology tombstone, timing | Retain only if needed for integrity/recordkeeping; graph labels must not carry ungoverned PII |
| Decision Records | Explicit subject identifier and exact scalar occurrences are replaced before the immutable record hash is computed | Immutable records are not rewritten; their keyed reference remains | Signed/hash-bound decision evidence | Define a decision-record retention basis. Do not place other raw PII in outcome, reason, or metadata fields |
| Universal Recorder | Explicit subject identifier and exact scalar occurrences are replaced before the immutable event hash is computed; default capture is `hash_only` | Immutable events are not rewritten; their keyed reference remains | Hashes, protocol metadata, authenticated producer, opaque reference | Prefer `metadata_only`/`hash_only`. Full capture creates a separate personal-data store requiring its own deletion design |
| Ledger and audit chain | Explicit subject and erasure-request identifiers are keyed before hashing/appending | Append-only records remain verifiable; no raw explicit identifier is added by new code paths | Keyed references, hashes, operation, timing, counts | Set a documented regulatory/contractual retention period and WORM policy; do not claim these values are anonymous |
| Webhooks/integration outbox | Erasure events carry keyed references; audit payload export is hash/reference-only by default | Delivery rows follow outbox retention and destination policy | Destination-side copies are controller responsibility | Contract and configure downstream deletion/retention independently |
| Redis/process caches | Memory content is a derived cache only | A namespace generation is advanced under a database ordering fence, invalidating every agent cache in O(1); the subject DEK is evicted after commit | Old generation metadata until TTL | Short TTL; Redis is not authoritative |
| Backups/WORM exports | Encrypted database bytes and pseudonymous evidence | Copies of subject ciphertext cannot be decrypted after DEK destruction | Immutable hashes/references remain | Retain wrapping-key versions required by the backup schedule; test restores and erasure invariants |

## What the erasure certificate proves

The certificate binds an opaque subject reference, a keyed erasure-request
reference, exact store counts, the key-destruction timestamp, an incremental
manifest SHA-256, and the committed terminal audit-event ID/hash. Memory hashes
are returned in keyset pages of at most 500 items. Certificate reads execute an
exact indexed `COUNT`, not a namespace-wide audit scan, and never place an
unbounded hash array in memory. Full audit-chain verification remains a
separate byte-bounded operator action, so the certificate reports
`chain_status="unchecked"` rather than silently treating a verification budget
as success.

The `lians-subject-erasure-memory-manifest-v1` commitment is reproducible.
Order evidence by `memory_id` ascending, encode each UUID as its 16 network-order
bytes and each lowercase content SHA-256 as 32 bytes, then compute
`state[0] = SHA256("lians/subject-erasure-memory-manifest/v1")` and
`state[n] = SHA256(domain || 0x00 || state[n-1] || uuid || content_hash)`.
The certificate's `manifest_sha256` is the final state (or `state[0]` for an
empty snapshot).

Every subject-bearing write takes the same transaction-scoped subject fence as
erasure. `POST /v1/erase` holds that fence, advances the namespace recall-cache
generation under an exclusive cache-ordering lock, destroys the subject DEK,
creates its tombstone, and records exact snapshot counts in one transaction.
That short transaction is the irreversible privacy boundary: new writes fail
with the destroyed-key tombstone and subsequent recalls cannot decrypt the
content. It does not lock or materialize every subject row.

The durable worker leases the job and scrubs memories, live facts,
relationships, and held admissions in deterministic pages. Each page commits
its cursor, exact progress, memory-hash evidence, and scrub mutations together.
Crashes can replay a transaction but cannot skip a page. Completion is recorded
only after counters reach every frozen boundary, the evidence aggregate equals
the memory snapshot count, no snapshot memory/live-fact derivative remains,
and the subject-key tombstone is still destroyed.

`POST /v1/erase` returns `202 Accepted` with a durable `job_id`, irreversible
key/cache timestamps, exact snapshot totals, progress, and stable failure
codes. The subject reference makes retries idempotent; an optional
`Idempotency-Key` is bound into the keyed request reference and raw request
values are not retained. Poll `GET /v1/erase/jobs/{job_id}`. A certificate
request before completion returns stable code `subject_erasure_not_complete`;
failed work can be requeued with `POST /v1/erase/jobs/{job_id}/retry` after the
operator remedies its reported failure code.

## Legacy and free-form data

Rows written by releases that predate keyed references may contain raw
identifiers. A durable erasure job keeps that legacy lookup value only in a
purpose-separated master-key envelope while its fixed snapshot is active. Each
scrubbed mutable row is canonicalized to the opaque subject reference, and the
encrypted locator is permanently removed at completion. Immutable signed/hash
evidence is not rewritten behind its existing integrity boundary.

Exact subject-field replacement is not a general PII detector. Names, medical
facts, account numbers, or identifiers embedded inside prose can persist in
full-capture payloads, decision metadata, graph labels, logs outside Lians, or
downstream systems. Production namespaces that promise subject erasure should
use reference/hash capture, reject raw PII in immutable free-form fields, and
include every downstream processor in the deletion runbook.

## Operational rules

1. Generate `SUBJECT_REFERENCE_KEY` from 32 random bytes; store it in a secret
   manager and back it up separately from the database.
2. Never rotate it casually. Rotation requires a planned evidence-reference
   migration; losing it prevents lookup by the original identifier.
3. Do not use it as a DEK, wrapping key, signing key, API secret, or audit key.
4. Restrict erasure to an unbarriered privacy administrator and record the
   controller's authorization outside free-form request text.
5. Test live stores, restored backups, caches, graph queries, Recorder reads,
   Decision Receipts, and downstream integrations in every erasure exercise.
6. Publish a namespace data-retention schedule covering content, evidence,
   tombstones, audit/WORM, backups, outbox deliveries, and external processors.
7. Alert on `lians_subject_erasure_jobs{status="failed"}`, oldest-active age,
   progress ratio, and worker readiness. Production startup rejects a disabled
   subject-erasure worker.
