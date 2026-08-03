# Durable enterprise integrations

Lians can deliver audit, Gate, investigation, remediation, GRC, ticketing, SIEM,
and billing events through a transactional outbox. The source mutation, audit
event, encrypted outbox event, and initial delivery rows commit together. A
restart cannot lose an accepted delivery, and a rollback cannot publish an
event that never became authoritative.

The delivery guarantee is **at least once**, not exactly once. Every request
includes a stable `Idempotency-Key`, and retries or an operator replay retain
that key. Receivers must persist it and return their prior successful result
when they see it again.

## Reliability model

1. `chain_log()` appends a tamper-evident audit event.
2. If an active destination pattern matches `audit.<operation>`, Lians creates
   an encrypted outbox event and one delivery per destination in the same
   database transaction.
3. Workers claim due rows with a time-bounded lease. PostgreSQL workers use
   `FOR UPDATE SKIP LOCKED`, so multiple API replicas can drain the same queue.
4. A 2xx response closes the run. Timeouts, network errors, 408, 425, 429, and
   5xx responses retry with capped exponential backoff and deterministic jitter.
5. Non-retryable 4xx responses and exhausted retries enter `dead_letter`.
6. An admin can replay a dead-lettered or cancelled run. Replay creates a new
   linked run and preserves the receiver idempotency key and immutable attempt
   history.

Expired leases are reclaimable after a worker crash. This can produce a
duplicate request if the first worker reached the receiver but died before it
recorded success, which is why receiver-side idempotency is mandatory.

## Destination types and event patterns

All destination types use an authenticated HTTPS `POST`; the type identifies
operator intent and inventory rather than selecting an undocumented vendor
payload:

- `siem`
- `grc`
- `ticketing`
- `billing`
- `generic_http`

Patterns are exact event names, `*`, or a trailing namespace wildcard such as
`audit.control.*`. Every audit-chain operation is exposed as
`audit.<operation>`. Gate and case routes already use the audit chain, so their
operations are transactionally eligible without a second application hook.

For a purpose-built domain event, call `enqueue_integration_event()` with the
same `AsyncSession` as the source write and let the caller commit both. The
admin API also exposes `POST /v1/integrations/events` for bounded custom events.

```python
from lians.integration_service import enqueue_integration_event

await enqueue_integration_event(
    db,
    namespace=namespace,
    barrier_group=barrier_group,
    event_type="billing.usage_window_closed",
    aggregate_type="usage_window",
    aggregate_id=window_id,
    idempotency_key=f"usage-window:{window_id}",
    payload={"window_id": window_id, "units": units},
)
# Commit the source mutation and outbox rows together.
await db.commit()
```

## Secret and payload handling

Destination URLs (including secret path components), bearer tokens, basic
credentials, API-key headers, custom header values, and signing secrets are
serialized together and sealed with
AES-256-GCM. The envelope key is purpose-separated from other Lians data and is
derived from the configured KMS-backed master key. Plaintext secrets are
returned only during creation or rotation and never appear in destination read
responses, audit payloads, attempt records, or logs. Read responses expose only
the URL origin and a SHA-256 fingerprint for configuration comparison.

Outbox payloads are also AES-256-GCM sealed at rest and bound to the namespace
and event ID. List/read APIs return hashes and metadata by default; an admin
must request `include_payload=true` to decrypt content. Automatic audit events
contain references and hashes by default. Set
`INTEGRATION_INCLUDE_AUDIT_PAYLOAD=true` only after reviewing the destination's
data-handling boundary.

Response bodies are never stored. Lians hashes only a bounded prefix for
correlation. Error records contain stable error codes and SHA-256 digests, not
exception messages that might leak credentials or remote content.

## Delivery authentication

Each request includes:

- `Idempotency-Key`: stable across retry and replay
- `X-Lians-Event-ID`: immutable outbox event ID
- `X-Lians-Delivery-ID`: delivery-run ID
- `X-Lians-Signature`: `t=<unix>,v1=<hex HMAC-SHA256>`

The signed input is the ASCII timestamp, a literal `.`, and the exact request
body. Reject timestamps outside a short tolerance and compare signatures with
a constant-time function.

