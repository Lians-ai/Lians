# SSO Integration

Lians authenticates API calls with namespace-scoped API keys. For human access and
enterprise identity, put SSO at the **gateway** in front of Lians — the standard,
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
   the user's team/role — or injects the identity headers your edge maps to one.
3. Lians enforces namespace + scope/role from that key.

This means **no IdP code in Lians**, SSO works with every provider, and revocation
/ MFA / conditional access stay in your IdP where security teams expect them.

## Mapping IdP groups → Lians roles

Map IdP group claims to Lians API keys with the matching `role` (see RBAC):

| IdP group | Lians role | Effective scopes |
|-----------|-----------|------------------|
| `lians-owners` | `owner` | read, write, admin |
| `lians-analysts` | `analyst` | read, write |
| `lians-compliance` | `compliance` | read, admin |
| `lians-viewers` | `readonly` | read |

Provision one API key per (team/namespace, role); the gateway selects the key from
the authenticated group claim. Rotate keys on role changes; revoke on offboarding.

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
