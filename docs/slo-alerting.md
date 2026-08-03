# Service levels, telemetry, and alerting

Lians is decision-evidence infrastructure. Availability alone is insufficient: the
service can answer requests while silently dropping telemetry or weakening retrieval.
The production telemetry contract therefore separates API availability, request
quality, evidence-ingest durability, and recovery readiness.

## Objectives and indicators

These are initial objectives for a 30-day rolling window. They become commitments only
after representative load and recovery drills show the deployment can meet them.

| Objective | Target | Indicator | Exclusions |
|---|---:|---|---|
| External API readiness availability | 99.9% | Successful HTTPS `/readyz` blackbox samples / scheduled samples | Approved maintenance only when announced and measured separately |
| Readiness latency | p95 ≤ 500 ms | Blackbox `probe_duration_seconds` | Measures readiness path, not end-user endpoint latency |
| Authenticated API request success | 99.9% | Non-5xx completed requests / completed requests | Client 4xx responses are not server errors |
| Interactive recall latency | p95 ≤ 500 ms | Server request histogram for recall endpoints | Background exports/backfills |
| Recorder accepted durability | 99.99% | Accepted spans minus receiver/enqueue loss / accepted spans | Explicitly rejected invalid payloads reported to the caller |
| Restore readiness | 100% scheduled drills | Passed drills / due drills | None; a missed drill is a failed control |

The application exports memory/recall and OTLP correlation metrics together with
bounded-cardinality route/status request counters and histograms. The supplied rules
keep external readiness separate from authenticated request success, and derive
recall latency only from a closed route-group vocabulary:

```text
lians_http_requests_total{route_group,method,status_class}
lians_http_request_duration_seconds_bucket{route_group,method,le}
```

Never label metrics with raw path, namespace, decision ID,
subject ID, API key, issuer subject, prompt, tool arguments, or evidence content.
Route groups are one of a source-defined closed set such as `recall`, `memory`,
`recorder`, `gate`, `decisions`, `integrations`, `identity`, `admin`, or
`operations`; unknown API routes collapse to `other_api` or `unmatched`.

The request instrumentation records body-limit, rate-limit, validation, route, and
unhandled-exception outcomes. Requests rejected before routing collapse to the
bounded `unmatched` group; raw URL paths are never exported.

## Decision-evidence observability contract

Counters describe bounded lifecycle events. Database-global gauges are refreshed
from durable rows every `OBSERVABILITY_REFRESH_SECONDS` (5--300 seconds) under the
internal admin RLS boundary. Every API replica publishes the same inventory, so
PromQL and dashboards use `max`, never `sum`, across replicas. Per-replica worker
and refresher gauges are explicitly named as health signals.

| Control | Event metrics | Durable inventory / health |
|---|---|---|
| Conflict review | None | `lians_conflicts{status=open|accept_a|accept_b|dismissed}` is authoritative |
| Recorder | `lians_recorder_events_total{outcome=accepted|deduplicated|rejected}` | `lians_recorder_runs{readiness=ready|waiting}`, `lians_recorder_captured_events{capture_mode=metadata_only|hash_only|full}` |
| Recorder evidence index | None; durable job state is authoritative | `lians_recorder_evidence_index_jobs{status}`, `lians_recorder_evidence_index_progress_ratio`, `lians_recorder_evidence_index_oldest_active_age_seconds`, per-replica enabled/healthy/last-heartbeat gauges |
| Integrations | `lians_integration_delivery_attempts_total{outcome=delivered|retry|dead_letter|cancelled|lease_lost}` | `lians_integration_deliveries{status}`, `lians_integration_outbox_events`, `lians_integration_oldest_due_age_seconds`, per-replica enabled/healthy gauges |
| Exhaustive impact | `lians_impact_job_events_total{outcome=created|claimed|advanced|retry|lease_lost|completed|failed}` | `lians_impact_jobs{status}`, `lians_impact_scan_progress_ratio`, `lians_impact_oldest_active_age_seconds`, per-replica enabled/healthy/last-heartbeat gauges |
| Decision evidence capacity | `lians_decision_evidence_capacity_rejections_total{endpoint=create|otlp,reason=count|bytes|both}` | None; the authoritative mutation fails atomically |
| Subject erasure | None; terminal memory counts retain the compatibility erasure counters | `lians_subject_erasure_jobs{status}`, `lians_subject_erasure_progress_ratio`, `lians_subject_erasure_oldest_active_age_seconds`, per-replica enabled/healthy/last-heartbeat gauges |
| SCIM binding reconciliation | None; durable tenant-version jobs and activation fences are authoritative | `lians_scim_binding_reconciliation_jobs{status}`, `lians_scim_binding_reconciliation_progress_ratio`, `lians_scim_binding_reconciliation_oldest_active_age_seconds`, per-replica enabled/healthy/last-heartbeat gauges |
| Product outcomes | None; these are authoritative inventories, not process counters | `lians_protected_decisions`, `lians_decision_evidence_complete_ratio`, `lians_protected_actions`, `lians_impact_matches`, `lians_investigation_cases{status}`, `lians_remediation_tasks{status}`, `lians_remediation_overdue_tasks`, `lians_closure_attestations` |
| Retention | `lians_retention_leader_elections_total{outcome}`, `lians_retention_cycles_total{outcome}`, `lians_retention_memories_pruned_total` | per-replica enabled, healthy, interval, and last-heartbeat gauges |
| Audit append | `lians_audit_append_boundary_attempts_total{outcome=accepted|rejected}` | The database chain remains authoritative |
| Inventory refresh | `lians_durable_inventory_refresh_total{outcome=success|failure}` | per-replica health and last-success timestamp |

