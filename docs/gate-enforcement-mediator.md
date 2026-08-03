# Gate enforcement mediator

`lians-gate-mediator` is Lians' supported enforcement point for HTTP side
effects. It is a separate process, credential, network identity, and deployment;
it is never mounted into the Lians public or admin API. The evaluator cannot
reach a protected provider directly, and the mediator cannot dispatch without
redeeming a short-lived single-use Gate permit as its own canonical principal.

## Security boundary

The mediator owns four things that callers cannot choose:

- exact, versioned route IDs with one HTTPS URL, method, action, target, content
  type, reviewed request contract, fixed headers, and byte/time limits;
- the provider credential and its non-secret provider/account binding reference;
- the canonical request-binding implementation;
- the only provider IAM and egress path able to perform the protected action.

Callers provide only a configured route ID, a decision ID during preparation,
the exact request body bytes, and then the permit capability and bound claims. They
cannot provide a URL, path, query, method, action, target, `Host`, authorization,
proxy, forwarding, cookie, or hop-by-hop header. The mediator builds upstream
headers from scratch, injects its server-held credential, and never forwards an
ingress header.

Provider IAM must reject the evaluator credential and direct network paths.
Without that external boundary, any HTTP mediator can be bypassed.

## Prepare, evaluate, execute

The service is stateless, so replicas do not share prepared bodies:

1. `POST /v1/prepare/{route_id}` receives the raw, bounded body with exact
   `Content-Type`, `X-Lians-Decision-Id`, and
   `X-Lians-Mediator-Client-Token` headers.
2. It returns server-derived `action`, `target_ref`,
   `enforcement_principal_id`, and `execution_request_hash`. The caller sends
   those exact values to `POST /v1/control/gate/evaluate`.
3. `POST /v1/execute/{route_id}` receives the same raw body, caller token, and
   the issued permit capability and bound claims in fixed `X-Lians-Permit-*` headers.
4. The mediator recomputes the binding, checks every presented permit claim and
   expiry locally, acquires capacity, resolves and pins the provider address,
   loads rotating credentials and TLS material, and prepares the exact request.
5. It redeems the permit once through the pinned Gate transport. Only an exact
   HTTP `201` with matching consumption claims permits one provider write.
6. It sends the already-prepared provider request exactly once. Redirects and
   automatic retries do not exist in either transport.

All mediator responses and both Gate capability endpoints carry
`Cache-Control: no-store` and `Pragma: no-cache`.

### Execute headers

Send the fields from `execution_permit` without transforming their values:

```text
X-Lians-Permit-Id
X-Lians-Permit-Enforcement-Principal
X-Lians-Permit-Action
X-Lians-Permit-Target-Ref
X-Lians-Permit-Decision-Id
X-Lians-Permit-Request-Hash
X-Lians-Permit-Issued-At
X-Lians-Permit-Expires-At
X-Lians-Permit-Token
```

Redact the caller token and permit token in ingress proxies, APM, exception
capture, and service-mesh logs. Uvicorn access logging is disabled by the CLI.
The mediator never logs request bodies, provider credentials, Gate keys, caller
tokens, or permit tokens.

The supported CLI always serves HTTPS. Configure a dedicated server certificate
and key; optionally require a client certificate from a configured CA in
addition to the mediator caller token. TLS termination must never downgrade the
evaluator-to-mediator hop to an untrusted clear-text network.

## Canonical request binding

`lians-http-execution-v1` uses sorted, compact ASCII JSON and SHA-256. The
committed envelope includes the complete non-secret immutable route manifest,
route-config digest, action, target, decision, exact URL and method, exact body
length and body SHA-256, fixed/security-relevant headers, content type,
credential binding reference, and deterministic idempotency header.

The rotating provider credential value is intentionally excluded because an
evaluator must not know it; the stable provider/account authority is included
as `credential.binding_ref`. The permit-ID audit header is non-authorizing and
is created after evaluation, so its value is also excluded. `/v1/prepare` is the
only supported compatibility bridge—callers should not reimplement this
serialization.

JSON routes require a reviewed `request_contract_ref` and an explicit top-level
field allowlist. The mediator rejects duplicate keys, non-finite constants,
unexpected or missing fields, excessive nesting, and excessive node counts,
then forwards the original bytes unchanged. `target_binding` is fixed to
`fixed-route-authority-v1`: `target_ref` must name the provider account/authority
that the exact URL, provider IAM, and fixed headers constrain. If any body field
can choose a different tenant, account, region, endpoint, operation, or protected
authority, it must not be admitted by that route contract; create a separate
fixed route or a purpose-built mediator adapter instead.

If `idempotency_header_name` is configured, its value is deterministically
derived from route ID plus decision ID. Provider idempotency remains essential:
a connection can fail after the provider accepted a request. Once a permit is
consumed, any timeout, oversized/invalid response, or transport error is
reported as outcome unknown and is never retried. Reconcile using the permit ID,
Gate consumption ID/hash, and the provider's idempotency record.

## DNS, SSRF, and TLS

URLs must be exact canonical HTTPS URLs with DNS hostnames. User information,
fragments, IP literals, path traversal, encoded dots/separators, ambiguous
percent escapes, and non-ASCII/trailing-dot hosts are rejected. At startup and
before each dispatch, every A/AAAA answer is checked. Mixed answers fail as a
unit; upstream loopback, private, link-local, metadata, carrier-grade NAT,
multicast, unspecified, reserved, and non-global addresses are rejected.

