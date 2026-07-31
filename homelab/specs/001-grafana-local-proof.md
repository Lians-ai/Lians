# Spec 001: local Grafana integration proof

Status: implemented MVP

Owner: Lians platform team
Required reviewers: product owner and platform/security reviewer
Release gate: automated checks green, manual Grafana walkthrough completed, and
known limitations recorded in the proof handoff

## Problem

A partner asking whether Lians works with Grafana should receive a repeatable,
inspectable proof—not an architecture promise. The proof must exercise the real
OTLP, Prometheus, decision-envelope, recall-receipt, and Evidence Pack surfaces.

## User story

As a partner engineer, I can launch one local stack, observe an AI workflow in
Grafana, use the identical trace ID to inspect its governed decision evidence in
Lians, and run an automated verifier that fails when the contract breaks.

## Scope

- Real Lians API, migrations, Postgres 16/pgvector, and Redis.
- Prometheus product metrics and alerts.
- Alloy collection and trace fan-out.
- Tempo traces and Loki Docker logs.
- Locally built unsigned Lians Grafana app plus a provisioned proof dashboard.
- A deterministic synthetic risk-decision workload.
- Machine-readable local verification receipt.

## Non-goals

- Production capacity or availability claims.
- Grafana catalog publication, review, or signing.
- Real customer data or production credentials.
- HA Kubernetes, multi-node failure, or internet exposure.
- Retrieval-quality claims from deterministic hash embeddings.

## Requirements

- `GRAF-001`: one launcher command builds and starts the complete MVP.
- `GRAF-002`: Postgres and Redis are internal-only; user surfaces bind to loopback.
- `GRAF-003`: Lians runs one worker and enables real Prometheus/OTEL extras.
- `GRAF-004`: partner OTLP traces are exported unsampled to both Tempo and Lians.
- `GRAF-005`: Lians runtime traces use a separate Tempo-only pipeline.
- `GRAF-006`: bootstrap provisions the Alloy key through the supported admin API;
  no API key is committed.
- `GRAF-007`: the scenario opens an envelope with a trace ID, performs bound
  recall, sends that trace through Alloy, and seals a decision.
- `GRAF-008`: the resulting evidence includes a recall receipt and OTEL trace/span
  evidence connected to that decision.
- `GRAF-009`: Grafana datasources, plugin, and dashboard require no UI setup.
- `GRAF-010`: verification emits JSON and exits non-zero on contract failure.
- `GRAF-010a`: the regulated synthetic decision reaches `replayable`
  completeness; a lower grade fails verification.
- `GRAF-011`: log/trace/metric retention is bounded to one day in the MVP.
- `GRAF-012`: the documentation clearly labels synthetic data, unsigned plugin,
  test-grade embeddings, and non-production credentials.

## Acceptance procedure

1. Run `lab.ps1 reset -Force` (or `lab.sh reset --force`) if old state exists.
2. Run `lab.ps1 up` (or `lab.sh up`).
3. Confirm the launcher exits zero and creates a verification receipt under
   `homelab/artifacts/`.
4. Confirm the receipt reports all component checks as passing.
5. In Grafana, open the provisioned dashboard and confirm metrics, a partner
   trace, and Lians/workload logs have data.
6. Run `lab.ps1 proof` (or `lab.sh proof`) and confirm its trace ID is present in
   both the sanitized OTLP evidence sources and sealed decision metadata; use the
   same ID to open the Tempo trace in Grafana.
7. Restart `lians`; repeat verification and confirm persisted evidence survives.

## Proof receipt minimum fields

- verification timestamp and overall result;
- component health results;
- Prometheus Lians target state;
- decision ID, envelope ID, trace ID, completeness grade;
- evidence types and source IDs;
- Evidence Pack schema/hash fields returned by Lians;
- no raw API keys, admin secret, or customer content.

## Requirement traceability

| Requirement | Primary evidence | Gate |
|---|---|---|
| GRAF-001 | `lab.ps1 up` / `lab.sh up` | launcher exits zero |
| GRAF-002, GRAF-003, GRAF-005, GRAF-006 | static contracts plus Compose model validation | CI |
| GRAF-004, GRAF-007, GRAF-008 | workload proof and verifier `proof`/`tempo` checks | local and E2E CI |
| GRAF-009 | authenticated Grafana plugin/datasource/dashboard checks | verifier |
| GRAF-010, GRAF-010a | verifier exit status and exported receipt | local and E2E CI |
| GRAF-011 | pinned one-day service configuration and runtime startup | config review/E2E CI |
| GRAF-012 | README safety-boundary review | product/security review |

The reviewer records the Git revision (including a `-dirty` suffix), receipt
path, and any manual observations before changing this spec's status.

## Deferred hardening specs

- Five-minute bounded load and declared SLO thresholds.
- Redis, collector, Grafana, and API outage isolation/queue-drain drills.
- Restricted application DB role and executable RLS isolation proof.
- Telemetry content scanner for prompts, secrets, subject IDs, and PII.
- Image digest lock, SBOM, and signed proof manifest.
- Current/minimum Grafana compatibility matrix and plugin signing workflow.
