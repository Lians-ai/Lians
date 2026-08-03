# API and SDK compatibility contract

Lians treats its wire formats as infrastructure contracts, not incidental
implementation details. This document describes the guarantees enforced by the
repository today. It is an engineering compatibility policy, not a hosted-service
availability or support SLA.

## Canonical contracts

- The public and network-isolated administrative HTTP surfaces are captured as
  separate OpenAPI 3.1 documents under `specs/openapi/` for every platform
  release.
- Decision Receipt and Universal Recorder payloads use their own schema versions.
  A platform release does not silently redefine an existing schema version.
- Investigator packets carry an independent `report_version`. Version 1.1 makes
  bounded collection coverage and partial integrity states required so a capped
  embedded history cannot be mistaken for a complete review packet.
- `lians-platform` is the deployable server distribution. `lians-sdk` is the
  public Python client distribution. They intentionally share the top-level
  `lians` import and therefore must be installed in separate environments.
- The canonical `lians-sdk` distribution requires Python 3.11 or newer because
  its optional local engine is the same runtime shipped by `lians-platform`.
  The separate typed HTTP-only Python client may retain an older runtime floor
  only while its isolated build and test matrix proves that claim.
- The TypeScript package is `@lians-ai/lians`. Java, Go, and C artifacts follow
  the same platform release number, but their feature surface may be smaller and
  must not be described as full parity unless a release gate proves it.

The release checker fails when package versions, the Alembic head, chart version,
application version, or versioned OpenAPI filenames drift apart. OpenAPI snapshot
checks fail when route or schema generation changes without an intentional contract
update.

## Change classification

The project is currently pre-1.0. Until 1.0, incompatible public API changes require
a minor version bump, migration notes, regenerated OpenAPI contracts, and matching
SDK changes. Patch releases may contain security fixes, correctness fixes, additive
optional fields, and documentation changes; they must not deliberately remove a
public field or change an existing field's meaning.

Changes to the following are compatibility-significant:

- paths, methods, authentication requirements, scopes, status codes, headers, and
  request or response schemas;
- enum members when a closed enum is consumed by generated or exhaustive clients;
- idempotency, ordering, transaction, concurrency, and retry behavior;
- receipt canonicalization, hashes, signatures, trust decisions, and completeness
  semantics;
- Recorder deduplication, correlation, privacy, and capture-gap semantics;
- database migration prerequisites, runtime-role grants, and downgrade safety;
- Kubernetes values, secret keys, health probes, metrics, and alert labels.

## Mutation contract

Only operations backed by the transactional operation ledger may advertise replay
through `Idempotency-Key`. The authenticated principal, information barrier, route
identity, and canonical request body are included in the claim digest. A completed
claim and its authoritative mutation commit together.

Operations that return a one-time secret, perform an externally ambiguous side
effect, or cannot reconstruct an identical response reject `Idempotency-Key`
explicitly. Their documentation must name the reconciliation step a caller should
take after a lost response. Clients must not automatically retry those operations.

Mutable administrative resources use an explicit expected version, expected value,
or expected timestamp while the row is locked. A stale mutation returns `409`
instead of silently overwriting a concurrent change.

## Additive evolution rules

- New optional response fields are additive; clients should ignore unknown object
  properties unless a signed specification explicitly says otherwise.
- New request fields default to optional until every maintained SDK can represent
  them.
- A required field is not added to an existing versioned wire schema.
- Existing enum vocabularies remain bounded. Adding a value is reviewed as a client
  compatibility change, not assumed to be harmless.
- Timestamps are timezone-aware RFC 3339 values. Hashes are lowercase hexadecimal
  unless the owning schema states another encoding.
- List endpoints use deterministic ordering and bounded page sizes. A pagination
  change must not create duplicates or omissions for an unchanged snapshot.
- DecisionRecord v3 adds server-derived principal type, named role, and effective
  scopes to new decision responses and their immutable hash. Receipt v0.1 keeps
  verified v2 records valid, but only v3 can mark
  `authorization.recording_write.verified=true`. Former caller-declared
  authorization metadata is retained under
  `authorization.declared_workflow_context` with `verified=false`; consumers
  must not treat that compatibility payload as an access-control attestation.
