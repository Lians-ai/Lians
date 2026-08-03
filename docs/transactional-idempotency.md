# Transactional idempotency

Lians binds a client retry key to one exact authenticated operation request. A
successful replay returns the same authoritative resource identifiers; reusing
the key with a different body, path parameter, principal, authentication method,
or information barrier returns `409`.

## Commit boundary

For PostgreSQL, Lians hashes the key with its namespace and bounded operation
name, takes a transaction-scoped advisory lock, and checks the immutable
`operation_idempotency` ledger before performing any mutation. A new completion
row is inserted in the same transaction as the authoritative resources, audit
events, projections, integration-outbox events, and durable metering event. The
row has no pending state: either the entire transaction commits, or neither the
resource nor its idempotency claim exists.

The ledger stores no raw retry key, request body, response body, tenant label in
metrics, or subject content. It retains only domain-separated SHA-256 digests,
bounded operation/resource kinds, ordered UUID resource references, the original
HTTP status, and a timestamp. PostgreSQL RLS isolates namespaces. Runtime access
is limited to `SELECT` and `INSERT`; triggers reject update, delete, and truncate.

SQLite uses an in-process keyed lock for local development and tests. It is not a
multi-replica production idempotency backend.

## Header contract

`Idempotency-Key` is optional and, when supplied, must be 1–255 bytes of visible
ASCII without whitespace. Clients should use an unpredictable stable value and
retain it until the outcome is reconciled. The same value may be used in a
different operation because key hashes are operation-scoped.

Migration `0046_operation_idempotency` installs the hashed ledger and guarded
compatibility mirror while intentionally retaining the raw-key table through
the 0.5 rolling window. Online revision
`0046a_idempotency_backfill` copies historical claims in committed,
durably cursored keyset pages. A 0.4.2 insert is mirrored into the hashed ledger by a
database-serialized trigger; a 0.5 admitted-memory completion flushes its hashed
claim first, then writes the exact legacy mapping in the same transaction. Both
paths compare the exact authoritative memory ID and reject disagreement, so the
retained representations cannot accept conflicting completion claims.

Because the previous table did not record the authenticated request
fingerprint, migrated or old-writer rows preserve the old exactly-once boundary
by blocking a duplicate mutation but fail closed with `409` instead of returning
an unverifiable response to a possibly different principal, barrier, body, or
subject. The retained table contains client-supplied raw retry keys: restrict it
to the runtime compatibility path, protect it like credential-adjacent data,
and include it in backup/restore reconciliation. Its removal is a future
contract migration, never part of the 0.5 rollout.

The 0.4.2 application checked this predecessor table only for admitted memory
creation, and its resource commit could precede its claim insert. That released
behavior cannot be made transactionally atomic by an additive schema migration
because the raw key is absent from the memory row. During the mixed-version
window, route keyed `POST /v1/memories` traffic exclusively to 0.5 writers (or
quiesce it) before canarying; do not load-balance the same keyed operation across
0.4.2 and 0.5.0. The bridge preserves completed old claims and rollback reads,
but is not a claim that an already-running old transaction became atomic.

## Covered authoritative mutations

The shared transaction ledger currently covers:

- `POST /v1/memories`, including admitted (`200`), held (`202`), and rejected
  (`422`) admission outcomes;
- `POST /v1/memories/batch`, with the admitted batch and ordered result IDs in one
  database commit; all affected agent locks are acquired in canonical order to
  prevent opposite-order batch deadlocks;
- `POST /v1/decisions`;
- `POST /v1/records/events`;
- `POST /v1/integrations/events`, whose encrypted event, original delivery
  fan-out, audit event, and completion record commit together;
- `POST /v1/integrations/destinations/{destination_id}/test`, whose event and
  delivery identifiers are durably replayed; and
- `POST /v1/decisions/{decision_id}/review`, whose replay reconstructs the exact
  immutable review projection even after a later review.

The Python HTTP SDK generates one key per call and enables automatic
transport/`429`/`5xx` retries only for these proven-safe methods (and separately
verified durable impact-assessment creation). The TypeScript SDK accepts an
explicit `idempotencyKey` for memory, batch, and decision creation but does not
automatically retry mutations.

## Explicitly excluded mutations

No automatic retry safety is claimed for admission resolution, supersession or
conflict resolution, privacy erasure, retention/policy changes, evidence graph
mutation, impact-job advancement, identity/credential administration,
integration destination administration or delivery replay, or Gate
evaluation/permit operations unless that subsystem documents its own independent
durable replay contract. In particular, capability issuance, one-time secret
responses, destructive operations, and permit consumption remain non-retryable
after an ambiguous response and reject an `Idempotency-Key` rather than imply
otherwise.

The complete route-by-route classification and optimistic-concurrency contract
is in [Mutation retry and concurrency](mutation-retry-concurrency.md).

Universal Recorder ingestion and integration delivery retain their existing
domain-specific durable deduplication/receiver-idempotency contracts rather than
using this generic operation ledger.

## Operations

`lians_idempotency_operations_total{outcome=...}` has five fixed outcomes:
`claim_completed`, `replay`, `request_conflict`, `invalid_key`, and
`replay_unavailable`. It never labels
keys, namespaces, routes, principals, or resources. New claims and replays are
normal. A sustained volume of request/body conflicts warns because it usually
indicates a client retry-key lifecycle bug. Any unavailable completed replay pages
as a potential ledger/resource integrity failure.
