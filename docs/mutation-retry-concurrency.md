# Mutation retry and concurrency contract

This document is the client and operator contract for ambiguous mutation
outcomes. A route is retry-safe only when the table below says so. The mere
presence of an `Idempotency-Key` header in a request does not make an arbitrary
route idempotent.

## Contract classes

- **Ledger replay**: repeat the exact authenticated request with the same
  `Idempotency-Key`. Lians binds the key to the operation, body, path identity,
  principal, authentication method, credential, namespace, and information
  barrier. A conflicting reuse returns `409`. A committed replay returns the
  original authoritative resource identifiers.
- **Domain deduplication**: the subsystem has a stable domain identity such as a
  Recorder event ID, OTLP `(trace_id, span_id)`, impact-assessment key, or
  integration receiver key. Follow that subsystem's rules; it is not the shared
  HTTP mutation ledger.
- **Optimistic precondition**: send the exact `expected_version`,
  `expected_updated_at`, expected relationship value, or SCIM `If-Match` value
  returned by the preceding authoritative read. Lians locks the row before
  comparing it. A stale Lians precondition returns `409`; SCIM returns `428` for
  a missing `If-Match` and `412` for a stale one.
- **Reconciliation only**: do not automatically retry after a timeout, reset, or
  lost response. Read/list the resource and audit history to determine whether
  the first request committed, then issue a new request with fresh state if
  needed. One-time secret, capability, permit, and destructive routes reject an
  `Idempotency-Key` with `400` so a client cannot mistake them for replayable.

Responses that disclose a newly generated secret, decrypted approval/closure
statement, or execution capability use `Cache-Control: no-store` and
`Pragma: no-cache`. One-time responses cannot be reconstructed from persisted
plaintext because Lians does not store that plaintext.

## Route matrix

