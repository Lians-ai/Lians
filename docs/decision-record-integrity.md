# DecisionRecord authenticity and integrity

Decision records are evidence only when Lians can distinguish a workload's
claimed label from the credential that actually wrote the record. Starting
with migration `0041_decision_record_integrity`, every new DecisionRecord has
an authenticated, immutable provenance boundary. Migration
`0057_decision_auth_snapshot` extends that boundary: new hash-v3 records bind
the authorization that permitted the write, while existing v1/v2 evidence is
left unchanged.

The established-table provenance index is isolated in online revision
`0041a_decision_integrity_idx`; it detects and repairs an invalid
concurrent index left by interruption without replaying the expand DDL.

## Identity semantics

`agent_id` is a caller-supplied workload label. It is useful for search and
correlation, but it is not proof of identity. Lians separately persists:

- `recorded_by_principal_ref`: the issuer-qualified OIDC binding or API-key
  principal reference derived by the authentication layer;
- `recorded_by_auth_method`: the server-observed authentication mechanism;
- `recorded_by_credential_ref`: a domain-separated SHA-256 reference to the
  authenticating credential ID, never the credential secret;
- `recorded_by_principal_type`: the server-derived principal class;
- `recorded_by_role`: the server-derived role, when one exists;
- `recorded_by_scopes`: the complete effective authorization scopes used for
  the request, bounded to 50 unique valid values and required to contain
  `write` for v3;
- `record_integrity_status` and `record_hash_version`: the provenance and hash
  contract applied to the row.

Clients cannot assert these values in `DecisionCreate`. Decision audit events
also use the canonical authenticated principal as their actor, not `agent_id`.

## Versioned record hash

New records use DecisionRecord hash v3. The canonical hash covers the record
ID, namespace and barrier, claimed agent label, authenticated recorder
provenance, principal type, optional role, complete effective scopes,
decision/model/policy fields, both time axes, evidence IDs, input/output hashes,
supersession link, and metadata. Human-review projection fields are deliberately
excluded: their authority is the separate immutable, hash-chained review-event
series.

Historical rows are not rewritten into apparent authenticity. Migration 0041
marks them as hash v1 with `record_integrity_status=legacy_unverified`, the
sentinel principal `lians:principal:v1:legacy-unverified`, and no credential
reference. They remain available for inventory and correction, but cannot be
signed or exported as verified DecisionRecords.

The same classification applies to rows inserted by a 0.4.2 pod during the
0.5 rolling window. Constant database defaults accept the old insert shape and
mark it v1/legacy-unverified at write time; they never infer a principal from
the claimed `agent_id`. Existing verified v2 rows remain verifiable under their
original hash contract, but their principal type, role, and scopes are empty and
authorization-snapshot completeness is false. Lians never guesses those values
from current group membership or credential metadata. The current writer
supplies every verified v3 field explicitly. Removing compatibility defaults is
deferred to a later contract release after all old writers are retired.

## Audit binding and fail-closed exports

The supported write path appends exactly one `decision_recorded` event in the
same transaction. Its EventLog namespace, canonical actor, `content_hash`, and
minimal non-PII payload bind the DecisionRecord ID to its versioned record hash.

Before Lians signs a Decision Receipt or creates an evidence pack, it:

1. recomputes the DecisionRecord hash using its declared v1/v2/v3 contract;
2. requires verified authenticated provenance;
3. finds exactly one matching `decision_recorded` EventLog row;
4. verifies the binding event's own versioned row hash and predecessor.

Any missing, duplicate, malformed, legacy-unverified, or mismatched state fails
with a conflict response. Lians never signs the current contents of a mutated
row merely because a signing key is available.

## Database immutability

PostgreSQL trigger `trg_decision_record_immutable` rejects DELETE and any
UPDATE that changes a hash-covered field. The statement-level
`trg_decision_record_reject_truncate` rejects TRUNCATE. The only mutable columns
are the legacy human-review projection, and the existing
`trg_decision_review_projection_guard` permits those values only when they
exactly match the latest immutable DecisionReviewEvent.

Corrections therefore create a new v3 DecisionRecord with `supersedes_id`
pointing to the prior record. Operators must not disable these triggers or run
the application with the table-owner, superuser, or `BYPASSRLS` role.
