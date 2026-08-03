# Lians Control Plane v0.1

The Lians control plane turns Decision Receipts and change-impact findings into
runtime enforcement and owned remediation. It consists of three linked systems:

1. **Trust registry** — tenant-approved receipt issuers and public verification
   keys, including validity windows, revocation, fingerprints, and explicit
   rotation lineage. Lians never persists private signing keys.
2. **Lians Gate** — server-selected immutable-version policy sets evaluated immediately before
   a consequential action. Every evaluation appends an allow, deny, or review
   record with the full input, applied rule snapshots, reasons, policy hash,
   request hash, and verdict hash. An allow atomically issues one short-lived,
   single-use capability for an independently authenticated mediator.
3. **Investigation and remediation** — cases link to decisions, system-change
   events, and Gate verdicts. Remediation work has an owner and status; a task or
   case cannot close without a hash-bound, append-only attestation and evidence
   references.

## Trust contract

Trusted keys are raw Ed25519 public keys represented as canonical base64. The
registry stores `fingerprint_sha256 = SHA256(raw_public_key)`. A key is trusted
at time `T` only when all of these are true:

- its issuer and key records are active;
- `valid_from <= T` and `valid_until` is absent or `T <= valid_until`;
- issuer, key, caller, and target pass namespace and information-barrier checks.

Rotation creates a new key row and revokes the old row atomically. The old row
records `replaced_by_key_id`; the new row records `rotated_from_key_id`; both
record the rotation reason. Public-key material is immutable in PostgreSQL.

## Gate rule contract

A policy version declares exact protected action identifiers, canonical absolute
target-reference URI selectors, a nonempty allowlist of canonical versioned
`enforcement_principal_ids`, and a maximum permit TTL from 1 through 300 seconds.
Target selectors are exact unless explicitly terminated by `/`, `:`, `#`, or `?`,
which prevents sibling-resource overmatch. Gate selects from active mappings;
caller policy fields are assertions only. Same-barrier selector overlap is rejected
under a serialized database guard, and missing or ambiguous mappings fail closed.
Evaluations and approvals require a linked decision and nonempty target reference.

A policy version contains ordered rules. Optional selectors limit a rule to
decision types and risk levels. Each matching rule can require:

- a minimum Decision Receipt grade and an active trusted issuer/key;
- current cited sources and an attached versioned runtime policy;
- principal scopes;
- an approval count and approval roles;
- information-barrier compatibility;
- absence of untrusted-content signals or a maximum untrusted-content score.

Every failed condition contributes a stable reason code and the rule's
`action_on_failure` (`deny` or `review`). The most restrictive failed action
wins. When one or more applicable rules all pass, the result is `allow`; when no
rule matches, the policy's default disposition applies. New policies default to
`deny`.

Policy definitions and rule rows are immutable. Activation only changes policy
lifecycle fields and retires the prior active version with the same name and
barrier. Historical Gate records embed the rule snapshots they used, so later
versions cannot reinterpret an earlier verdict.

Decision type, risk, policy attachment, cited-source currency, and untrusted-content
signals are reconstructed from the immutable decision/evidence boundary. Evaluation
callers cannot self-label or suppress those fields. The linked decision must first
pass its authenticated v2 record-hash and unique `decision_recorded` audit-binding
verification; legacy-unverified or tampered decisions fail closed.

Every evaluation requests one policy-allowed mediator, a TTL no greater than the
policy maximum, and a lowercase SHA-256 `execution_request_hash` of the canonical
provider/tool request. An allow verdict and its opaque 256-bit permit are persisted
atomically, with only the token digest stored. Deny/review emits no permit. The
mediator consumes the permit once with the actual action, target, decision, and
request hash; its authenticated principal must match exactly. All invalid, expired,
replayed, or mismatched permits fail identically. Evaluation list/get endpoints
never return token material.

## HTTP surface

All routes are under `/v1/control` and use the existing `X-API-Key` identity.

- `POST/GET /trust/issuers`
- `POST /trust/issuers/{issuer_id}/revoke`
- `POST/GET /trust/issuers/{issuer_id}/keys`
- `GET /trust/keys/{key_id}?at=...`
- `POST /trust/issuers/{issuer_id}/keys/{key_id}/rotate`
- `POST /trust/issuers/{issuer_id}/keys/{key_id}/revoke`
- `POST/GET /gate/policies`
- `POST /gate/policies/{policy_id}/activate`
- `POST /gate/evaluate`
- `POST /gate/permits/consume`
- `GET /gate/evaluations[/{evaluation_id}]`
- `POST/GET/PATCH /investigations/cases...`
- `POST/GET/PATCH /investigations/.../tasks...`
- `POST /investigations/tasks/{task_id}/close`
- `POST /investigations/cases/{case_id}/close`
- `GET /investigations/{case|task}/{resource_id}/attestation`

Trust and policy mutations require `admin`; runtime Gate evaluation and
investigation mutations require `write`; reads require `read`. Caller scopes
cannot be inflated in an evaluation request. Optional `actor_id`/`principal_id`
fields must match the authenticated OIDC or API-key principal; when omitted,
Lians supplies that authenticated identity. Every mutation also appends a
hash-chained event to the existing Lians audit log.

## Storage enforcement

Migrations `0028_control_plane`, `0038_gate_policy_routing`, and
`0040_gate_execution_permits` add PostgreSQL namespace and restrictive
information-barrier RLS to every control-plane table. Gate decisions, policy
rules, and closure attestations have database triggers rejecting updates and
deletes. Policy definitions and trusted public-key material have guard triggers
allowing lifecycle changes while rejecting in-place evidence changes.
The routing migration additionally installs an RLS-independent, serialized trigger
that refuses selector-free or overlapping policy activation. Permit grants and
consumptions add foreign keys, exact-claim insert guards, one-per-evaluation and
one-consumption uniqueness, expiry validation, and update/delete/truncate rejection.

## Integration hooks

Applications include `lians.api.routes_control.router` in FastAPI and import
`lians.control_models` wherever the shared SQLAlchemy metadata is assembled.
Framework recorders call Gate immediately before tool execution. A separately
credentialed broker consumes an allow permit immediately before dispatch, then
passes the Gate decision/consumption IDs into the Decision Receipt or investigation
case. Provider IAM must reject direct evaluator calls.
Change-impact processing can open a case with its `ledger_events.id`; receipt
verification can resolve a trusted key through the registry before admitting a
receipt as trusted evidence.
