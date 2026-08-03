# Lians integration delivery envelope v1

Durable destinations receive either the CloudEvents-compatible JSON envelope
defined by `envelope.schema.json` or a configured raw domain payload. Transport
headers carry a stable receiver idempotency key, immutable event/delivery IDs,
and an HMAC-SHA256 signature.

Delivery is at least once. A receiver must deduplicate the `Idempotency-Key`
across retries and operator replays.

See `docs/integration-outbox.md` for signing, retry, security, and operational
requirements.