The selected validated IP—not the hostname—is passed to `socket.connect`.
TLS still uses the configured hostname for SNI and certificate verification,
requires the system or configured CA, and permits TLS 1.2 or newer. This closes
the usual validate-then-client-resolves rebinding gap for that request. DNS and
route configuration remain trusted inputs for selecting the initial permitted
public address, so production egress policy must independently restrict each
mediator to its provider IPs or an authenticated FQDN-aware gateway.

Each HTTP attempt has one absolute watchdog across numeric-IP connect, TLS
handshake, request write, response headers, and bounded response body. A timeout
closes the live socket; it never starts a second attempt.

Gate control-plane connections use the same IP-pinned TLS transport. Private
Gate addresses are accepted only inside an explicit `gate.allowed_ip_cidrs`;
loopback, link-local, metadata, multicast, unspecified, and reserved addresses
remain forbidden.

## Identity and rotation

Startup and readiness call `/v1/identity/whoami` and require exact equality for:

- `expected_mediator_principal_id`;
- `expected_namespace`;
- a non-empty `expected_barrier_group` for the dedicated mediator scope;
- API-key authentication for the dedicated mediator credential;
- the `write` scope.

Successful readiness identity checks are cached for the bounded
`identity_recheck_seconds` interval (60 seconds by default) so health probes do
not consume the shared mediator key's entire rate-limit budget.

Use one dedicated, barrier-scoped mediator identity per protected provider or
authority domain. One shared API key also shares the server's per-key rate
limit, so capacity-plan or shard identities deliberately.

Caller, metrics, Gate, provider, CA, and mTLS material are referenced by absolute files.
Header secrets are re-read for every call; outbound TLS contexts are rebuilt for
every prepared dispatch, so projected-secret rotation does not require storing
old bytes in application state. Rotate the inbound server certificate with a
rolling restart. Keep old and new provider credentials valid during a rolling
rotation. A change of provider account or authority requires a new
`credential.binding_ref` and new versioned route ID.

A Lians mediator API-key rotation changes the canonical principal. Do not
overwrite that projected key in place while pods still pin the old principal.
Create a versioned Secret and config revision together, briefly authorize both
principals in a new Gate policy version, roll new pods, then activate a new-only
policy and revoke the old key. Old pods must retain the old Secret until drained.

Changing a route in place is unsupported. A route ID must end in its exact
`.route_version` suffix. Create a new versioned route ID and
retain the old route on every replica for at least the 300-second permit ceiling
plus rollout skew. The config hash makes a mixed-version request fail closed;
retention keeps a valid old permit operable.

## Operations and metrics

The Lians API exports two fixed-cardinality, non-tenant Gate counters through
its existing authenticated `/metrics` endpoint:

- `lians_gate_evaluations_total{disposition}` with only `allow`, `deny`, or
  `review`;
- `lians_gate_permit_events_total{outcome}` with only `issued`, `consumed`,
  `rejected`, `expired`, `replayed`, or `mismatched`.

The consume API still returns the identical non-oracular `403` for every
invalid capability. A more specific aggregate outcome is emitted only when a
stored permit's secret digest is valid; unknown IDs and bad tokens remain
`rejected`. Metrics never label by namespace, tenant, principal, barrier, route,
action, target, decision, evaluation, permit, consumption, provider, or token.

The standalone mediator exposes a separate bearer-authenticated `/metrics`
registry containing only:

- `lians_gate_mediator_upstream_requests_total{outcome}`;
- `lians_gate_mediator_upstream_duration_seconds{outcome}`.

Mediator outcomes are bounded to `success`, `client_error`, `server_error`, and
`outcome_unknown`. Latency begins immediately before the single provider
dispatch, after authoritative permit consumption. The scrape bearer must be
different from the evaluator caller token, is re-read for rotation, and is
never logged. Access logs remain disabled and every scrape response is
`no-store`.

Install the supplied rules and route these alerts:

- replay or mismatch: page security/on-call and investigate version skew or
  credential misuse;
- provider outcome unknown: page and reconcile by permit ID, consumption
  hash, and provider idempotency record; never retry automatically;
- sustained rejection/expiry/non-allow rate: investigate policy rollout,
  clocks, queueing, receipt trust, and approval availability;
- upstream error/latency or mediator scrape failure: investigate provider,
  TLS, DNS/egress, and capacity before changing permit TTLs.

These aggregate metrics are operational signals, not the audit record. Use the
append-only Gate evaluation, grant, consumption, and mediator correlation data
for incident reconstruction.

## Deployment

See [`deploy/gate-mediator`](../deploy/gate-mediator/README.md). The Kubernetes
example has no Ingress, accepts traffic only from evaluator-labelled pods, runs
non-root with a read-only filesystem and no service-account token, and has no
provider egress until the operator adds one exact `ipBlock` or an FQDN-aware
policy. Vanilla Kubernetes `NetworkPolicy` cannot enforce DNS names.

Never run this process in the Lians API pod, reuse an evaluator/API credential,
or expose it through the public Lians Ingress.