```python
import hashlib
import hmac

def verify(secret: str, signature_header: str, body: bytes) -> bool:
    fields = dict(part.split("=", 1) for part in signature_header.split(","))
    signed = fields["t"].encode("ascii") + b"." + body
    expected = hmac.new(secret.encode(), signed, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, fields["v1"])
```

The default `cloudevents` profile follows the JSON shape in
`specs/integration-outbox/v1/envelope.schema.json`. The `raw` profile sends the
decrypted domain payload directly but retains the same headers and integrity
controls.

## Network controls

Destination URLs use HTTPS, reject embedded credentials, query-string tokens,
fragments, redirects, proxy environment variables, and blocked address ranges.
DNS is checked when a destination is saved and immediately before each send.
At send time Lians connects to the validated IP itself while retaining the
configured hostname for HTTP `Host`, TLS SNI, and certificate verification;
DNS cannot change the socket target between validation and connect. Every DNS
answer must be allowed, and loopback, link-local, metadata, multicast,
unspecified, and reserved ranges remain blocked even when private-network
delivery is enabled.

Set `INTEGRATION_ALLOW_PRIVATE_NETWORK=true` only for an explicitly routed
private sink. Application checks complement, but do not replace, a Kubernetes
NetworkPolicy, egress firewall, service mesh policy, or dedicated outbound
proxy. Plain HTTP is development-only and is rejected by production startup
validation.

`AIRGAP_MODE=true` prevents the delivery worker from starting. Events remain
queued so an operator can export or deliver them after an approved posture
change.

## Administration and operations

Namespace admins use `/v1/integrations` to:

- create, list, update, rotate, test, and soft-revoke destinations;
- inspect encrypted event metadata and delivery state;
- read immutable attempt history;
- replay terminal delivery runs; and
- inspect `/v1/integrations/readiness` for queue and dead-letter state.

Destination changes use an `expected_version` to prevent lost updates. Revoking
a destination cancels pending/retry rows and prevents new fan-out. A request
already leased or in flight may still reach the receiver; rotate credentials or
block network access for immediate emergency containment.

Custom event enqueue and destination test are the two client-facing integration
mutations covered by the shared transactional `Idempotency-Key` ledger. Reusing
the same key with the exact authenticated request returns the original event
and delivery identifiers; changing the body, destination, principal, credential,
or barrier returns `409`.

Destination creation/secret rotation responses disclose one-time signing
material and are non-cacheable. Those operations, destination revocation, and
delivery replay reject `Idempotency-Key`; reconcile destination fingerprints,
versions, and linked delivery runs after an ambiguous response. Delivery replay
preserves the receiver idempotency key, but the administrative replay request
itself does not have a replayable response contract.

Alert on any sustained `dead_letter` count, a growing retry queue, or an oldest
due timestamp that exceeds the delivery SLO. Keep the worker lease greater than
the maximum destination timeout. The production validator requires at least
130 seconds; the default lease is 180 seconds.

## Upgrade and legacy webhook transition

Apply Alembic revision `0033_integration_outbox` before starting workers. It
adds namespace and information-barrier RLS, append-only outbox/attempt guards,
cross-boundary delivery triggers, and replay-chain constraints.

The older `/v1/webhooks` and `SIEM_URL` paths are retired compatibility paths.
When explicitly enabled outside production, `/v1/webhooks` is protected by the
namespace-wide `LEGACY_WEBHOOK_MAX_ENDPOINTS_PER_NAMESPACE` ceiling. Creation
is serialized, and list/fan-out operations refuse an over-cap legacy database
rather than returning or delivering an unmarked partial result. Delivery error
records contain bounded status codes, never response bodies, exception text, or
destination URLs.
They use in-process best-effort delivery, are disabled by default, and production
startup rejects either path when enabled/configured. Domain events emitted by
maintained write paths are offered transactionally to matching durable
destinations. Create an equivalent destination, verify a queued test delivery,
move receiver idempotency to the new header, and leave the legacy paths disabled.

Readiness reports configuration and queue state; it does not prove that a
downstream vendor processed, retained, or reconciled an accepted payload.
