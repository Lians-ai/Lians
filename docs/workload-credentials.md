# Tenant workload credentials

Lians has two deliberately separate API-key control surfaces:

- `POST /v1/identity/workload-credentials` is the normal tenant lifecycle. It
  accepts only a verified **human OIDC bearer** with the tenant `admin` scope.
  Namespace, actor, and any caller barrier come from the verified identity
  binding, never from the request body.
- `/v1/admin/api-keys` is the compatibility and deployment **break-glass**
  surface protected by `X-Admin-Secret`. It can operate across namespaces and
  is therefore not a tenant self-service API. Keep it off public networks and
  monitor every use.

## Security invariants

Tenant-managed credentials:

- use a `lians_wk_` secret shown only in the create/rotate response;
- persist only the SHA-256 digest, never the plaintext secret;
- require an expiry bounded by
  `WORKLOAD_CREDENTIAL_MIN_TTL_SECONDS` and
  `WORKLOAD_CREDENTIAL_MAX_TTL_SECONDS` (30 days by default);
- cannot receive wildcard, break-glass, platform, or global-admin scopes;
- cannot exceed the OIDC caller's effective scopes;
- cannot receive a named role the caller's verified role cannot delegate;
- inherit a barrier-scoped caller's barrier and can never widen it to `null` or
  another barrier;
- record a stable identity-binding reference in `created_by`, plus immutable
  create, rotate, and revoke audit events without the raw OIDC subject or secret;
- use row locks, a required `expected_version`, and a unique rotation edge to
  prevent duplicate successors and stale lifecycle mutations.

API-key authentication rejects revoked and expired credentials. `last_used_at`
is approximate: Lians performs a throttled conditional update, so hot credentials
do not cause one write per request and concurrent replicas cannot continually
rewrite the row.
PostgreSQL resolves the exact SHA-256 digest through a PUBLIC-revoked SECURITY
DEFINER function that returns only active authorization fields, then establishes
namespace/barrier RLS before the optional `last_used_at` update. Direct runtime
reads of `api_keys` are RLS-constrained; the table omits FORCE solely for the
reviewed table-owner lookup, whose owner, settings, and grants are checked at
startup and readiness.

## Create

```http
POST /v1/identity/workload-credentials
Authorization: Bearer <human-oidc-access-token>
Content-Type: application/json

{
  "label": "production-recorder",
  "role": "analyst",
  "scopes": [],
  "barrier_group": "equities",
  "ttl_seconds": 86400
}
```

The response includes `secret` exactly once. Deliver it directly to the target
workload's secret manager; do not log or persist the response body in CI output.
The response is marked `no-store`. Creation rejects `Idempotency-Key`: after a
lost response, list redacted credentials and inspect audit events before
creating a replacement because Lians cannot replay the plaintext secret.

## List and inspect

```http
GET /v1/identity/workload-credentials?include_expired=true&include_revoked=true
GET /v1/identity/workload-credentials/{credential_id}
Authorization: Bearer <human-oidc-access-token>
```

These endpoints return metadata, never a digest or plaintext secret. A
barrier-scoped administrator sees only credentials for that exact barrier.

## Rotate

```http
POST /v1/identity/workload-credentials/{credential_id}/rotate
Authorization: Bearer <human-oidc-access-token>
Content-Type: application/json

{"expected_version": 1, "ttl_seconds": 86400}
```

Rotation atomically revokes the predecessor and returns one new secret with the
same least-privilege grants. A `409` means the supplied version is stale or the
credential is already inactive; refresh metadata before deciding what to do.
Rotation is non-cacheable and rejects `Idempotency-Key`; reconcile the
predecessor's rotation edge before another attempt.

## Revoke

```http
DELETE /v1/identity/workload-credentials/{credential_id}?expected_version=1
Authorization: Bearer <human-oidc-access-token>
```

Revocation takes effect on the next authentication attempt. Existing stateless
requests already executing are not recalled. Revocation rejects
`Idempotency-Key`; list metadata after an ambiguous response.

## Operational policy

Prefer one credential per workload and environment, narrow scopes, the shortest
practical TTL, and automated rotation before expiry. Alert on break-glass API-key
events, tenant credentials unused beyond your policy window, repeated expired-key
authentication failures, and credentials approaching expiry without a successor.