- `audit_chain.lians_evidence_graph` is an optional, separately hashed receipt
  extension with a fixed link-registration watermark and a 10,000-entry hard
  bound. A server fails the export instead of emitting a partial manifest marked
  complete. Its closed schema is published beside Decision Receipt v0.1.

## Bounded inventory reads

- Legacy JSON-array list responses use additive `X-Lians-*` headers rather than
  changing their body shape. `X-Lians-Total-Count` is the exact cardinality of
  the filtered collection before the cursor. `X-Lians-Has-More` and paired
  `X-Lians-Next-*` values drive traversal. `X-Lians-Page-Complete=true` means
  there is no page after the supplied cursor; the stricter
  `X-Lians-Collection-Complete=true` means the un-cursored response itself
  contains the entire filtered collection. Append-only compatibility arrays
  read the page before their exact count and require `total == returned` before
  making that collection-wide claim.
- Decision records, system-of-record events, and evidence artifacts use stable
  descending timestamp/UUID keysets. Continue decisions with
  `before_decided_at`/`before_id`, events with
  `before_occurred_at`/`before_id`, and artifacts with
  `before_recorded_at`/`before_id`. Both cursor values are required together;
  ordering includes the immutable UUID tie-breaker, and every page is fetched
  with `limit + 1` so continuation is observed rather than inferred.
- Control-plane array responses preserve their response shape while disclosing
  exact filtered cardinality and continuation in `X-Lians-*` headers. Trust
  issuers, trusted keys, and Gate policy sets retain bounded legacy `offset`
  support and can traverse without the offset ceiling using paired
  `before_created_at`/`before_id` keysets. Gate approvals, evaluations, and
  investigation cases use paired descending time/UUID cursors named for their
  timestamp fields. Remediation tasks use ascending
  `after_created_at`/`after_id`; their legacy offset can be exchanged for the
  returned keyset cursor at any page boundary. Ordering always includes the
  immutable resource ID as a tie-breaker.
- Administrative governance policy inventory uses the lexical
  `after_namespace` keyset, and immutable policy revisions use the unique
  descending `before_policy_version` keyset. Both retain exact total-count and
  truthful page-completeness headers.
- Durable integration destinations, outbox events, and delivery runs expose
  exact totals and paired descending timestamp/UUID keysets; their bounded
  legacy offsets can be exchanged for the returned cursor. Delivery-attempt
  history continues by the unique ascending `after_attempt_number` watermark.
- SCIM Group resources are complete or error: at most 1,000 Users per Group and
  1,000 Groups per User. A write that would exceed the inverse bound returns
  `409` with `scimType=tooMany` and commits no membership edge; a legacy
  over-capacity resource returns `413 tooMany` rather than a truncated body.
- ValidMind model inventory pages default to 100 and allow at most 250 records;
  offsets are capped at 50,000. The database maintains exact per-scope model,
  decision, span, and distinct-version counts transactionally, so public reads
  never scan source telemetry. Per-model version arrays contain the first 100
  values in lexical order and disclose the exact total, sample limit, and
  whether the array is complete.
- In 0.5, a model observed in multiple information-barrier scopes is represented
  once per opaque `lians_scope_id`. Its `id` is consequently scoped and may
  differ from the namespace-wide 0.4.2 model ID. A 0.4.2 ID remains accepted for
  lookup and `vm_cuid` write-back only while it resolves to exactly one scope;
  ambiguous legacy IDs return `409`. Uniquely resolvable legacy and scoped link
  rows are transactionally mirrored during the rolling-compatibility window.
  Agent IDs are unchanged.
- Compliance erasure reports return at most `subject_id_limit` distinct
  pseudonymous subjects (default 1,000; maximum 5,000) and disclose the exact
  `subject_ids_total` plus `subject_ids_complete`. Event and confidence counts
  are database aggregates, not inferred from the bounded display list.
- Revoking an issuer is atomic only when it has at most 500 active keys. Larger
  cascades return `409` with the exact active count and require callers to revoke
  keys individually before retrying. The issuer remains active on rejection.
