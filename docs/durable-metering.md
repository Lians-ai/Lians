# Durable Stripe metering

Lians meters the work its decision-evidence and runtime-control infrastructure
protects. The product-native commercial dimensions are:

- `authoritative_decision`: one unit when an authoritative decision record, its
  normalized evidence links, and its authenticated `decision_recorded` audit
  binding commit. Body-bound API replays and deterministic OTLP re-ingestion do
  not create another unit.
- `protected_action`: one unit only when an enforcement mediator successfully
  consumes a request-bound, single-use Gate permit. Unknown, invalid, expired,
  mismatched, replayed, and transactionally failed redemptions create no unit.
  An active namespace `protected_actions_daily_limit` is reserved in the same
  transaction; quota denial leaves the permit unspent and creates no billable fact.

Memory writes and successful recalls remain separately metered compatibility
dimensions for existing memory-product contracts. They are not the canonical
measure of protected infrastructure work.

All four dimensions are recorded in PostgreSQL before reporting them to Stripe.
There is no in-process billing queue. Each usage row is added to the exact same
database transaction as its authoritative source mutation and audit evidence,
so the source fact and billing obligation commit or roll back together. A Stripe
outage can delay delivery but cannot erase a committed billable fact.

## Delivery contract

- `metering_events` stores the customer snapshot, whole-number quantity,
  deterministic provider identifier, lease, retry projection, and terminal
  state. Customer changes affect only future usage.
- Decision UUIDs and Gate permit UUIDs provide stable source identities. Lians
  stores only their SHA-256 source hash in the metering row. API idempotency,
  OTLP advisory locks, and Gate permit row locks serialize authoritative races;
  metering uniqueness is the final fence against another committed charge.
- `metering_attempt_records` is an append-only start/result ledger. It stores no
  response body, API key, customer ID, or source identifier.
- Every replica may run the worker. Claims use row locks with `SKIP LOCKED` on
  PostgreSQL, expired leases are recoverable, and no database connection is
  held during the Stripe request.
- The same deterministic identifier is sent as both Stripe's meter-event
  `identifier` and HTTP idempotency key. Stripe documents identifier uniqueness
  enforcement for a rolling period of at least 24 hours. Lians stops automatic
  retries at 23 hours and dead-letters the ambiguous event instead of risking a
  duplicate charge after that provider window.
- Provider retries use bounded deterministic jitter. Permanent 4xx responses,
  attempt exhaustion, events older than the configured acceptance budget, and
  expired idempotency windows enter `dead_letter`.
- PostgreSQL RLS isolates both tables by namespace. Identity fields cannot be
  changed, attempts cannot be updated/deleted/truncated, delivered rows are
  immutable, and the runtime role has no delete/truncate privilege.

