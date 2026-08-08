# Lians — guide for AI coding assistants

This file gives coding agents the repository-specific context needed to change
Lians without weakening its evidence, security, or tenancy boundaries.

## What Lians is

Lians is provider-neutral memory, governed-improvement, decision-evidence, and
runtime-control infrastructure for consequential AI systems. It keeps current
and historical agent knowledge, compiles exact-token-budgeted context, evaluates
candidate agent versions against protected quality and safety constraints, and
records what an agent observed, was permitted to use, decided, and did. It also
reconstructs the recorded historical boundary, finds decisions affected by later
source, policy, model, tool, or permission changes, and mediates protected actions
through evidence-aware policy gates.

The primary product primitives are:

- **Universal Recorder** — normalizes native Lians, OpenTelemetry GenAI, MCP,
  and A2A events while preserving capture gaps and authenticated provenance.
- **Decision Receipt v0.1** — an open, independently verifiable evidence object
  with deterministic canonicalization, hashes, signatures, fixtures, and a CLI.
- **Evidence graph and impact engine** — separates available, retrieved, cited,
  policy-evaluated, tool-used, and outcome evidence and supports durable,
  snapshot-bounded blast-radius assessments.
- **Lians Gate** — evaluates policy and evidence requirements and issues
  mediator-bound, single-use execution permits for protected actions.
- **Lians Investigator** — reconstructs decisions, manages cases and remediation,
  and records human-attested closure.
- **Bitemporal memory** — preserves event time and system-recorded time, suppresses
  superseded facts, and supports exact point-in-time recall boundaries.
- **Governed improvement plane** — binds immutable agent versions to exact-token
  context/tool optimization, repeated evaluations, protected constraints, signed
  attestations, staged release evidence, outcomes, drift, and review-only proposals.

Lians proves the integrity and completeness of what it captured. Quality, token,
latency, and cost improvements are workload-specific measured outcomes, never
universal installation guarantees. Lians also never claims access to hidden model
reasoning, universal deterministic replay, or regulatory compliance merely
because the software is installed.

## Repository layout

```text
agentmem/                         production server and canonical Python package
  src/lians/                      FastAPI application and platform services
    api/                          public and administrative route handlers
    gate_mediator/                separately deployed execution mediator
  alembic/versions/               immutable, ordered database migrations
  tests/                          server and contract tests
  sdk/python/lians/               public `lians-sdk` Python package
  sdk/typescript/src/             public `@lians-ai/lians` package
sdk/python/src/lians/             source-only compatibility/conformance client
specs/                            open Recorder, Receipt, and control contracts
deploy/helm/lians/                production Helm chart
k8s/                              reference Kubernetes and monitoring overlays
ops/                              Prometheus rules and operational assets
docs/                             architecture, security, and operator runbooks
```

The root distribution is named `lians-platform`, but the production server's
top-level import is `lians`. Server code, tests, Alembic, scripts, and operations
must never prefix that package with the source-directory module name. The public
`lians-sdk` is installed in client
environments and intentionally cannot be co-installed with the server package.
Its optional local-mode engine is vendored behind a private compatibility bridge.

## Local commands

```bash
cd agentmem
python -m pip install -e ".[dev]"
pytest -v
pytest -v -k "not pgvector"
pytest tests/test_memory_service.py
```

The repository also has release, schema, canonical-import, OpenAPI, Helm,
container, supply-chain, and PostgreSQL contract checks. Run validation in
proportion to the change and follow any active task-specific sequencing request.
Do not silently replace production-grade provider paths with deterministic local
test stubs.

## Non-negotiable architecture boundaries

1. **Canonical server identity:** import `lians` directly; a source-directory
   module prefix is allowed only inside the explicitly isolated SDK-vendoring
   bridge.
2. **Database tenancy:** PostgreSQL RLS and transaction-local namespace/barrier
   context are authoritative. Application filters supplement but do not replace
   them. Background enumeration uses the internal admin sentinel only.
