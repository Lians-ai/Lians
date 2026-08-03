# Namespace governance

Lians can enforce data-processing location, Recorder capture modes, and daily
operation/ingest quotas independently for each tenant namespace. Enforcement
happens in the authoritative transaction; clients cannot select or override the
processing region with a request header.

## Deployment region

Set `DEPLOYMENT_REGION` from deployment inventory, for example:

```dotenv
DEPLOYMENT_REGION=us-east-1
```

Production startup rejects blank and placeholder values such as `local`,
`unknown`, and `configure-me`. Region identifiers are normalized to lowercase
and may contain letters, numbers, `-`, `_`, and `.`. The identifiers are an
operator-defined contract: use one naming system consistently across deployment
inventory, policy automation, and customer agreements.

`DEPLOYMENT_REGION` is the sole enforcement input. Lians intentionally ignores
client-supplied region headers because a caller assertion is not proof of where
server-side processing occurs.

## Policy behavior

Governance extends the existing `namespace_policies` row without changing its
retention, legal-hold, or billing behavior.

- No policy row, or a governance status of `unconfigured`, preserves the legacy
  unlimited behavior and global Recorder capture settings.
- `active` enforces the namespace policy.
- `disabled` preserves the policy and history but temporarily returns the
  namespace to global/unlimited behavior.
- A `null` allowed-region list means every configured server region is allowed.
  An empty list denies every write in an active policy.
- A `null` capture-mode list means the global deployment modes apply. An empty
  list denies every Recorder event.
- Namespace policy can only restrict global capture. It cannot enable `full`
  capture when `RECORDER_ALLOW_FULL_CAPTURE=false`.
- A `null` quota means unlimited. A quota of zero denies that write category.

Policy replacement uses `PUT` semantics. Every mutation increments
`policy_version`, stores the authenticated admin actor from `X-Admin-Actor`,
adds an immutable content-hashed policy revision, and appends an audit-chain
event. Clearing governance fields does not delete retention or Stripe settings.

## Configure a policy

The examples use placeholders; do not put real admin credentials in shell
history or documentation.

```bash
curl -X PUT https://lians.example/v1/admin/governance/policies/acme \
  -H "X-Admin-Secret: $LIANS_ADMIN_SECRET" \
  -H "X-Admin-Actor: platform-admin@example.com" \
  -H "Content-Type: application/json" \
  -d '{
    "expected_version": 0,
    "allowed_processing_regions": ["us-east-1", "us-west-2"],
    "allowed_recorder_capture_modes": ["metadata_only", "hash_only"],
    "recorder_events_daily_limit": 5000000,
    "decision_records_daily_limit": 500000,
    "protected_actions_daily_limit": 500000,
    "memory_writes_daily_limit": 100000,
    "recalls_daily_limit": 1000000,
    "estimated_ingest_bytes_daily_limit": 107374182400
  }'
```

Lifecycle and inspection endpoints:

| Method | Endpoint | Mutation precondition | Purpose |
|---|---|---|---|
| `GET` | `/v1/admin/governance/policies` | None | List configured policies |
| `GET` | `/v1/admin/governance/policies/{namespace}` | None | Read one policy |
| `PUT` | `/v1/admin/governance/policies/{namespace}` | Body `expected_version` (`0` asserts first configuration) | Create or replace |
| `PUT` | `/v1/admin/governance/policies/{namespace}/status` | Body `expected_version` | Enable or disable |
| `DELETE` | `/v1/admin/governance/policies/{namespace}` | Query `expected_version` | Clear governance fields |
| `GET` | `/v1/admin/governance/status/{namespace}` | None | Effective state, usage, revision integrity |
| `GET` | `/v1/admin/governance/policies/{namespace}/revisions` | None | Immutable revision history |

Use `{"expected_version":3,"status":"disabled"}` or
`{"expected_version":4,"status":"active"}` with the status endpoint. Clear
with `DELETE .../policies/acme?expected_version=5`. A no-op status request does
not create a misleading revision. A stale precondition returns `409`; read the
current policy before issuing a new mutation. Policy rows and the first-create
boundary are locked in the database, so two replicas cannot both commit from
the same version.

## Tenant visibility

An authenticated namespace principal with `read` scope can inspect only its own
effective state:

```bash
curl https://lians.example/v1/governance/effective \
  -H "X-API-Key: $LIANS_API_KEY"
```

`GET /v1/governance/usage` returns just the current UTC day counters and
remaining capacity. PostgreSQL row-level security independently confines usage
and policy-revision rows to `app.current_namespace`; the admin sentinel is the
only cross-namespace path.

## Reservation semantics

Lians maintains a unique `(namespace, usage_date)` counter row. PostgreSQL
transactions lock that row before checking limits and increment all requested
counters together. An advisory policy-boundary lock also prevents a first
policy creation from racing a write when no policy row existed previously.

Reservations cover:

- Universal Recorder events, including native, MCP, A2A, and OTLP ingestion;
- Decision records created directly or derived from OTLP GenAI traces;
- successful Gate permit consumptions, before a mediator executes the protected action;
- memory writes, including writes approved from the admission queue;
- every successful recall path, including Redis-cache and keyed fast paths; and
- a deterministic UTF-8 estimate of externally submitted ingest bytes.

Recorder retries that pass schema normalization count toward the Recorder event
and byte quotas even when deduplicated. This prevents a retry flood from
bypassing tenant capacity controls. Invalid requests and write transactions
that roll back do not consume committed usage. A protected-action quota denial
rolls back the permit consumption, audit binding, and billing fact together, so
the single-use permit remains unspent but still retains its original expiry.
OTLP-derived decisions consume the
decision quota, but their bytes are not double-counted after the OTLP body has
already been reserved.

All periods use UTC and reset at `00:00:00Z`. A quota denial returns HTTP `429`
with `Retry-After` and a structured body containing the metric, limit, current
usage, requested amount, remaining capacity, reset time, and policy version.
Region and capture-mode denials return HTTP `403` with stable codes:

- `processing_region_not_allowed`
- `recorder_capture_mode_not_allowed`
- `namespace_daily_quota_exceeded`

## Rollout

1. Set and verify `DEPLOYMENT_REGION` on every API worker.
2. Apply through the release's exact Alembic head reported by platform readiness.
3. Confirm `/v1/platform/readiness` reports an explicit deployment region.
4. Create policies with intentionally high quotas; immediately set `disabled`
   through the status endpoint if automation must stage enforcement.
5. Compare `/v1/governance/usage` with telemetry before lowering quotas.
6. Exercise a quota denial and a disallowed-region deployment in staging.
7. Record the region naming contract and policy ownership in the customer runbook.

Platform readiness reports configuration state, not residency certification.
Operators still need deployment inventory, change control, and independent
evidence showing where the database, backups, queues, and external processors
actually run.