`audit ... outcome="accepted"` means the authoritative append function or local
test boundary accepted the row inside the current transaction. It is deliberately
not named "committed": the enclosing mutation, outbox, and audit row can still roll
back atomically. A rejected boundary call is always a data-integrity investigation.

If durable inventory refresh is unhealthy or stale, the last gauge values are stale
unknowns, not zeros and not proof that a backlog cleared. Use tenant-authorized API,
Investigator, or database workflows for identities and root cause; never add those
values to metric labels.

## Error-budget policy

For 99.9% availability, the 30-day error budget is 0.1%, approximately 43.2 minutes
if samples are evenly distributed. The Prometheus rules use multi-window burn rates:

| Response | Long window | Short window | Burn threshold |
|---|---:|---:|---:|
| Page | 1 hour | 5 minutes | 14.4× |
| Page | 6 hours | 30 minutes | 6× |
| Ticket | 1 day | 2 hours | 3× |
| Ticket | 3 days | 6 hours | 1× |

Both long and short windows must cross a paging threshold. Missing readiness samples
page independently because missing telemetry must not look healthy.

When 50% of a monthly budget is consumed, freeze nonessential production changes and
assign an owner. At 75%, require incident-commander approval for any change unrelated
to reliability or security. At 100%, stop feature releases until the service owner
accepts a remediation plan with dates. Never erase or recategorize an outage to make
the objective pass.

## Scrape and routing contract