- Admission-review pages are ordered by `(created_at, id)`, disclose exact
  `total`, `returned`, collection `complete`, and `has_more`, and continue with
  the paired `after_created_at`/`after_id` keyset. Decrypted held content is
  materialized only for the bounded returned page.
- Supersession-review pages contain only unresolved low-confidence events. They
  disclose exact `total`, `returned`, collection `complete`, and `has_more`, and
  continue with `before_chain_position`. Resolution is versioned against the
  latest supersede event, so a later re-supersession is a new review item rather
  than being hidden by an older confirmation or rejection.
- Conflict-review pages disclose exact cardinality and continue in stable
  `(detected_at, id)` order using the paired `after_detected_at`/`after_id`
  keyset. `complete` is true only when the first page represents the entire
  filtered collection; decrypted memory content is loaded only for one bounded
  page.
- The development-only legacy webhook delivery history exposes an exact global
  `total` and a stable descending `(created_at,id)` keyset. Continue with the
  paired `next_after_created_at` and `next_after_id` values while `has_more=true`.
  `complete=true` retains its compatibility meaning: the first response itself
  contained the entire stored history. Production uses the durable integration
  outbox instead of this legacy path.
- Audit export keeps an object response and traverses by increasing
  `chain_position`. `total_rows` is the exact time-filtered collection size
  before `after_chain_position`; `has_more` drives continuation and
  `next_chain_position` is present only when another page exists. `complete`
  means the uncursored response contains the entire filtered collection, not
  merely that a continuation page has no successor. Retain the returned
  `snapshot_max_chain_position` as `through_chain_position` across the traversal
  to exclude concurrent appends. When verification is
  requested, `chain_rows_checked`, `chain_truncated`, and `chain_tip` define the
  verified prefix; `chain_status=partial` is never a whole-chain claim.

## Export capacity contract

Content-bearing snapshots, Markdown exports, evidence packs, and receipts are
byte-preflighted before plaintext hydration. Hash-only receipts use an
independent, smaller budget and do not load subject keys. Audit export and chain
verification have their own byte budget. HTTP 413 responses carry a stable
machine-readable code plus `estimated_bytes` and `byte_limit`; the server never
returns or signs a truncated prefix as complete. The stable codes are
`snapshot_page_byte_capacity_exceeded`,
`snapshot_markdown_byte_capacity_exceeded`,
`decision_receipt_byte_capacity_exceeded`,
`evidence_pack_byte_capacity_exceeded`,
`audit_export_page_byte_capacity_exceeded`, and
`audit_verification_byte_capacity_exceeded`.

Knowledge snapshot traversal is bitemporally fixed: the first response returns
`recorded_as_of`, and clients retain it with the `(event_time,id)` keyset on
every continuation. This prevents a later-ingested, backdated fact from moving
the collection or its exact total during export.

## Deprecation and removal

Before a public HTTP or SDK capability is removed, a release must:

1. mark the capability deprecated in code and documentation;
2. provide the replacement and migration path;
3. preserve the deprecated path for at least one subsequent minor release unless
   an actively exploitable security issue makes that unsafe;
4. record the removal in the changelog and release notes; and
5. regenerate and review both OpenAPI surfaces.

Compatibility aliases and legacy environment variables may remain longer than the
public product name. Their presence does not authorize new code to depend on them.

## Release review gate

Every release review should verify, without co-installing server and SDK packages:

1. lock-step version and single-migration-head checks;
2. public and administrative OpenAPI drift checks;
3. Decision Receipt schema and independent conformance fixtures;
4. isolated Python and TypeScript SDK builds and contract tests;
5. migration from the immediate prior schema and the exact-schema startup check;
6. immutable deployment images, chart rendering, and Kubernetes schema validation;
7. retry, concurrency, no-store, and one-time-secret behavior; and
8. upgrade, rollback, restore, and operator reconciliation notes.

Breaking a contract may occasionally be the correct security decision. It must be
explicit, versioned, documented, and testable; it must never happen as an accidental
side effect of refactoring.
