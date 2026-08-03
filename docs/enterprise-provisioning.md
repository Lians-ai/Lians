# Enterprise provisioning with SCIM 2.0

Lians exposes a tenant-isolated SCIM 2.0 service-provider foundation for
provisioning human identities from enterprise identity providers. Provisioned
users are reconciled into the native OIDC `IdentityBinding` table, so SCIM does
not create a second authentication path or trust authorization claims supplied
in SCIM payloads.

## Security model

- Every SCIM service-provider configuration belongs to exactly one Lians
  namespace and one administrator-approved `TrustedIdentityProvider`.
- The service base path contains an opaque tenant configuration UUID:
  `/scim/v2/{tenant_config_id}`.
- A SCIM bearer token can authenticate only against the configuration that
  issued it. The server stores a SHA-256 digest of each 384-bit generated token;
  plaintext is returned only on configuration creation or credential rotation.
- SCIM configuration, credentials, users, groups, memberships, and entitlements
  are protected by forced PostgreSQL row-level security. Composite foreign keys
  additionally prevent a user or group membership from crossing namespaces.
- A Group contains at most 1,000 Users and one User belongs to at most 1,000
  Groups. The service takes the tenant mutation lock, performs exact capacity
  checks, and PostgreSQL independently serializes and enforces the per-User
  bound. Create, replace, PATCH reconciliation, and deletion are complete or
  fail without changing a subset of memberships.
- Every mutation is appended to the Lians audit chain. Tokens are never logged.
  External identity subjects are represented only by a SHA-256 fingerprint in
  audit payloads.
- Tenant enable, disable, and revoke do not scan every User in the request.
  They atomically freeze an exact `(created_at, id)` User snapshot and enqueue a
  leased, resumable reconciliation job fenced to that tenant version. Disable
  and revoke first bulk-disable all linked bindings in the same transaction, so
  access closes before the asynchronous audit-rich reconciliation begins.
- Enabling is also fail closed. Every SCIM-managed binding carries the current
  tenant/version activation fence. PostgreSQL's pre-tenant authentication
  function and the SQLite development lookup both reject that binding until the
  fixed snapshot is `completed`; a failed or retried job cannot expose a
  partially reconciled subset. Manual bindings and pre-0062 bindings with no
  SCIM fence retain their compatibility behavior.
- `PUT`, `PATCH`, and `DELETE` require `If-Match`. A stale version receives
  `412 invalidVers`; an omitted precondition receives `428 invalidVers`.

Treat the returned SCIM token like a production secret: place it in the identity
provider's secret store, never a source file or shell history. Configure an
expiry and rotate it on a regular schedule.

## Bootstrap

First register the enterprise OIDC issuer through the native identity federation
admin API. Then create the SCIM tenant configuration:

```http
POST /v1/admin/enterprise/scim/tenants
X-Admin-Secret: <admin secret>
Content-Type: application/json

{
  "namespace": "acme-prod",
  "provider_id": "69bc4cc8-7022-4984-8bd4-3b759d773eb8",
  "subject_attribute": "externalId",
  "credential_label": "entra-production",
  "credential_expires_at": "2027-01-01T00:00:00Z"
}
```

The response contains `tenant`, `credential`, `scim_base_path`, and
`bearer_token`. The token is never retrievable again. Configure the identity
provider with the returned base path and token.

The creation and credential-rotation responses are non-cacheable. These
one-time token operations, tenant/credential revocation, and other destructive
credential lifecycle calls reject `Idempotency-Key`. If a response is lost,
list tenant and credential metadata and inspect the audit trail before creating
or rotating again; the plaintext token cannot be replayed.

Tenant, credential, and entitlement administrative list responses retain their
array bodies but are exact keyset inventory pages. They report
`X-Lians-Total-Count`, page limit/returned counts, page and collection
completeness, and continuation headers. Entitlement traversal continues to
accept `after_group_id` and emit `X-Lians-Next-Group-Id`; the paired
`X-Lians-Next-After-Group-Id` header is the canonical cursor spelling.

`subject_attribute` controls the deterministic OIDC subject mapping:

- `externalId` (recommended) maps SCIM `externalId` to the verified JWT `sub`.
  An active SCIM user must provide it.
- `userName` maps SCIM `userName` to `sub`. Use this only if the issuer guarantees
  that user names are stable, immutable subjects.

## Authorization mappings

SCIM payloads cannot grant a Lians role, scope, or information barrier. An
administrator maps a provisioned SCIM group to an authorization contribution:

```http
PUT /v1/admin/enterprise/scim/tenants/{tenant_id}/groups/{group_id}/entitlement
X-Admin-Secret: <admin secret>
Content-Type: application/json

{
  "role": "analyst",
  "scopes": ["read", "write"],
  "barrier_group": "investment-banking"
}
```

For an update, include the mapping's `expected_version`. Lians unions explicit
scopes and accepts at most one distinct non-null role and one distinct non-null
barrier across all groups assigned to a user. A membership or mapping change
that would produce two roles or two barriers is rejected atomically. This makes
authorization independent of group ordering and fails closed under ambiguity.
The union may contain at most 50 distinct scopes. Reconciliation counts the
complete per-User membership first and refuses an over-capacity or malformed
legacy state; it never authorizes from a truncated Group set.

An active user with no role and no scope remains provisioned but its native
identity binding is disabled. Adding an unambiguous mapped group enables it.
Deactivating or deleting a SCIM user disables its identity binding in the same
database transaction. Deleting a group removes its memberships and immediately
recomputes every affected binding.

