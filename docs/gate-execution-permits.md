# Gate execution permits

Lians Gate is enforced through a separate mediator—such as a tool broker,
provider proxy, sidecar, or admission controller—that is the only identity able
to perform the protected side effect. An `allow` verdict is not itself
authority. It produces one short-lived, single-use bearer permit bound to the
exact execution request, and the mediator must redeem that permit immediately
before dispatch.

## Trust boundary

Give the evaluator and mediator different credentials. Record the mediator's
exact `principal_id` from `GET /v1/identity/whoami`; do not use a label, subject,
email address, or caller-supplied alias. Current canonical references are
versioned issuer/binding-qualified OIDC identities or credential-UUID-qualified
API/workload identities:

```text
lians:principal:v1:oidc:<provider-uuid>:<binding-uuid>
lians:principal:v1:api-key:<credential-uuid>
```

The provider must reject direct evaluator credentials. If the evaluator can
reach the provider without the mediator, it can still bypass Gate regardless of
the permit protocol. Lians also rejects an evaluation that names its authenticated
evaluator principal as the mediator.

## Immutable policy contract

Each policy version requires:

- nonempty exact `protected_actions`;
- canonical ASCII absolute `target_ref_prefixes` (percent-encode non-ASCII data
  with uppercase hexadecimal escapes);
- a nonempty, duplicate-free `enforcement_principal_ids` allowlist;
- `maximum_permit_ttl_seconds`, default 60 and hard-limited to 300 seconds;
- one or more Gate rules and a restrictive default.

Target matching is exact unless a selector explicitly ends at a resource
boundary (`/`, `:`, `#`, or `?`). Thus
`https://broker.example/orders/prod` does not cover
`https://broker.example/orders/production`; use
`https://broker.example/orders/prod/` to cover descendants. Same-action
selectors that overlap under this rule cannot both be active in one barrier.

These fields are covered by `policy_hash` and the database policy-definition
guard. Migration `0040_gate_execution_permits` retires legacy active policies
whose historical hashes did not cover an enforcement boundary. Create and
activate a new policy version to reopen the action.

Credential rotation changes the canonical mediator principal ID. Use a new
policy version (briefly allowing old and new IDs if continuity is required),
move provider IAM to the new credential, then activate a new-only version and
revoke the old credential. The short permit ceiling bounds the transition.

## Evaluation and issuance

`POST /v1/control/gate/evaluate` requires the evaluator to provide:

- the protected `action`, canonical `target_ref`, and linked `decision_id`;
- one policy-allowed `enforcement_principal_id`;
- `permit_ttl_seconds` no greater than the policy maximum;
- lowercase `execution_request_hash`, the SHA-256 digest of the mediator's
  canonical provider/tool request including every security-relevant argument.

Before evaluating rules or issuing a permit, Gate verifies that the linked
decision has authenticated v2 recorder provenance, an exact record hash, and
one valid immutable `decision_recorded` audit-chain binding. Legacy-unverified
or tampered decisions fail closed.

Use a deterministic serialization such as RFC 8785 JSON Canonicalization Scheme
for the provider request. Include the action, target, decision identifier,
provider operation, destination/account/region, and complete arguments. Both the
evaluator and mediator must hash the same representation; the mediator hashes
the actual request it is about to send rather than trusting an evaluator's copy.
It must also derive `action` and `target_ref` from the normalized provider
destination, rejecting path traversal or ambiguous encodings instead of echoing
the evaluator's labels.

The stored digest is an integrity commitment, not encryption: low-entropy
arguments may be guessable. Keep reusable credentials out of provider payloads,
prefer opaque secret-manager references, and minimize data while still covering
every argument that can change authorization or the side effect.
Use stable opaque resource IDs in `target_ref`; targets and request digests are
audit evidence and should not embed human names, emails, credentials, or raw PII.

A deny or review verdict never creates a permit. An allow verdict and its permit
grant are inserted in one database transaction. The response contains an
`execution_permit` exactly once:

