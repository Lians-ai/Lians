# Billing & Tier Integration

How Clerk plans map to lians features, how to provision API keys per tier, how to gate routes and UI, and how to handle upgrades and downgrades.

> **Scope:** this document describes tiering for the **managed cloud offering
> only**. The self-hosted product is Apache 2.0; every feature
> gated below (information barriers, crypto-shred erasure, backtest checks,
> audit chain, air-gap mode) ships in the open-source repository with no
> license key. Tiers here gate access to *our hosted instance*, not the software.

---

## Tier Overview

| Tier | Price | Monthly Decisions | Monthly Protected Actions |
|---|---|---|---|
| Free | $0 | 10,000 | 10,000 |
| Starter | $15/mo | 100,000 | 50,000 |
| Growth | $69/mo | 500,000 | 250,000 |
| Pro | $199/mo | 2,000,000 | 1,000,000 |
| Enterprise | Custom | Contracted volume | Contracted volume |

The canonical commercial units are authoritative decision creation and
successful single-use Gate permit consumption. Overage on paid tiers is billed
through those transactionally recorded protected units. Memory writes and
successful recalls remain distinct compatibility meters for pre-existing
memory-product contracts; do not use them as a proxy for decision protection.
Deployment, reconciliation, and recovery requirements live in the
[durable metering runbook](durable-metering.md).

---

## Tier → Lians Scopes

When you provision an API key on signup, the `scopes` array you send to `POST /v1/admin/api-keys` controls what the key can do. Map Clerk plan features to scopes like this:

| Clerk Feature | Lians Scope | Tiers |
|---|---|---|
| Memory writes | `write` | All |
| Memory recalls | `read` | All |
| Authoritative decisions | `write` | All |
| Protected Gate actions | `write` | All |
| Semantic search | `read` | All |
| Domain adapters | `adapters` | Starter+ |
| Audit log | `audit` | Starter+ |
| Conflict detection | `conflicts` | Growth+ |
| Webhooks | `webhooks` | Growth+ |
| Compliance reports | `compliance` | Growth+ |
| Serialized hash-chain audit | `compliance` | Growth+ |
| Information barriers | `barriers` | Pro+ |
| HIPAA encryption | `hipaa` | Pro+ |
| GDPR erasure certificates | `erasure` | Pro+ |
| Backtest | `backtest` | Pro+ |
| Prometheus metrics | `metrics` | Pro+ |
| Air-gap mode | `airgap` | Enterprise |
| Custom KMS | `kms` | Enterprise |

```ts
// lib/lians-tiers.ts
export const TIER_SCOPES: Record<string, string[]> = {
  free:       ["read", "write"],
  starter:    ["read", "write", "adapters", "audit"],
  growth:     ["read", "write", "adapters", "audit", "conflicts", "webhooks", "compliance"],
  pro:        ["read", "write", "adapters", "audit", "conflicts", "webhooks", "compliance",
               "barriers", "hipaa", "erasure", "backtest", "metrics"],
  enterprise: ["read", "write", "adapters", "audit", "conflicts", "webhooks", "compliance",
               "barriers", "hipaa", "erasure", "backtest", "metrics", "airgap", "kms"],
}

export const TIER_QUOTAS: Record<string, {
  decisions: number
  protectedActions: number
}> = {
  free:       { decisions: 10_000,    protectedActions: 10_000 },
  starter:    { decisions: 100_000,   protectedActions: 50_000 },
  growth:     { decisions: 500_000,   protectedActions: 250_000 },
  pro:        { decisions: 2_000_000, protectedActions: 1_000_000 },
  enterprise: { decisions: Infinity,  protectedActions: Infinity },
}
```

---

## Step 1 — Clerk Webhook: Provision Key on Signup

Clerk fires `user.created` after checkout. Read the plan slug from `publicMetadata` (Clerk sets this when a user subscribes), derive the scopes, provision the key, and wire the Stripe customer ID for usage metering.