Tenant configuration `PATCH` returns `202` plus `Location`,
`X-Lians-Reconciliation-Job-Id`, and `X-Lians-Reconciliation-Status`. Tenant
revocation retains its `204` body contract and returns the same headers. Inspect
exact progress at:

```http
GET /v1/admin/enterprise/scim/tenants/{tenant_id}/binding-reconciliations/{job_id}
X-Admin-Secret: <admin secret>
```

The response reports the immutable target version and snapshot boundary, exact
User total, durable cursor, reconciled count, attempts, page count, lease,
terminal status, and explicit `snapshot_complete`. A failed fixed snapshot is
retried only through `POST .../{job_id}/retry` after remediation. Operators may
use `POST .../{job_id}/advance` to lease and process at most one configured page;
it uses the same fencing and worker path as autonomous processing. A newer tenant
version marks older work `superseded`; stale work can never re-enable access.
The final page flips every binding for that tenant/version to complete in the
same transaction as the job's completion audit event. Users created between
pages are synchronously fenced and included in that final activation update,
even though the job's exact progress denominator remains its original snapshot.

`DELETE` creates a security tombstone and does not permit the deleted
`userName`, `externalId`, or group identity to be silently reused. Use
`PATCH active=false` for reversible employee suspension and reactivation.

## SCIM endpoints

All SCIM requests use:

```http
Authorization: Bearer <SCIM token>
Accept: application/scim+json
Content-Type: application/scim+json
```

The service provides:

| Method | Path | Behavior |
|---|---|---|
| `GET` | `/ServiceProviderConfig` | Advertises PATCH, filtering, and ETag support |
| `GET` | `/ResourceTypes` | Lists User and Group resource types |
| `GET` | `/Schemas` | Lists supported core schemas |
| `POST` | `/Users` | Creates and reconciles a user |
| `GET` | `/Users` | Paginated user list |
| `GET` | `/Users/{id}` | Gets one user |
| `PUT/PATCH/DELETE` | `/Users/{id}` | Replaces, patches, or deactivates a user |
| `POST` | `/Groups` | Creates a group and optional memberships |
| `GET` | `/Groups` | Paginated group list |
| `GET` | `/Groups/{id}` | Gets one group and its memberships |
| `PUT/PATCH/DELETE` | `/Groups/{id}` | Replaces, patches, or deletes a group |

Lists use one-based `startIndex` and `count` from 0 through 100. Equality filters
are supported for `userName`, `externalId`, and `displayName` where the attribute
exists, for example:

```text
filter=userName eq "ada@example.com"
```

`GET /Groups` batch-loads all memberships for the returned Group page in one
bounded expansion instead of issuing one query per Group. Every Group remains
complete up to 1,000 Users, while the page also has a 10,000-member and 8 MiB
complete-response ceiling by default. If either cumulative limit is exceeded,
the endpoint returns SCIM `413 tooMany` and no partially populated Group.

PATCH supports the core scalar User fields, name subattributes, emails, Group
members, and filtered member removal such as:

```json
{
  "schemas": ["urn:ietf:params:scim:api:messages:2.0:PatchOp"],
  "Operations": [
    {
      "op": "Remove",
      "path": "members[value eq \"d522e093-3634-4422-840c-24c0b21b957f\"]"
    }
  ]
}
```

Group request bodies and the combined result of all PATCH operations are capped
at 1,000 unique Users. Adding a Group that would make any referenced User exceed
1,000 Groups returns a SCIM `409` error with `scimType: "tooMany"`; no edge in
that request is committed. A pre-contract database that already exceeds a
complete-resource bound returns `413 tooMany` rather than omitting members.

Responses include a weak ETag such as `W/"4"`. Send that exact value in
`If-Match` on the next mutation. Lians locks the resource before comparing the
version. Missing `If-Match` returns `428 invalidVers`; a stale value returns
`412 invalidVers`. After an ambiguous `PUT`, `PATCH`, or `DELETE`, GET the
resource before deciding whether another mutation is needed.

## Credential operations

List redacted credential metadata:

```http
GET /v1/admin/enterprise/scim/tenants/{tenant_id}/credentials
X-Admin-Secret: <admin secret>
```

Rotate a credential, returning a new token once:

```http
POST /v1/admin/enterprise/scim/tenants/{tenant_id}/credentials/{credential_id}/rotate
X-Admin-Secret: <admin secret>
Content-Type: application/json

{
  "expected_version": 1,
  "label": "entra-2027-q1",
  "expires_at": "2027-04-01T00:00:00Z",
  "revoke_prior": true
}
```

Revoke without replacement with
`DELETE .../credentials/{credential_id}?expected_version=N`. Revocation is
effective on the next request. Disabling or revoking the tenant configuration
also blocks every SCIM credential and disables all linked identity bindings.
Every rotation advances the predecessor's version even when `revoke_prior` is
false, so the same observed version cannot create multiple successors.

## Application integration hooks

The provisioning files are deliberately isolated. The application assembler
must perform these two imports when integrating the module:

```python
# agentmem/src/lians/main.py
from .api.routes_scim import admin_router as scim_admin_router, router as scim_router

app.include_router(scim_admin_router)
app.include_router(scim_router)
```

Alembic metadata assembly must import the models so future autogeneration sees
them:

```python
# agentmem/alembic/env.py
from lians import enterprise_models  # noqa: F401
```

Revision `0031_enterprise_provisioning`, whose parent is
`0030_identity_federation`, is the schema foundation. Production deployments
must apply the complete packaged graph through
`0063_admin_identity_indexes` before enabling the routes. The later revisions
add the serialized inverse-membership capacity boundary, protected
authentication bootstrap, and the forced-RLS durable tenant reconciliation
queue.
