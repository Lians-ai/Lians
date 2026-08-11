# Lians product platform guide

Lians uses one bitemporal memory and evidence engine for individual developers,
product teams, and regulated enterprises. Local and hosted deployments expose
the same contracts, so moving from a laptop to a managed workspace does not
change memory semantics or discard history.

## Local development and evaluation

Install the complete local runtime and start a persistent API:

```bash
pip install 'lians-sdk[local]'
lians doctor
lians dev --api-key local-development-key
```

The server prints its URL, key, and SQLite location. Connect Python or
TypeScript to `http://127.0.0.1:8000`; the Studio accepts the same URL and key.

Run the built-in sample evaluation or provide a compatible JSON dataset:

```bash
lians eval
lians eval ./memory-eval.json \
  --min-recall 0.90 \
  --max-stale-leak-rate 0 \
  --max-p95-latency-ms 250 \
  --output .lians/evaluation.json
```

The report schema is `lians.memory-eval.v1`. It contains per-question evidence,
category scores, receipt hashes, latency percentiles, deadline misses, and token
estimates. Threshold failures use exit code 1.

## Capture policy and durable preferences

Every agent resolves one immutable built-in profile plus a small set of audited
overrides. Assignments use an expected revision so two administrators cannot
silently overwrite each other.

```http
PUT /v1/agents/support-agent/policy
X-API-Key: ...
Content-Type: application/json

{
  "profile": "support_agent",
  "actor": "risk-admin",
  "expected_revision": 2,
  "overrides": {"retention_days": 90}
}
```

Durable personal preferences, accessibility needs, explicit remember requests,
policies, and stable personal facts receive a deterministic importance floor.
Transient acknowledgements and short-lived questions are classified separately.
The source content stays authoritative and the classification is stored as a
replaceable, versioned projection.

## Hierarchical scope

Use kind/id pairs:

```text
org/acme/team/platform/project/api
```

A recall at that path can include `org/acme` and
`org/acme/team/platform` when `include_parent_scopes=true`; it cannot read
`org/acme/team/platform/project/mobile`. Namespace isolation and information
barriers remain additional, independent controls. Omitting `scope` preserves
the existing namespace-wide behavior for backward compatibility.

## Latency-sensitive paths

`write_mode=inline` performs embedding before returning. `write_mode=fast`
performs admission, encryption, temporal persistence, audit logging, live-fact
projection, and cache invalidation synchronously, then queues embedding by
memory ID. Plaintext never appears in the durable job payload.

For progressive retrieval, `POST /v1/recall/stream` emits Server-Sent Events:

1. `started` immediately;
2. `snapshot` with bounded fast recall when deep or reconstruct mode was asked;
3. `final` with the requested result and content-addressed receipt;
4. `done` with the final receipt hash.

## Workspaces and connectors

A namespace is the security boundary of a workspace. Workspace metadata adds a
display name, plan, region label, settings, and bounded resource counts without
weakening that boundary.

Connector records support direct SDK, GitHub, Slack, Notion, Google Drive, and
generic webhook gateways. Provider apps keep OAuth credentials in their own
secret manager and push normalized events to:

```http
POST /v1/connectors/{connector_id}/events
```

`external_id` is idempotent per connector. Retried deliveries return the
original memory ID. Each memory retains connector ID, kind, external ID, source,
scope, event time, and the complete memory/evidence audit history.

## Enterprise control plane

An unbarriered key with admin scope can call:

```http
GET /v1/control-plane/overview?verify_audit=true
```

The response consolidates memory lifecycle counts, open conflicts, pending
admissions, decision and evidence coverage, replayable and human-review rates,
connector state, pending/dead durable jobs, retention, legal hold, deployment
security posture, and audit-chain verification. It complements the detailed
compliance report, WORM posture, audit export, erasure certificates, evidence
packs, and source-change blast-radius APIs.

## Human memory controls

Controls never rewrite history:

- confirm and pin retain the record and strengthen its human provenance;
- demote changes ranking policy without deleting evidence;
- retire closes validity and removes the item from current recall;
- replace creates a corrected version and links the original into its lineage.

All controls are audit-chained. Erasure remains a separate privacy operation
that destroys subject encryption material while preserving non-content custody
evidence.