Install [the Prometheus rules](../ops/prometheus/lians-rules.yaml) and validate them
with the Prometheus version deployed by the operator before applying. Prometheus
alerting rules support `for`, `keep_firing_for`, labels, and templated annotations as
documented in the official
[alerting-rule reference](https://prometheus.io/docs/prometheus/latest/configuration/alerting_rules/).

Required scrape jobs:

- `lians-api`: a Prometheus Blackbox Exporter HTTPS probe of the externally routable
  `/readyz` endpoint every 30 seconds. The Blackbox Exporter defines `probe_success`
  as the probe outcome and also exposes duration and certificate-expiry signals.
- `agentmem`: an authenticated scrape of `/metrics` every 30 seconds using the bearer
  token in `METRICS_BEARER_TOKEN`. Keep the token in a Secret referenced by the
  monitoring system; never place it in a ConfigMap or rule file.
- `lians-otel-collector`: scrape port `8888` on every collector replica every 30
  seconds. Preserve `instance` so one unhealthy persistent queue is not hidden by an
  aggregate.
- Kubernetes state and kubelet volume metrics for Deployment/StatefulSet replicas,
  container restarts, and queue PVC free space.

Restrict `/metrics` and collector port `8888` to the monitoring namespace and require
TLS/authentication at the monitoring boundary. Application metrics contain no tenant
namespace label, but the backend, alerts, and dashboards still require operational
access controls because aggregate volumes and security-control outcomes are sensitive.

The deployed collector explicitly uses `without_type_suffix: true` and
`without_units: true`. Therefore its counters are named, for example,
`otelcol_exporter_enqueue_failed_spans`, not
`otelcol_exporter_enqueue_failed_spans_total`. Revalidate rules whenever the pinned
collector image changes. OpenTelemetry documents queue capacity/size, enqueue
failures, send failures, receiver refusals, and accepted/sent flow in its official
[Collector internal telemetry reference](https://opentelemetry.io/docs/collector/internal-telemetry/).

Replace relative `runbook` annotations with the immutable, externally reachable URL
for the released documentation before production. Alertmanager routes must page a
24×7 human for `severity=page`, create an owned work item for `severity=ticket`, and
send `data_loss=possible` to security/compliance as well as SRE.

## Alert meaning

- `LiansApiUnavailable` means every external readiness probe failed for two minutes.
  It is the clearest immediate availability page.
- Availability burn alerts mean the 99.9% budget is being spent too quickly; they
  deliberately page before the monthly budget is exhausted.
- `LiansApiMetricsScrapeDown` is diagnostic loss. It warns rather than pages when the
  independent readiness probe still succeeds.
- `LiansOtelQueueHigh` and `Critical` mean telemetry remains durable only while disk
  and queue capacity remain. Preserve the PVC and restore downstream flow.
- `LiansOtelQueueEnqueueLoss` and `LiansOtelReceiverRefusingSpans` can mean evidence
  loss. They page on any increase and remain firing for investigation.
- Export send failures alone do not prove loss because retries are enabled. The page
  requires persistent failures together with material queue utilization.
- `LiansSemanticRecallDegraded` means the API may be available while retrieval quality
  is falling back. It is a quality incident, not an availability event.
- `LiansIdempotencyRequestConflictsSustained` means clients are repeatedly reusing a
  retry key with a different request body or authenticated boundary. Replays and new
  claims are normal and do not alert; sustained body conflicts require SDK/client
  owner investigation.
- `LiansIdempotencyReplayUnavailable` means an immutable completed claim no longer
  resolves to its authoritative resource. This is a data-integrity page: preserve
  the database and investigate retention or RLS drift before allowing more writes.
- `LiansDurableInventoryRefreshUnhealthy` or `Stale` invalidates backlog dashboards
  on the affected replica until an authoritative refresh succeeds.
- Recorder rejection and waiting-run alerts identify evidence completeness risk
  without revealing which tenant or payload is involved.
- Recorder evidence-index health/heartbeat alerts are per replica; failed and old
  fixed-snapshot jobs protect evidence reconstruction. Preserve the durable job
  and cursor, and never recreate one merely to reset its age.
- Integration dead-letter/age alerts operate on PostgreSQL inventory; worker-health
  alerts are per replica and do not claim the durable backlog is process-local.
- Impact worker health/heartbeat alerts are per replica; impact failure/age alerts
  preserve the frozen job and cursor as evidence. Deleting and recreating a job is
  not remediation. The progress ratio uses persisted scanned-row and frozen-row
  counts, not global sequence magnitudes, so tenant interleaving cannot inflate it.
- Subject-erasure worker health is a privacy-control page. The request transaction
  has already destroyed the DEK, but a failed job means derivative-store scrubbing
  and the bounded certificate are incomplete. Preserve the job/evidence rows,
  remedy the stable failure code, and use the explicit retry endpoint.
- SCIM reconciliation health/heartbeat alerts are per replica. Failure or age pages
  mean an identity version remains activation-fenced; preserve the fixed User
  snapshot and job cursor, and never enable its bindings manually to clear an alert.
- Evidence-completeness and overdue-remediation alerts are product-control signals:
  resolve their tenant-specific scope through authorized Investigator workflows,
  never by adding tenant identifiers to Prometheus labels.
- Retention leadership contention is normal. Failed/partial cycles and stale scheduler
  heartbeats alert; manual unaudited deletion is never the fallback.
- `LiansAuditAppendBoundaryRejected` is a data-integrity page even when the outer API
  request also failed and rolled back.

## Dashboards

At minimum, provide four views with links to the relevant deployment, database,
collector, and incident system:

1. API: readiness success, error-budget remaining, probe p50/p95/p99, metrics scrape
   health, pod readiness/restarts, CPU, memory, and database pool saturation.
2. Recorder, controls, and outcomes: accepted/deduplicated/rejected Recorder events, durable
   ready/waiting runs and capture-mode inventory, collector queue size/capacity per
   replica, Recorder evidence-index backlog/progress/worker health, integration
   status/age/worker health, impact backlog/progress/worker health, subject-erasure
   backlog/progress/worker health, SCIM reconciliation backlog/progress/worker
   health and heartbeat age, retention
   freshness/outcomes, audit-boundary rejections, protected decisions/actions,
   evidence-complete ratio, impact matches, investigation/remediation state, overdue
   tasks, and attested closures.
3. PostgreSQL: writer availability, replication/failover state, connections, latency,
   locks, dead tuples, storage/IOPS, WAL generation/archive lag, latest restorable time,
   and backup age.
4. Recovery controls: last logical backup, WORM attestation status, last logical/PITR
   drill, achieved RPO/RTO, unresolved drill findings, KMS/key expiry, and certificate
   expiry.

Queue drain time should be derived from current queued batches divided by a sustained
successful export rate. Treat zero export rate with a nonzero queue as infinite drain
time, not zero.

## Release and alert validation gate

Before enabling a new rule set:

1. Validate syntax using the exact deployed Prometheus `promtool` binary.
2. Confirm all referenced series and labels exist on every target; counter suffixes
   are particularly sensitive to collector configuration.
3. Replay synthetic series through rule tests for healthy, missing, burn, recovery,
   queue-full, and counter-reset cases.
4. Route a test alert through Alertmanager to paging, ticketing, security, and the
   status-page workflow without using a real customer namespace.
5. Confirm inhibit rules suppress symptoms only when a higher-level root-cause page
   is firing; data-loss alerts must never be inhibited by API-down alerts.
6. Record rule version, Prometheus/collector versions, validation output, responders,
   and timestamps in the release evidence.