3. **Audit append boundary:** consequential mutations append through the
   serialized database boundary in the same transaction. Do not update or delete
   immutable evidence, receipt, audit, attempt-ledger, or revision records.
4. **Transactional obligations:** audit events, integration deliveries, metering
   events, cache fences, governance reservations, and idempotency results must be
   committed atomically with the mutation that creates them where their contract
   requires it.
5. **Temporal truth:** PostgreSQL sequences are allocation ordered, not commit
   ordered. Snapshot claims that depend on a monotonic watermark must take the
   matching registration fence.
6. **Identity:** persisted attribution comes from authenticated principals and
   workload credentials. Caller-supplied agent labels are explicitly unverified.
7. **Secrets and privacy:** use encrypted secret storage and configured key
   providers. Metrics, logs, receipts, and integration projections must not expose
   tenant-controlled identifiers, secrets, raw payloads, or unbounded labels.
8. **Network security:** production PostgreSQL uses hostname-verifying TLS;
   production Redis uses authenticated `rediss://` with peer verification; egress
   adapters pin validated destinations and reject unsafe redirects/rebinding.
9. **Migrations:** add a new Alembic revision; never edit an already released
   migration. Keep a single graph head and update `lians.version` plus release
   contracts when the head changes.
10. **Trust language:** distinguish observed, reachable, estimated, reconstructed,
    simulated, verified, and legacy-unverified states in APIs and documentation.

## High-value change map

| Change | Primary locations |
| --- | --- |
| Decision/evidence schema or impact analysis | `evidence_models.py`, `evidence_service.py`, `api/routes_decisions.py`, `specs/` |
| Recorder protocol or normalization | `recorder_*`, `api/routes_recorder.py`, SDK `recorder` modules, `specs/universal-recorder/` |
| Receipt/signing/trust | `decision_receipt.py`, `receipt_signer.py`, `receipt_cli.py`, control trust routes, `specs/decision-receipt/` |
| Runtime policy/Gate | `control_*`, `api/routes_control.py`, `gate_mediator/`, `specs/control-plane/` |
| Identity/tenancy/governance | `identity_*`, `enterprise_*`, `governance_*`, `authz.py`, `db.py` |
| Write, recall, supersession, erasure | `memory_service.py`, `supersession.py`, `subject_privacy.py`, `crypto.py` |
| Durable outbound delivery | `integration_*`, `metering*`, corresponding admin routes and runbooks |
| Production posture | `main.py`, `config.py`, `connection_security.py`, `deploy/`, `k8s/`, `ops/`, `.github/workflows/` |

## Core correctness invariants

- Present recall excludes facts that are no longer valid, while point-in-time
  recall reconstructs only facts visible inside both event-time and recorded-time
  boundaries.
- Every authoritative decision, Recorder event, immutable review, receipt trust
  record, and audit event verifies before it is disclosed or used for enforcement.
- Information barriers, namespace isolation, subject erasure, and legal holds are
  fail-closed at the strongest available boundary.
- Retries cannot duplicate consequential mutations; a reused idempotency key with
  a different canonical request is rejected.
- Durable outbox workers use leases, bounded retry horizons, immutable attempt
  ledgers, dead-letter reconciliation, and provider idempotency identifiers.
- Gate permits are short-lived, mediator- and request-bound, single-use, and never
  returned to the general caller as reusable provider credentials.
- Capture completeness and provenance strength are disclosed; absent evidence is
  not inferred, and availability is not mislabeled as causal use.
- Prometheus label vocabularies are bounded and tenant-neutral; durable inventory
  is not represented by process-local gauges.

When adding a route, require the narrowest authorization scope, establish tenant
context before the first database statement, use typed response models, preserve
the audit/idempotency transaction boundary, update SDK/spec/OpenAPI surfaces, and
add both positive and adversarial contract coverage.