```ts
// app/api/webhooks/clerk/route.ts
import { Webhook } from "svix"
import { clerkClient } from "@clerk/nextjs/server"
import { TIER_SCOPES } from "@/lib/lians-tiers"

const LIANS_API = process.env.LIANS_API_URL          // e.g. https://api.lians.dev
const ADMIN_SECRET = process.env.LIANS_ADMIN_SECRET  // admin_secret from lians config

export async function POST(req: Request) {
  const payload = await req.text()
  const headers = Object.fromEntries(req.headers)

  const wh = new Webhook(process.env.CLERK_WEBHOOK_SECRET!)
  const event = wh.verify(payload, headers) as any

  if (event.type === "user.created") {
    const clerkUserId: string = event.data.id

    // Clerk sets plan slug on publicMetadata after checkout — default to "free"
    const tier: string = event.data.public_metadata?.plan ?? "free"
    const stripeCustomerId: string | undefined = event.data.private_metadata?.stripe_customer_id

    const scopes = TIER_SCOPES[tier] ?? TIER_SCOPES.free

    // 1. Provision the lians API key
    const keyRes = await fetch(`${LIANS_API}/v1/admin/api-keys`, {
      method: "POST",
      headers: {
        "X-Admin-Secret": ADMIN_SECRET!,
        "Content-Type": "application/json",
      },
      body: JSON.stringify({
        namespace: clerkUserId,
        label: "default",
        scopes,
      }),
    })

    if (!keyRes.ok) throw new Error(`Key provision failed: ${await keyRes.text()}`)
    const { key, id: keyId, version: keyVersion } = await keyRes.json()

    // 2. Store the plaintext key once in Clerk private metadata for one-time reveal
    //    Store the key ID in your own DB for rotate/revoke operations
    await clerkClient.users.updateUserMetadata(clerkUserId, {
      privateMetadata: {
        pendingApiKey: key,
        liansKeyId: keyId,
        liansKeyVersion: keyVersion,
        liansTier: tier,
      },
    })

    // 3. Wire Stripe customer ID for usage metering
    if (stripeCustomerId) {
      const currentBilling = await fetch(
        `${LIANS_API}/v1/admin/billing/${clerkUserId}`,
        { headers: { "X-Admin-Secret": ADMIN_SECRET! } },
      ).then(response => response.json())
      await fetch(`${LIANS_API}/v1/admin/billing/${clerkUserId}`, {
        method: "PUT",
        headers: {
          "X-Admin-Secret": ADMIN_SECRET!,
          "Content-Type": "application/json",
        },
        body: JSON.stringify({
          expected_updated_at: currentBilling.updated_at,
          stripe_customer_id: stripeCustomerId,
        }),
      })
    }
  }
}
```

---

## Step 2 — Dashboard: Reveal Key Once

The plaintext key is stored in Clerk private metadata and cleared on first read. After that, only the key ID is available (for rotate/revoke).

```ts
// app/api/user/api-key/route.ts
import { auth, clerkClient } from "@clerk/nextjs/server"

export async function GET() {
  const { userId } = auth()
  if (!userId) return new Response("Unauthorized", { status: 401 })

  const user = await clerkClient.users.getUser(userId)
  const pending = user.privateMetadata?.pendingApiKey as string | undefined

  if (pending) {
    // Clear immediately — never show again
    await clerkClient.users.updateUserMetadata(userId, {
      privateMetadata: { ...user.privateMetadata, pendingApiKey: null },
    })
    return Response.json({ key: pending, keyId: user.privateMetadata?.liansKeyId, fresh: true })
  }

  return Response.json({ keyId: user.privateMetadata?.liansKeyId, fresh: false })
}
```

```tsx
// components/ApiKeyPanel.tsx
"use client"
import { useEffect, useState } from "react"

export function ApiKeyPanel() {
  const [data, setData] = useState<{ key?: string; keyId?: string; fresh: boolean } | null>(null)

  useEffect(() => {
    fetch("/api/user/api-key").then(r => r.json()).then(setData)
  }, [])

  if (!data) return <p>Loading...</p>

  return (
    <div>
      {data.fresh ? (
        <div className="rounded border border-yellow-400 bg-yellow-50 p-4">
          <p className="font-semibold">Copy your API key — it will not be shown again.</p>
          <code className="block mt-2 break-all">{data.key}</code>
          <button onClick={() => navigator.clipboard.writeText(data.key!)}>
            Copy
          </button>
        </div>
      ) : (
        <p>API key ending in <code>...{data.keyId?.slice(-8)}</code></p>
      )}
      <button onClick={rotateKey}>Rotate key</button>
    </div>
  )
}

async function rotateKey() {
  const res = await fetch("/api/user/api-key/rotate", { method: "POST" })
  const { key } = await res.json()
  alert(`New key (copy now): ${key}`)
}
```

---

## Step 3 — Key Rotation

```ts
// app/api/user/api-key/rotate/route.ts
import { auth, clerkClient } from "@clerk/nextjs/server"

export async function POST() {
  const { userId } = auth()
  if (!userId) return new Response("Unauthorized", { status: 401 })

  const user = await clerkClient.users.getUser(userId)
  const keyId = user.privateMetadata?.liansKeyId as string
  const keyVersion = user.privateMetadata?.liansKeyVersion as number

  const res = await fetch(`${process.env.LIANS_API_URL}/v1/admin/api-keys/${keyId}/rotate?expected_version=${keyVersion}`, {
    method: "POST",
    headers: { "X-Admin-Secret": process.env.LIANS_ADMIN_SECRET! },
  })

  if (res.status === 409) return new Response("Key changed; refresh before rotating", { status: 409 })
  if (!res.ok) return new Response("Rotate failed", { status: 502 })
  const { key, id: newKeyId, version: newKeyVersion } = await res.json()

  await clerkClient.users.updateUserMetadata(userId, {
    privateMetadata: {
      ...user.privateMetadata,
      liansKeyId: newKeyId,
      liansKeyVersion: newKeyVersion,
    },
  })

  // Return directly — this response is the one-time reveal
  return Response.json({ key })
}
```