Stripe processes accepted meter events asynchronously. Subscribe a separate,
durable Stripe event destination to `v1.billing.meter.error_report_triggered`
and `v1.billing.meter.no_meter_found`; an HTTP 2xx only confirms ingestion, not
the later aggregation result. See the official [meter-event API reference](https://docs.stripe.com/api/billing/meter-event/create)
and [usage recording guide](https://docs.stripe.com/billing/subscriptions/usage-based/recording-usage-api).

## Configuration

`STRIPE_API_KEY` enables provider delivery. Removing it pauses delivery without
dropping staged usage. In production, a configured key requires
`STRIPE_METER_WORKER_ENABLED=true`, a live `sk_live_` or restricted `rk_live_`
key, the `lians-platform[billing]` dependency, and
`STRIPE_METER_ASYNC_ERROR_DESTINATION_CONFIGURED=true`. The last setting is an
operator attestation: set it only after the two Stripe thin events above feed a
durable, monitored destination.

The lease must exceed `STRIPE_METER_PROVIDER_TIMEOUT_SECONDS` by at least 15
seconds. The maximum retry horizon must stay inside
`STRIPE_METER_IDEMPOTENCY_WINDOW_SECONDS`. Startup fails closed when these
relationships or meter-event names are invalid.

Configure four distinct Stripe event names:

| Environment variable | Default | Contract |
|---|---|---|
| `STRIPE_METER_DECISION_EVENT` | `lians_authoritative_decision` | Product-native authoritative decision unit |
| `STRIPE_METER_PROTECTED_ACTION_EVENT` | `lians_protected_action` | Product-native successful Gate permit consumption |
| `STRIPE_METER_WRITE_EVENT` | `agentmem_memory_write` | Compatibility memory-write unit |
| `STRIPE_METER_RECALL_EVENT` | `agentmem_memory_recall` | Compatibility successful-recall unit |

Startup rejects blank, malformed, overlong, or duplicate event names even when
provider delivery is paused. Tenant billing remains opt-in through the namespace
Stripe customer mapping.

Read the current mapping, then set or clear a namespace customer with the exact
`updated_at` token returned by the read:

```text
GET /v1/admin/billing/{namespace}
X-Admin-Secret: ...

PUT /v1/admin/billing/{namespace}
X-Admin-Secret: ...
{"expected_updated_at":"2026-08-02T12:34:56.789Z","stripe_customer_id":"cus_..."}
```

Use `expected_updated_at: null` only when the GET response has `updated_at:
null`, which asserts that the shared namespace-policy row does not exist. A
concurrent retention, billing, or governance change produces `409`; fetch the
authoritative state and decide again instead of blindly retrying.

Clearing a customer stops staging future usage. It does not cancel already
committed usage obligations.

Before enabling live delivery:

1. Create Stripe meters whose event names exactly match the four configured
   Lians event names and whose customer/value payload keys match
   `stripe_customer_id` and `value`.
2. Exercise the complete flow in a non-production deployment with a Stripe test
   key, including one forced retry and one asynchronous error report.
3. Configure and monitor the two thin-event destinations, then set the explicit
   destination attestation.
4. Apply the database migration before rolling out API replicas, install a live
   or restricted live key through the secret manager, and update the workload's
   secret rollout revision.
5. Confirm every replica reports a healthy worker, a zero dead-letter count,
   and a bounded oldest-due age before opening billable traffic.

## Operations and reconciliation

The private admin surface provides:

- `GET /v1/admin/billing-metering/status` for worker freshness, bounded queue
  counts, dead letters, delivery/configuration posture, and the oldest due
  timestamp. Add `?namespace=...` to scope database counts.
- `GET /v1/admin/billing-metering/events?status=dead_letter` for secret-free
  event projections, stable provider identifiers, and error digests.
- `POST /v1/admin/billing-metering/events/{id}/replay` only after Stripe has
  confirmed the stable provider identifier was not accepted. The request must
  include an explicit assertion and an opaque incident/ticket reference:

  ```json
  {
    "reconciliation": "provider_confirmed_not_accepted",
    "reconciliation_reference": "INC-12345"
  }
  ```

  Replay preserves all prior attempt records, hashes the reference into the
  audit chain, increases the absolute attempt limit, and starts a new bounded
  idempotency-safety epoch.

Worker timestamps and health on the status endpoint describe the replica that
served the admin request; durable event counts are database-wide. Use the
per-instance Prometheus worker gauges to evaluate every replica. The worker
emits a bounded in-process heartbeat while a provider batch is active, so slow
but timeout-bounded delivery does not look like a stalled database poll.

Prometheus exports fixed-cardinality metrics:

- `lians_metering_events{status=...}`
- `lians_metering_delivery_attempts_total{outcome=...}`
- `lians_metering_oldest_due_age_seconds`
- `lians_metering_delivery_enabled`
- `lians_metering_worker_healthy`

Alert when the worker readiness check is stale, any dead letter exists, retry
volume increases, or oldest-due age exceeds the delivery SLO. Do not blindly
replay `idempotency_window_expired` after an outage: first determine whether
Stripe accepted the stable identifier. If it was accepted, mark/reconcile the
local obligation through an approved database procedure rather than sending a
second meter event; if it was not accepted, use the audited replay endpoint.

Back up both metering tables with the authoritative database. Restore them
before enabling the worker, retain the API key outside database backups, and
verify the dead-letter queue before reopening billable traffic.

For key rotation, update the secret and force a bounded replica rollout; do not
delete or rewrite pending rows. Complete rotation well inside the 23-hour
automatic-retry window. A 401/403 dead letter requires confirmation that Stripe
did not accept the event before replay. Events beyond the configured 34-day
provider-age budget cannot be repaired through the meter-event API; reconcile
them through the approved billing/invoice adjustment procedure and retain the
local ledger as evidence.