```json
{
  "disposition": "allow",
  "execution_permit": {
    "permit_id": "...",
    "evaluation_id": "...",
    "enforcement_principal_id": "lians:principal:v1:api-key:...",
    "action": "payments.transfer",
    "target_ref": "urn:payments:account:123",
    "decision_id": "...",
    "execution_request_hash": "<64 lowercase hex>",
    "issued_at": "...",
    "expires_at": "...",
    "token": "lians_permit_v1_..."
  }
}
```

The token contains 256 random bits encoded with a log-safe URL alphabet. Lians
stores only its SHA-256 digest. Do not log, trace, persist, retry-queue, or put the
token in a URL. `GET /v1/control/gate/evaluations` and
`GET /v1/control/gate/evaluations/{id}` never return permit material.
The same committed audit-chain event anchors the non-secret permit ID, expiry,
and immutable grant hash. The issuance response is marked `Cache-Control:
no-store`. Configure API gateways, APM agents, and exception collectors to omit
request/response bodies for both permit endpoints.

Issuance and redemption are not blindly retryable: the server may commit before
the caller observes the response. The Python SDK disables its automatic
transport/5xx retries for both calls. Treat an ambiguous result as consumed or
unavailable and obtain a fresh evaluation instead of replaying the request.
Disable automatic POST retries for these paths in proxies and service meshes as
well.

## Mediator redemption

The independently authenticated mediator calls
`POST /v1/control/gate/permits/consume` with the `permit_id`, secret `token`, and
the actual action, target, decision ID, and request hash. Lians:

1. takes a transaction advisory lock and row lock for the permit;
2. hashes the presented token and compares its digest in constant time;
3. requires the authenticated principal to equal the policy-authorized audience;
4. compares every execution claim exactly with both the grant and allow verdict;
5. rejects expiry and any existing consumption;
6. appends one immutable consumption record and commits it with its audit event.

The consumption audit event anchors both the grant hash and consumption hash;
neither event contains the bearer token.

Unknown, malformed, expired, replayed, wrong-principal, and wrong-claim permits
all fail with the same non-oracular response. The mediator dispatches the exact
provider request only after successful consumption. Because redemption is
single-use, a network failure after consumption requires a new evaluation;
provider idempotency keys should independently prevent duplicate effects.

Lians now ships the supported standalone `lians-gate-mediator` process. Its
two-phase raw-body API owns canonicalization, pins validated IPs while preserving
TLS hostname verification, rebuilds provider headers, and enforces the
consume-before-dispatch order without redirects or retries. See
[Gate enforcement mediator](gate-enforcement-mediator.md). Both evaluation and
consumption responses are marked `Cache-Control: no-store` and `Pragma:
no-cache`, including error paths.

Operational counters deliberately exclude tenant and capability identity:
`lians_gate_evaluations_total{disposition}` records committed verdicts and
`lians_gate_permit_events_total{outcome}` records the bounded permit lifecycle.
Internally distinguishable expiry, replay, and mismatch outcomes never alter
the uniform redemption response. Alert and mediator scrape guidance lives in
[Gate enforcement mediator: operations and metrics](gate-enforcement-mediator.md#operations-and-metrics).

## Database enforcement

Permit grants and consumptions have namespace and restrictive barrier RLS,
foreign keys, unique evaluation/permit constraints, claim and expiry checks,
insert-validation triggers, and update/delete/truncate rejection triggers. The
grant trigger accepts only an allow evaluation whose action, target, decision,
policy, mediator, request digest, and evaluation time match. The consumption
trigger revalidates the allow verdict and every immutable grant claim.

The application role must be non-superuser and `NOBYPASSRLS`. A database
superuser/table owner, a compromised mediator credential, provider access that
bypasses the mediator, or a mediator that hashes different arguments remains an
external trust failure and must be controlled through database IAM, provider
IAM, egress policy, and independent audit logs.