Key creation, rotation, and revocation return one-time or terminal results and
reject `Idempotency-Key`. If the response is lost, list the namespace's keys and
reconcile the current key ID/version before taking another action.

---

## Step 4 — Handle Plan Upgrades and Downgrades

Clerk fires `user.updated` when a subscription changes. Read the new plan, derive the new scopes, rotate the key so the new scopes take effect immediately.

```ts
// Inside the Clerk webhook handler (app/api/webhooks/clerk/route.ts)

if (event.type === "user.updated") {
  const clerkUserId: string = event.data.id
  const newTier: string = event.data.public_metadata?.plan ?? "free"
  const newScopes = TIER_SCOPES[newTier] ?? TIER_SCOPES.free

  const user = await clerkClient.users.getUser(clerkUserId)
  const currentTier = user.privateMetadata?.liansTier as string | undefined

  // Only act if the plan actually changed
  if (currentTier === newTier) return new Response("OK")

  const keyId = user.privateMetadata?.liansKeyId as string
  const keyVersion = user.privateMetadata?.liansKeyVersion as number

  // Rotation preserves the old scopes. Revoke the old credential with an exact
  // version precondition before provisioning the replacement scope set.
  const revokeRes = await fetch(`${process.env.LIANS_API_URL}/v1/admin/api-keys/${keyId}?expected_version=${keyVersion}`, {
    method: "DELETE",
    headers: { "X-Admin-Secret": process.env.LIANS_ADMIN_SECRET! },
  })
  if (revokeRes.status === 409) throw new Error("Key changed; reconcile before retrying")
  if (!revokeRes.ok) throw new Error(`Key revoke failed: ${await revokeRes.text()}`)

  const keyRes = await fetch(`${process.env.LIANS_API_URL}/v1/admin/api-keys`, {
    method: "POST",
    headers: {
      "X-Admin-Secret": process.env.LIANS_ADMIN_SECRET!,
      "Content-Type": "application/json",
    },
    body: JSON.stringify({
      namespace: clerkUserId,
      label: "default",
      scopes: newScopes,
    }),
  })

  if (!keyRes.ok) throw new Error(`Key provision failed: ${await keyRes.text()}`)
  const { key, id: newKeyId, version: newKeyVersion } = await keyRes.json()

  await clerkClient.users.updateUserMetadata(clerkUserId, {
    privateMetadata: {
      ...user.privateMetadata,
      pendingApiKey: key,
      liansKeyId: newKeyId,
      liansKeyVersion: newKeyVersion,
      liansTier: newTier,
    },
  })
}
```

The user will see the "copy your new key" banner next time they visit the dashboard — the same one-time reveal flow as signup.
In production, persist this revoke/provision/metadata-update sequence as a
durable state machine: a crash after provisioning but before saving the
plaintext replacement cannot be repaired by blindly repeating the create call.

---

## Step 5 — Frontend Feature Gating

Use Clerk's `has()` helper to show or hide UI sections based on the features you defined per plan in the Clerk dashboard.

```tsx
// components/FeatureGate.tsx
import { useAuth } from "@clerk/nextjs"

// Gate any component behind a Clerk plan feature
export function FeatureGate({
  feature,
  children,
  fallback = null,
}: {
  feature: string
  children: React.ReactNode
  fallback?: React.ReactNode
}) {
  const { has } = useAuth()
  return has?.({ feature }) ? <>{children}</> : <>{fallback}</>
}
```

```tsx
// Usage in your dashboard
import { FeatureGate } from "@/components/FeatureGate"

export function DashboardPage() {
  return (
    <div>
      {/* Visible to all tiers */}
      <MemoryPanel />

      {/* Starter+ */}
      <FeatureGate feature="domain_adapters" fallback={<UpgradeBanner to="starter" />}>
        <AdapterSelector />
      </FeatureGate>

      {/* Growth+ */}
      <FeatureGate feature="conflict_detection" fallback={<UpgradeBanner to="growth" />}>
        <ConflictFlagsPanel />
      </FeatureGate>

      {/* Pro+ */}
      <FeatureGate feature="information_barriers" fallback={<UpgradeBanner to="pro" />}>
        <BarrierGroupManager />
      </FeatureGate>

      <FeatureGate feature="gdpr_erasure_certificates">
        <ErasureCertificateDownload />
      </FeatureGate>

      {/* Enterprise */}
      <FeatureGate feature="air_gap_mode">
        <AirgapConfigPanel />
      </FeatureGate>
    </div>
  )
}
```

