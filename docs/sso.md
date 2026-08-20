# SSO Integration

Lians authenticates API calls with namespace-scoped API keys. For human access and
enterprise identity, put SSO at the **gateway** in front of Lians - the standard,
low-coupling pattern that works with any IdP (Okta, Entra ID, Auth0, Ping, Google)
over OIDC or SAML, and keeps the memory layer focused on data, not identity.

## Pattern: forward-auth at the gateway

```
User ─▶ Reverse proxy / API gateway ──(OIDC/SAML)──▶ IdP
            │  (validates session, injects identity)
            ▼
        Lians API  ── X-API-Key (per team/role) ──▶ namespace + scopes
```

1. The gateway (NGINX `auth_request`, Envoy ext_authz, oauth2-proxy, Cloudflare
   Access, AWS ALB OIDC, etc.) authenticates the user against your IdP.
2. On success it forwards the request to Lians with a Lians **API key** chosen for
   the user's team/role - or injects the identity headers your edge maps to one.
3. Lians enforces namespace + scope/role from that key.

This means **no IdP code in Lians**, SSO works with every provider, and revocation
/ MFA / conditional access stay in your IdP where security teams expect them.

## Mapping IdP groups → namespace, role, and barrier

Each API key carries three things the gateway maps from the IdP group claim:
`namespace` (tenant), `role` (scopes), and an optional **`barrier_group`** (the
information-barrier wall). Provision one key per (namespace, role, barrier) and
have the gateway select it from the authenticated group claim.

| IdP group | namespace | role → scopes | barrier_group |
|-----------|-----------|---------------|---------------|
| `acme-equity-research` | `acme` | `analyst` → read, write | `research` |
| `acme-equity-trading` | `acme` | `analyst` → read, write | `trading` |
| `acme-compliance` | `acme` | `compliance` → read, admin | _(none - sees all)_ |
| `acme-viewers` | `acme` | `readonly` → read | `research` |

When a key has a `barrier_group`, **every read and write under it is scoped to
that wall at the database layer** (PostgreSQL RLS, migration 0013): a write is
tagged with the barrier and a read can only see that barrier's rows plus shared
(NULL-barrier) rows. So `acme-equity-research` and `acme-equity-trading` cannot see
each other's memories even though they share a namespace - the Chinese wall is
enforced below the application, driven entirely by the IdP group. A key with no
`barrier_group` (compliance) sees everything in its namespace.

This makes the **IdP group → namespace + role + barrier** chain end-to-end: the
identity decision in your IdP determines tenancy, permission, *and* isolation,
with no identity code in Lians. Rotate keys on role/desk changes; revoke on
offboarding.

## Per-tenant isolation

Each team/tenant maps to a Lians **namespace** (its own API key). Namespace
isolation is enforced at PostgreSQL RLS, so even a gateway misconfiguration cannot
read another tenant's memories. Within a namespace, **information barriers**
(barrier groups) wall off desks/care-teams/matters at the DB layer
(see [security-whitepaper.md](security-whitepaper.md) §4).

## Why not build OIDC into Lians?

Embedding a full OIDC/SAML stack would duplicate the IdP, couple releases to
identity changes, and expand the audit surface. Gateway forward-auth is the
recommended enterprise pattern (it is how most data services integrate SSO) and
keeps Lians' security boundary small and reviewable. A native OIDC mode may be
added later for turnkey single-node deployments; the gateway pattern remains the
recommendation for production.
