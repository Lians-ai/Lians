# Immutable approval and review attestations

Lians treats a human assertion as evidence only when it is bound to an
authenticated principal and stored as an immutable event. API clients cannot
claim an approval role, manufacture an approval count, or overwrite review
history.

API-key provisioning accepts validated `role` and `barrier_group` fields,
returns them in administrative inventory, and preserves both during rotation.
This gives non-OIDC workloads the same server-derived approval role semantics
as federated identity bindings. Trusted receipt key IDs are restricted to safe,
stable URL segments so rotation and revocation paths are unambiguous.

Approval identity uses a versioned canonical principal reference. OIDC principals
are addressed by both the trusted provider UUID and binding UUID, never raw `sub`;
API/workload credentials are addressed by credential UUID, never a mutable or
duplicate display label. Thus equal subjects from different issuers and equal key
labels cannot collide, impersonate, supersede, or collapse a quorum series.

## Gate approvals

A policy set declares one or more exact `protected_actions` and one or more
`target_ref_prefixes`. Gate resolves the active policy from that mapping; optional
caller `policy_set_id`, name, or version fields are assertions and cannot select a
more permissive policy. Overlapping mappings in the same information barrier are
rejected both by the activation service and a PostgreSQL activation trigger.
Barrier-specific mappings take precedence over tenant-wide mappings, and the
longest boundary-safe target selector is authoritative. A selector is exact unless
it ends in `/`, `:`, `#`, or `?`, preventing a `.../prod` selector from also
matching `.../production`. Missing or ambiguous mappings fail closed.

Every policy version also declares a nonempty allowlist of canonical
`enforcement_principal_ids` and a `maximum_permit_ttl_seconds` from 1 through 300
(default 60). These are immutable, hash-covered policy fields. Every evaluation
selects one allowed mediator, a shorter-or-equal TTL, and a SHA-256
`execution_request_hash` for the canonical downstream request.

The default for new policy sets is `deny`. A policy set's
`default_disposition` applies when no enabled rule matches the server-derived
decision type and risk level. If one or more rules match, every matched
rule must pass; only then does Gate record `allow` and atomically issue one
short-lived, opaque execution permit. A failed rule returns its
configured `deny` or `review` action, with `deny` winning when multiple failures
have different actions. This makes a default-deny policy usable without turning
it into an unconditional deny.

Every evaluation must link a real Lians decision and a nonempty target reference.
Gate first verifies the decision's authenticated v2 record hash and unique
immutable `decision_recorded` audit binding. It then replaces caller hints with
the decision's immutable type and policy version,
derives the strictest risk classification from the decision and normalized evidence
links (defaulting conservatively to `medium` when absent), and checks
every cited memory against current, unerased validity in the same tenant/barrier.
A caller cannot submit untrusted-content findings: Gate reconstructs prompt-injection,
blocked-source, and normalized evidence-risk signals from the recorded decision
boundary so an evaluator cannot omit a known finding.
A trusted receipt must cryptographically verify and its signed namespace,
decision ID, decision type, and policy version must match that server-derived
boundary. A valid receipt for another decision or tenant cannot satisfy the rule.

An allow permit is returned only by `POST /v1/control/gate/evaluate`; list/get
responses never contain it. The database stores its digest, not the plaintext
token. The exact policy-authorized mediator consumes it once at
`POST /v1/control/gate/permits/consume` while presenting the actual action, target,
decision, and request digest. Row/advisory locking, constant-time digest comparison,
expiry checks, exact claim binding, and a unique immutable consumption record make
replay or substitution fail closed. See [Gate execution permits](gate-execution-permits.md).

`POST /v1/control/gate/approvals` creates an attestation for one exact execution
boundary. The boundary hash covers the action, decision ID, change-event ID,
immutable Gate policy ID and hash, target reference and information barrier,
and Decision Receipt hash. The approver identity and named role come from the
API key or federated identity binding, never from the request body.

An approval may have an expiry, evidence references, and an optional statement.
Statements are AES-GCM sealed at rest with the purpose-separated
`gate-approval-attestation-statement` key and context-bound additional
authenticated data. Audit events contain only statement and principal hashes.

Lifecycle changes append a successor with
`POST /v1/control/gate/approvals/{id}/supersede`. An approver may supersede their
own attestation; an authenticated `owner` or `compliance` principal with admin
scope may revoke another principal's attestation. Update and delete triggers
make every series append-only.

Gate evaluations accept `approval_ids`. The server resolves each ID and rejects
an approval unless it is in the same namespace and exact barrier, matches the
complete context hash, is the latest event in its series, is approved and not
expired, and passes its persisted hash verification. A principal contributes at
most one approval. The legacy free-form `approvals` field is explicitly
rejected.

Each policy rule may additionally restrict which authenticated principal types
can satisfy its approval count and role requirements and may set a maximum
approval age. For example,
`allowed_approval_principal_types: ["human"]` with
`maximum_approval_age_seconds: 900` excludes API-key and workload attestations
and requires a human OIDC-bound approval issued within the preceding 15
minutes. The exact eligibility constraints and the resolved attestation type,
authentication method, and timestamp are captured in the immutable Gate
evaluation snapshot. This proves the configured Lians identity/freshness
boundary; MFA, step-up, liveness, and device assurance remain IdP controls.

List and get endpoints redact statements by default. `include_statement=true`
requires admin scope.

## Decision reviews

`POST /v1/decisions/{decision_id}/review` now appends a
`DecisionReviewEvent`. Reviewer identity, role, authentication method, and
credential reference are derived from authentication. A supplied legacy
`reviewer` value is accepted only when it exactly matches that identity.

Review events have a monotonic sequence, prior-event hash, event hash, encrypted
note, and note hash. Database advisory locks and uniqueness constraints
serialize concurrent appends. PostgreSQL and SQLite triggers reject updates and
deletes, validate predecessors, and permit the legacy `DecisionRecord` review
fields to change only when they match the latest immutable event. Those three
fields remain a read projection for existing clients and receipts.

`GET /v1/decisions/{decision_id}/review-history` verifies the full visible chain
before returning it. Notes are redacted unless `include_notes=true`, which
requires admin scope.

## Database controls

Migration `0032_immutable_attestations` adds tenant namespace RLS, restrictive
information-barrier policies, append-only triggers, chain-shape constraints,
unique sequence/predecessor constraints, and PostgreSQL advisory-lock insert
guards. It follows `0031_enterprise_provisioning`.

Existing pre-migration `DecisionRecord` review projection values remain readable
as legacy state. The first new authenticated review starts the authoritative
immutable history; Lians does not invent an authenticated event for a historical
caller-asserted reviewer.

Migration `0038_gate_policy_routing` adds authoritative action/target selectors,
changes the default for newly created policies to deny, and adds policy-level
approval principal-type and freshness requirements. Existing selector-free policy
rows remain readable but are deliberately ineligible for evaluation or activation;
an administrator must create and activate a mapped version before reopening the
protected side effect.

Migration `0040_gate_execution_permits` adds the policy mediator/TTL boundary,
first-class target, mediator, and request-digest evaluation claims, append-only
permit grants and consumptions, RLS, foreign keys, uniqueness constraints, and
database insert guards. It retires pre-permit active policies without rewriting
their historical definition hashes; administrators must create a permit-aware
version before execution can resume.