The feature names here must exactly match what you named them in the Clerk dashboard when setting up each plan.

---

## Step 6 — Backend Route Protection via Scopes

Lians already checks scopes via `AuthContext.require()` in `agentmem/src/lians/api/deps.py`. Add scope checks to the relevant routes so a downgraded or free-tier key gets a `403` if it tries to use a feature above its tier.

Routes to protect and their required scope:

| Route | Required Scope |
|---|---|
| `POST /v1/memory` | `write` |
| `POST /v1/recall` | `read` |
| `GET /v1/audit/*` | `audit` |
| `GET /v1/conflicts` | `conflicts` |
| `POST /v1/webhooks` | `webhooks` |
| `GET /v1/compliance/*` | `compliance` |
| `POST /v1/backtest` | `backtest` |
| `GET /v1/snapshot` | `compliance` |
| `POST /v1/privacy/erase` | `erasure` |
| `GET /v1/admin/barriers` | `barriers` |
| `GET /metrics` | `metrics` |

Example — adding scope check to a route that doesn't have one yet:

```python
# In any route that should be Growth+ only
from ..api.deps import get_auth, AuthContext

@router.get("/v1/conflicts")
async def list_conflicts(auth: AuthContext = Depends(get_auth), ...):
    auth.require("conflicts")   # returns 403 if scope missing
    ...
```

---

## Step 7 — Map Plans to Enforced Namespace Quotas

Lians already enforces optional UTC-day quotas transactionally. Recorder events,
decision records, memory writes, recalls, and estimated ingest bytes are reserved in
the same database transaction as the protected operation. A request that would
exceed an active limit is rejected with `429` and a bounded `Retry-After` value; a
rolled-back operation does not consume capacity.

Your billing/provisioning control plane should completely replace the namespace
policy through `PUT /v1/admin/governance/policies/{namespace}` and then activate it
through `PUT /v1/admin/governance/policies/{namespace}/status`. Both mutations use
`expected_version` compare-and-swap protection. Do not infer plan entitlements in
the request path from mutable Clerk metadata.

Use `GET /v1/governance/effective` and `GET /v1/governance/usage` for tenant-visible
limits, UTC reset time, reserved usage, and remaining capacity. Immutable policy
revisions are available to administrators at
`GET /v1/admin/governance/policies/{namespace}/revisions`.

Monthly contract limits, pooled organization entitlements, and grace-period policy
remain responsibilities of the billing control plane. Translate those commercial
rules into conservative daily Lians decision limits. Protected-action monthly
entitlements remain a billing-control-plane responsibility until a first-class Gate
quota is configured. Stripe metering is an independent usage record and must not be
treated as the enforcement boundary.

---

## Environment Variables Required

```bash
# lians backend
STRIPE_API_KEY=sk_live_...
STRIPE_METER_DECISION_EVENT=lians_authoritative_decision
STRIPE_METER_PROTECTED_ACTION_EVENT=lians_protected_action
# Compatibility-only meters for existing memory-product contracts.
STRIPE_METER_WRITE_EVENT=agentmem_memory_write
STRIPE_METER_RECALL_EVENT=agentmem_memory_recall
# Set true only after Stripe's asynchronous meter-error thin events are routed
# to a durable, monitored destination (see docs/durable-metering.md).
STRIPE_METER_ASYNC_ERROR_DESTINATION_CONFIGURED=true
ADMIN_SECRET=your-admin-secret

# your website
LIANS_API_URL=https://api.lians.dev
LIANS_ADMIN_SECRET=your-admin-secret     # same value as ADMIN_SECRET above
CLERK_WEBHOOK_SECRET=whsec_...           # from Clerk dashboard → Webhooks
```

---

## Summary Checklist

- [ ] Clerk: plans created with correct feature flags per tier
- [ ] Clerk: webhook endpoint registered, subscribed to `user.created` and `user.updated`
- [ ] Website: `POST /api/webhooks/clerk` handler provisions key on signup
- [ ] Website: `GET /api/user/api-key` reveals key once, clears from metadata
- [ ] Website: `POST /api/user/api-key/rotate` rotates key, reveals new one
- [ ] Website: `FeatureGate` component wraps tier-locked UI sections
- [ ] lians: scope checks added to Growth+/Pro+/Enterprise routes
- [ ] lians: `PUT /v1/admin/billing/{namespace}` called on signup to wire Stripe customer ID
- [ ] lians: durable metering migration, worker alerts, and Stripe asynchronous-error destination verified per `docs/durable-metering.md`
- [ ] Plan provisioning writes and activates versioned namespace quotas
- [ ] Tenant UI reads effective limits and UTC reset time from the governance API
