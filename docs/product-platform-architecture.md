# Lians product platform architecture

Lians ships one evidence-grade memory engine through two deliberately different
product experiences:

- **Developer Studio** is local-first, fast to start, and optimized for
  inspection, debugging, evaluation, and migration from a laptop to hosted
  infrastructure.
- **Enterprise Control Plane** adds collaborative review, hierarchical scopes,
  policy administration, identity federation, evidence export, and deployment
  controls without changing the engine or public memory contract.

The two surfaces share the same versioned API. Enterprise features never use a
separate memory store or a second interpretation of history.

## Product contract

The platform is organized around six stable resources:

1. **Memory** — encrypted content with bitemporal validity, provenance,
   importance, admission findings, and an append-only control history.
2. **Scope** — a hierarchy of session, user, agent, project, team, organization,
   and tenant boundaries. Effective access is always evaluated server-side.
3. **Policy profile** — versioned admission, retention, redaction, importance,
   recall, and latency rules suitable for a named workload.
4. **Evaluation** — a customer-owned dataset, run, metric set, and signed result
   that can be enforced in CI.
5. **Decision envelope** — the immutable evidence boundary for a consequential
   action, including what was included and what was excluded.
6. **Connector** — a permission-aware external source whose grants, checkpoints,
   revocations, and derived memories remain attributable to the source version.

## History-preserving controls

Studio controls never edit memory content in place:

- `confirm`, `pin`, and `demote` change ranking policy metadata and append a
  tamper-evident audit event;
- `retire` closes the memory validity interval and removes it from the live read
  model;
- `replace` creates a corrected memory, links the old record to it, and closes
  the old validity interval.

This contract allows an individual developer to fix an assistant's memory while
preserving the same reconstruction guarantees required by a regulated reviewer.

## Delivery sequence

1. Studio inventory, explanations, controls, latency, and evidence navigation.
2. One-command local runtime, TypeScript local parity, and productized evals.
3. Versioned policy profiles, asynchronous enrichment, and explicit latency
   budgets.
4. Hierarchical scopes, user-facing memory controls, and permission-aware source
   connectors.
5. Hosted projects and environments, enterprise identity, policy administration,
   residency, key management, SIEM, and review workflows.
6. Signed evidence exports, source-change blast-radius analysis, restore proof,
   and operational release gates.

Every phase must preserve temporal soundness, audit immutability, erasure
completeness, barrier isolation, point-in-time correctness, and backtest purity.

## Implemented platform surface

The delivery sequence above is now represented in the repository by stable,
tested contracts:

- Studio inventory and controls: `GET /v1/memories` and
  `POST /v1/memories/{id}/control`;
- policy catalog and assignment: `GET /v1/policy-profiles` and
  `PUT /v1/agents/{agent_id}/policy`;
- scoped memory: `scope` on capture, recall, context, message ingestion, and
  connector configuration;
- latency paths: `write_mode=fast` and `POST /v1/recall/stream`;
- workspace and connectors: `/v1/workspace`, `/v1/connectors`, and idempotent
  connector event ingestion;
- enterprise posture: `GET /v1/control-plane/overview`, existing compliance
  reports, evidence packs, blast-radius analysis, audit verification, retention,
  legal hold, SIEM jobs, information barriers, and WORM posture.

External provider gateways retain their own OAuth credentials. Lians connector
configuration explicitly rejects tokens, passwords, API keys, and secrets; it
accepts normalized, attributable events through the workspace API instead.