| Mutation | Retry class | Atomic concurrency boundary | Recovery after an ambiguous response |
|---|---|---|---|
| `POST /v1/memories` | Ledger replay | Operation advisory lock and immutable completion row commit with the memory, audit, projections, governance usage, metering, and outbox | Retry the exact request with the same key |
| `POST /v1/memories/batch` | Ledger replay | Canonically ordered agent locks plus one completion row and one database commit | Retry the exact batch with the same key |
| `POST /v1/decisions` | Ledger replay | Completion row commits with the decision, receipt/integrity state, audit, quota, metering, and outbox | Retry the exact request with the same key |
| `POST /v1/records/events` | Ledger replay | Completion row commits with the immutable ledger event | Retry the exact request with the same key |
| `POST /v1/decisions/{id}/review` | Ledger replay | Completion row commits with the immutable review event; replay reconstructs that review even if a later review exists | Retry the exact request with the same key |
| `POST /v1/decisions/impact-assessments` | Domain deduplication | Namespace/barrier registration fence plus unique hashed body `idempotency_key` and request fingerprint | Repeat the exact body key; a changed request is rejected |
| `POST /v1/decisions/impact-assessments/{id}/advance` | Reconciliation only | Assessment row lock and durable cursor make concurrent advancement serial | Read assessment status before another advance |
| Evidence artifact and decision-link creation | Domain identity deduplication | Immutable artifact identity and unique link registration constraints | Query exact artifact/link identity; do not assume the HTTP header is replayed |
| `POST /v1/integrations/events` | Ledger replay | Completion row commits with encrypted event, initial deliveries, audit, and fan-out | Retry the exact request with the same header/body key |
| `POST /v1/integrations/destinations/{id}/test` | Ledger replay | Destination row lock plus completion row commits with the event and delivery IDs | Retry the exact request with the same key |
| `POST /v1/recorder/events` and `/batch` | Domain deduplication | Unique namespace/barrier-scoped dedup identity commits with normalized event and integrity state | Reuse the same envelope `idempotency_key`, source event ID, or protocol identity |
| `POST /v1/traces` | Domain deduplication | Database uniqueness on namespace, trace ID, and span ID | Resend the same OTLP export; already accepted spans conflict-do-nothing |
| Admission resolution | Reconciliation only | Pending row `FOR UPDATE`; approval takes the subject-erasure fence before that row, then the governance/agent write boundaries; `pending` to terminal is single-winner | Read the admission row/status; `Idempotency-Key` is rejected |
| Conflict resolution | Reconciliation only | Agent advisory lock, conflict-row `FOR UPDATE`, and locked loser memory; open to terminal transition is single-winner | Read the conflict status and audit event; `Idempotency-Key` is rejected |
| Supersession confirm/reject | Optimistic relationship precondition | Agent advisory lock then memory-row `FOR UPDATE`; exact `expected_superseded_by` and prior terminal audit decision are checked | Refresh the review item/audit event; `Idempotency-Key` is rejected |
| `POST /v1/erase` | Reconciliation only | Transaction-scoped subject-key advisory fence (including cached-key writes) and affected agent boundaries serialize erasure; certificate/audit evidence is durable | Read the erasure certificate using the subject; `Idempotency-Key` is rejected |
| `/v1/graph/relate`, `/extract`, and `/unrelate` | Reconciliation only | Subject-erasure fence where applicable, per-agent advisory mutex, and relationship row locks serialize live-edge deduplication, exclusive replacement, and invalidation | Query graph state and audit history before a new mutation; `Idempotency-Key` is rejected and LLM extraction must never be auto-retried |
| Legacy webhook create | Reconciliation only, one-time secret | Secret is sealed before commit; response is non-cacheable | List endpoint metadata; create a new endpoint only after reconciliation; key is rejected |
| Legacy webhook patch | Optimistic timestamp | Tenant/barrier-filtered endpoint row `FOR UPDATE` then exact `updated_at` comparison | GET/list and retry with the new timestamp |
| Legacy webhook delete | Optimistic timestamp plus reconciliation only | Same endpoint lock and timestamp comparison before delete | List endpoint metadata; key is rejected |
| `PUT /api/v1/models/{external_id}` (ValidMind link) | Optimistic timestamp | Per-link advisory missing-row lock and row `FOR UPDATE`; `null` asserts absence | Read the model's `vm_link_updated_at`, then retry with that exact token |
| Break-glass API-key create | Reconciliation only, one-time secret | Unique secret digest and audit commit | List redacted key metadata/audit; key is rejected and response is non-cacheable |
| Break-glass API-key rotate/revoke | Optimistic integer plus reconciliation only | API-key row `FOR UPDATE`, exact `expected_version`, and unique successor edge | List key metadata and rotation edge; key is rejected; rotation response is non-cacheable |
| Barrier assign | Optimistic expected value | Per-namespace/agent assignment boundary, shared agent-write mutex, and assignment row `FOR UPDATE` | List assignment and use its exact group, or `null` only when absent |
| Barrier remove | Optimistic expected value plus destructive | Same assignment/agent boundaries and row lock; exact `expected_group_name` before delete | List assignment; key is rejected |
| Retention `PUT` | Optimistic timestamp | Namespace-policy advisory missing-row boundary and row `FOR UPDATE`; `null` asserts absence | GET policy and retry with its exact `updated_at` |
| Retention prune | Reconciliation only, destructive | Namespace-policy lock holds legal-hold/TTL stable; affected agents are locked and candidate memories are re-read `FOR UPDATE` before destruction | GET policy and inspect prune audit events; key is rejected |
| Billing mapping `PUT` | Optimistic timestamp | Shared namespace-policy advisory boundary and row lock | GET billing mapping and retry with exact `updated_at` |
| Metering dead-letter replay | Reconciliation only | Metering event row/state transition and explicit provider reconciliation decision | Read event status/attempt history; key is rejected |
| Governance policy `PUT` | Optimistic integer | Namespace policy advisory boundary plus row `FOR UPDATE`; version `0` asserts first configuration | GET policy/status and retry with `policy_version` |
| Governance status `PUT` and policy `DELETE` | Optimistic integer | Same policy boundary and row lock; every change advances `policy_version` | GET policy/status and use the new version |
| Identity provider/binding create | Reconciliation only | Database uniqueness; binding creation also locks its provider against concurrent revoke | List by issuer or subject identity and inspect audit before another create |
| Identity provider/binding patch | Optimistic integer | Target row `FOR UPDATE` then exact `expected_version` | GET metadata and retry with the new version |
| Identity provider/binding revoke | Optimistic integer plus destructive | Target row `FOR UPDATE`, exact version, terminal revoked state | GET/list metadata; key is rejected |
| Workload credential create | Reconciliation only, one-time secret | Credential and audit commit atomically | List credentials; key is rejected and response is non-cacheable |
| Workload credential rotate/revoke | Optimistic integer plus reconciliation only | Credential row `FOR UPDATE`, exact version, and unique rotation edge | List predecessor/successor metadata; key is rejected; rotation is non-cacheable |
| Integration destination create | Reconciliation only, one-time signing secret | Encrypted configuration and audit commit atomically | List destination fingerprints; key is rejected and response is non-cacheable |
| Integration destination patch | Optimistic integer | Destination row `FOR UPDATE` and exact `expected_version` | GET destination and retry with fresh version |
| Integration destination secret rotation/revoke | Optimistic integer plus reconciliation only | Destination row lock, exact version, terminal/cancellation transition | GET destination/deliveries; key is rejected; rotation is non-cacheable |
| Integration delivery replay | Reconciliation only | Terminal delivery transition and unique monotonic replay sequence | List linked delivery runs; key is rejected. Receiver idempotency remains stable across runs |
| SCIM tenant create | Reconciliation only, one-time token | Identity-provider row lock plus unique tenant configuration | List tenant configuration; key is rejected and response is non-cacheable |
| SCIM tenant patch/revoke | Optimistic integer | Tenant row `FOR UPDATE`, exact version, terminal revoke | GET tenant; destructive revoke rejects the key |
| SCIM credential rotate/revoke | Optimistic integer plus reconciliation only | Credential row `FOR UPDATE`; the exact version advances on every rotation, including overlap rotations that retain the prior credential | List credentials; key is rejected; rotation token response is non-cacheable |
| SCIM group entitlement `PUT`/`DELETE` | Optimistic integer | Entitlement row `FOR UPDATE`; `null` creates, integer updates/deletes; affected user rows lock in canonical order | GET entitlement and retry with its version |
| SCIM User/Group `PUT`, `PATCH`, `DELETE` | SCIM optimistic ETag | Tenant and resource rows lock; group/member side effects lock affected rows in canonical order | GET resource and retry with its returned ETag; missing/stale preconditions are `428`/`412` |
| SCIM User/Group `POST` | Reconciliation only | Tenant-scoped uniqueness on user/group identities | Filter/list by `userName`, `externalId`, or `displayName` after ambiguity |
| Trust issuer/key create | Reconciliation only | Issuer lock prevents registration beneath concurrent revoke; uniqueness closes duplicate identities | List issuer/keys and audit before a new create |
| Trust issuer/key revoke/rotate | Terminal reconciliation only | Issuer/key rows `FOR UPDATE`; issuer cascade uses one bounded SQL update and rejects more than 500 active keys with `409`; terminal transitions and unique replacement key identity | Read the paginated trust inventory/audit; for an oversized issuer revoke keys individually, then retry; key is rejected |
| Gate policy create/activate | Reconciliation only | Activation locks the draft, namespace policy boundary, and current active row; only one active version wins | Read policy inventory/active version |
| Gate approval append/supersede | Append-only domain chain, reconciliation only | Approval advisory boundary and immutable chain/uniqueness constraints | Read the exact approval chain before appending again; `Idempotency-Key` is rejected and statement-bearing responses are non-cacheable |
| `POST /v1/control/gate/evaluate` | Reconciliation only, one-time capability | Policy/approval snapshot commits with a unique permit; response is non-cacheable | Query evaluation by ID/correlation; never auto-retry; key is rejected |
| `POST /v1/control/gate/permits/consume` | Reconciliation only, single-use capability | Permit row lock and database transition enforce one successful consumption | Query evaluation/permit enforcement records; key is rejected |
| Investigation case create | Reconciliation only | Database identities and audit commit; no shared replay ledger | List/filter cases and audit before another create |
| Investigation case patch | Optimistic timestamp | Case row `FOR UPDATE` then exact `expected_updated_at` | GET case and retry with fresh timestamp |
| Remediation task create | Optimistic parent timestamp | Case row `FOR UPDATE`, exact `expected_case_updated_at`; creation advances the case timestamp | GET case/tasks; an ambiguous create makes the old parent token stale |
| Remediation task patch | Optimistic timestamp | Case row is the serialization root, followed by task `FOR UPDATE` and exact `expected_updated_at` | GET task and retry with fresh timestamp |
| Task/case close | Optimistic timestamp plus one-time attestation | Task closure takes the parent case lock before its task lock; case closure takes that same case lock and uses an exact SQL outstanding-task count; unique closure attestation | GET resource/closure; key is rejected and decrypted response is non-cacheable |

