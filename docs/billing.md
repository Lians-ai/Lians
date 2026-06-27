# Billing & Tier Integration

How Clerk plans map to lians features, how to provision API keys per tier, how to gate routes and UI, and how to handle upgrades and downgrades.

---

## Tier Overview

| Tier | Price | Monthly Writes | Monthly Recalls |
|---|---|---|---|
| Free | $0 | 10,000 | 10,000 |
| Starter | $15/mo | 100,000 | 50,000 |
| Growth | $69/mo | 500,000 | 250,000 |
| Pro | $199/mo | 2,000,000 | 1,000,000 |
| Enterprise | Custom | Unlimited | Unlimited |

Overage on paid tiers is billed via Stripe usage metering (writes + recalls) — the metering worker in `agentmem/src/lians/metering.py` already handles this.

---

## Tier → Lians Scopes

When you provision an API key on signup, the `scopes` array you send to `POST /v1/admin/api-keys` controls what the key can do. Map Clerk plan features to scopes like this:

| Clerk Feature | Lians Scope | Tiers |
|---|---|---|
| Memory writes | `write` | All |
| Memory recalls | `read` | All |
| Semantic search | `read` | All |
| Domain adapters | `adapters` | Starter+ |
| Audit log | `audit` | Starter+ |
| Conflict detection | `conflicts` | Growth+ |
| Webhooks | `webhooks` | Growth+ |
| Compliance reports | `compliance` | Growth+ |
| Merkle audit chain | `compliance` | Growth+ |
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

export const TIER_QUOTAS: Record<string, { writes: number; recalls: number }> = {
  free:       { writes: 10_000,    recalls: 10_000 },
  starter:    { writes: 100_000,   recalls: 50_000 },
  growth:     { writes: 500_000,   recalls: 250_000 },
  pro:        { writes: 2_000_000, recalls: 1_000_000 },
  enterprise: { writes: Infinity,  recalls: Infinity },
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
    const { key, id: keyId } = await keyRes.json()

    // 2. Store the plaintext key once in Clerk private metadata for one-time reveal
    //    Store the key ID in your own DB for rotate/revoke operations
    await clerkClient.users.updateUserMetadata(clerkUserId, {
      privateMetadata: { pendingApiKey: key, liansKeyId: keyId, liansTier: tier },
    })

    // 3. Wire Stripe customer ID for usage metering
    if (stripeCustomerId) {
      await fetch(`${LIANS_API}/v1/admin/billing/${clerkUserId}`, {
        method: "PUT",
        headers: {
          "X-Admin-Secret": ADMIN_SECRET!,
          "Content-Type": "application/json",
        },
        body: JSON.stringify({ stripe_customer_id: stripeCustomerId }),
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

  const res = await fetch(`${process.env.LIANS_API_URL}/v1/admin/api-keys/${keyId}/rotate`, {
    method: "POST",
    headers: { "X-Admin-Secret": process.env.LIANS_ADMIN_SECRET! },
  })

  if (!res.ok) return new Response("Rotate failed", { status: 500 })
  const { key, id: newKeyId } = await res.json()

  await clerkClient.users.updateUserMetadata(userId, {
    privateMetadata: { ...user.privateMetadata, liansKeyId: newKeyId },
  })

  // Return directly — this response is the one-time reveal
  return Response.json({ key })
}
```

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

  // Rotate the key — old key is revoked, new key carries updated scopes
  // Note: rotation preserves namespace and label but we need to re-provision
  // with the new scopes. Rotate then update scopes via a new key.
  await fetch(`${process.env.LIANS_API_URL}/v1/admin/api-keys/${keyId}/rotate`, {
    method: "POST",
    headers: { "X-Admin-Secret": process.env.LIANS_ADMIN_SECRET! },
  })

  // Rotation copies old scopes — re-provision a fresh key with correct scopes instead
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

  const { key, id: newKeyId } = await keyRes.json()

  await clerkClient.users.updateUserMetadata(clerkUserId, {
    privateMetadata: {
      ...user.privateMetadata,
      pendingApiKey: key,
      liansKeyId: newKeyId,
      liansTier: newTier,
    },
  })
}
```

The user will see the "copy your new key" banner next time they visit the dashboard — the same one-time reveal flow as signup.

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

## Step 7 — Quota Enforcement (Future)

The metering worker reports usage to Stripe but does not currently block requests when a free-tier user hits their monthly limit. To enforce quotas you need to:

1. Add a `tier` column to `NamespacePolicy` (new Alembic migration).
2. Add a `writes_this_month` / `recalls_this_month` counter, reset on the 1st of each month by the scheduler (`agentmem/src/lians/scheduler.py`).
3. In `memory_service.py`, before writing or recalling, check the counter against `TIER_QUOTAS[tier]` and return `HTTP 429` with a `Retry-After` header when exceeded.

This is not implemented yet — the metering layer handles overage billing for paid tiers, so quota enforcement is only critical for the Free tier to prevent abuse.

---

## Environment Variables Required

```bash
# lians backend
STRIPE_API_KEY=sk_live_...
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
- [ ] Free tier quota enforcement (write `TIER_QUOTAS` check into `memory_service.py`)
