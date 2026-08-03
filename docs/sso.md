# Native OIDC identity federation

Lians verifies OIDC JWTs natively and resolves each verified issuer subject to
an administrator-owned identity binding. The binding supplies namespace,
principal type, role/scopes, optional authorized party, and information barrier;
none of those authorization fields are trusted from request headers or bodies.

The compatibility gateway-forward-auth pattern remains possible, but native
OIDC bearer authentication is the preferred identity-aware path because Lians
verifies issuer, audience, signature algorithm, signing key, token times, token
age, and the exact subject binding itself.

## Trust an issuer and bind a principal

Platform operators use the break-glass-protected identity administration API:

1. `POST /v1/admin/identity/providers` registers an exact issuer, JWKS URI,
   audiences, permitted algorithms, required claims, and token-age policy.
2. `POST /v1/admin/identity/bindings` maps one verified issuer subject to a
   tenant authorization context.
3. `POST /v1/admin/identity/providers/{id}/probe` validates that the configured
   JWKS endpoint yields usable signing keys.
4. `GET /v1/identity/whoami` lets a bearer inspect the resulting context.

Provider and binding lifecycle mutations require the exact `expected_version`
returned by the latest administrative read. A stale mutation returns `409`;
re-read the authoritative object and decide again. Provider revocation and
binding creation lock the same provider row, so a new binding cannot slip
through a concurrent provider revocation. Revocation is
immediate, and provider changes also clear the in-process JWKS cache.

Provider and binding revocations deliberately reject `Idempotency-Key`: their
responses are not stored in the generic replay ledger. If the client loses the
response, reconcile with the administrative GET/list surface before issuing a
new request. Lians does not follow a token-supplied `jku` or `x5u`, and its JWKS
fetcher applies network destination guards.

## Identity binding

Each binding contains:

| Field | Meaning |
|---|---|
| `namespace` | Tenant boundary enforced by application queries and PostgreSQL RLS |
| `principal_type` | `human` or `workload` |
| `role` / `scopes` | Effective tenant permissions |
| `barrier_group` | Optional information-barrier wall |
| `authorized_party` | Optional required OAuth `azp`/`client_id` |

Named roles expand as follows: `owner` = read/write/admin, `analyst` =
read/write, `compliance` = read/admin, and `readonly` = read. Information
barriers remain restrictive even when identities share a namespace.

After JWT verification, PostgreSQL resolves only the exact `(provider UUID,
external subject)` pair through a PUBLIC-revoked SECURITY DEFINER function that
returns the active authorization fields but not the raw subject. Direct runtime
access to `identity_bindings` is namespace/barrier RLS-constrained. The table
deliberately omits FORCE only for that table-owner lookup; production readiness
proves the API login is a non-owner without `BYPASSRLS` and verifies the
function owner, fixed settings, and grants.

## Workload authentication choices

For workloads, prefer either:

- a short-lived OIDC workload token whose binding has
  `principal_type=workload` and, where available, an exact `authorized_party`;
  or
- an expiring, tenant-managed workload API credential created by a human OIDC
  administrator through `/v1/identity/workload-credentials`.

See [workload-credentials.md](workload-credentials.md) for issuance, rotation,
revocation, expiry, least-privilege delegation, and the deliberate separation
from the `/v1/admin/api-keys` break-glass surface.

## SCIM provisioning

SCIM 2.0 users and groups can drive deterministic identity bindings and tenant
membership. SCIM bearer credentials are digest-only, independently rotatable,
and administered through the dedicated SCIM administration surface. Keep SCIM
provisioning credentials separate from OIDC signing trust and workload secrets.

## Gateway/SAML compatibility

An enterprise gateway may still terminate SAML or OIDC and call Lians with a
pre-provisioned API key. In that mode, the key—not an injected identity header—
is the authorization boundary. Never allow a proxy to select namespace, role,
scope, or barrier from unsigned client-controlled headers. Native OIDC should be
used whenever the application needs a verifiable human/workload principal in
Decision Receipts, Gate approvals, reviews, or audit events.