Integration **delivery to a receiver** is at least once. Each network attempt and
operator replay carries a stable receiver `Idempotency-Key`; the receiver must
persist it. Destination revocation cancels queued work, and workers re-check
active destination state before dispatch. A request already leased or in flight
can still reach the receiver, so emergency containment also requires credential
rotation or an egress block.

## Timestamp and locking rules

Timestamp tokens compare the exact persisted instant after normalizing both
values to UTC. They are not fuzzy clocks and must not be synthesized by a
client. Preserve all fractional seconds returned by JSON. A `null` expected
timestamp means "I observed that no row exists"; it is not a wildcard.

PostgreSQL `FOR UPDATE` locks serialize existing rows. Transaction-scoped
advisory locks serialize first creation where no row exists yet, and per-agent
advisory locks protect memory/fact projection boundaries. Unique constraints and
terminal-state checks remain the final correctness barrier if application
workers race.

SQLite supports local development and single-process tests. Its in-process
locks and transaction behavior are not a cross-replica production guarantee.
Production mutation concurrency requires PostgreSQL.

Barrier assignment currently uses the exact observed group name instead of an
integer version because the deployed schema has no assignment-version column.
This prevents blind widening and ordinary lost updates, but it cannot detect an
ABA sequence (`A` to `B` to `A`). Add a monotonically increasing version in a
future migration before offering a fully ABA-proof barrier lifecycle contract.
