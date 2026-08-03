"""Bounded-cardinality Prometheus metrics for the Lians control plane.

Metric labels are a public security and cost boundary.  Every label in this
module is selected from a closed vocabulary.  Tenant namespaces, identities,
resource IDs, URLs, errors, policy names, and evidence values are deliberately
absent.  Durable inventory gauges are refreshed from PostgreSQL by
``observability_service``; per-replica health gauges are named as such.

Install the optional extra with ``pip install lians-platform[metrics]``.  The
helpers remain no-ops when ``prometheus-client`` is unavailable.
"""
from __future__ import annotations

from datetime import UTC, datetime

try:
    from prometheus_client import (
        CONTENT_TYPE_LATEST,
        CollectorRegistry,
        Counter,
        Gauge,
        Histogram,
        generate_latest,
    )

    _PROM_AVAILABLE = True
except ImportError:
    _PROM_AVAILABLE = False

_WRITE_BUCKETS = (0.001, 0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5)
_RECALL_BUCKETS = (0.0005, 0.001, 0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0)
_HTTP_BUCKETS = (0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0)

_WRITE_RELATIONS = (
    "ADDS",
    "CONFIRMS",
    "SUPERSEDES",
    "REFINES",
    "CONTRADICTS_SAME_TIME",
    "other",
)
_RECALL_ROUTERS = ("cache", "keyed", "semantic", "semantic_degraded", "other")
_CONFLICT_RESOLUTIONS = ("accept_a", "accept_b", "dismiss", "other")
_CONFLICT_STATUSES = ("open", "accept_a", "accept_b", "dismissed")
_RECORDER_OUTCOMES = ("accepted", "deduplicated", "rejected")
_RECORDER_READINESS = ("ready", "waiting")
_CAPTURE_MODES = ("metadata_only", "hash_only", "full")
_INTEGRATION_OUTCOMES = ("delivered", "retry", "dead_letter", "cancelled", "lease_lost")
_INTEGRATION_STATUSES = ("pending", "leased", "retry", "delivered", "dead_letter", "cancelled")
_IMPACT_OUTCOMES = (
    "created",
    "claimed",
    "advanced",
    "retry",
    "lease_lost",
    "completed",
    "failed",
)
_IMPACT_STATUSES = ("pending", "running", "completed", "failed")
_RECORDER_INDEX_STATUSES = ("pending", "running", "completed", "failed")
_SUBJECT_ERASURE_STATUSES = ("pending", "running", "completed", "failed")
_SCIM_RECONCILIATION_STATUSES = (
    "pending",
    "running",
    "completed",
    "failed",
    "superseded",
)
_INVESTIGATION_CASE_STATUSES = ("open", "in_review", "remediating", "resolved", "closed")
_REMEDIATION_TASK_STATUSES = ("pending", "in_progress", "blocked", "cancelled", "closed")
_RETENTION_LEADERSHIP = ("acquired", "contended", "local")
_RETENTION_OUTCOMES = ("completed", "partial_failure", "failed", "skipped")
_AUDIT_OUTCOMES = ("accepted", "rejected")
_REFRESH_OUTCOMES = ("success", "failure")
_HTTP_METHODS = ("GET", "HEAD", "POST", "PUT", "PATCH", "DELETE", "OPTIONS", "OTHER")
_HTTP_STATUS_CLASSES = ("1xx", "2xx", "3xx", "4xx", "5xx", "other")
_DECISION_EVIDENCE_CAPACITY_ENDPOINTS = ("create", "otlp")
_DECISION_EVIDENCE_CAPACITY_REASONS = ("count", "bytes", "both")
_BEST_EFFORT_COMPONENTS = (
    "auto_metadata",
    "merkle_batch",
    "siem_schedule",
    "subject_key_unwrap",
    "other",
)


class _Noop:
    """Drop-in for any Prometheus metric when prometheus-client is absent."""

    def labels(self, **_: object) -> _Noop:
        return self

    def inc(self, amount: float = 1) -> None:
        pass

    def dec(self, amount: float = 1) -> None:
        pass

    def set(self, value: float) -> None:
        pass

    def observe(self, amount: float) -> None:
        pass


_NOOP = _Noop()

if _PROM_AVAILABLE:
    REGISTRY = CollectorRegistry()

    _writes = Counter(
        "agentmem_memory_writes_total",
        "Committed memory writes by bounded supersession outcome",
        ["relation"],
        registry=REGISTRY,
    )
    _recalls = Counter(
        "agentmem_memory_recalls_total",
        "Committed recall evidence by bounded router and cache outcome",
        ["router", "cache_hit"],
        registry=REGISTRY,
    )
    _erased = Counter(
        "agentmem_memories_erased_total",
        "Memory records destroyed by subject erasure",
        registry=REGISTRY,
    )
    _erase_requests = Counter(
        "agentmem_erasure_requests_total",
        "Committed subject-erasure requests",
        registry=REGISTRY,
    )
    _add_hist = Histogram(
        "agentmem_add_duration_seconds",
        "Memory add wall time including supersession and commit",
        buckets=_WRITE_BUCKETS,
        registry=REGISTRY,
    )
    _recall_hist = Histogram(
        "agentmem_recall_duration_seconds",
        "Recall wall time including retrieval and evidence commit",
        buckets=_RECALL_BUCKETS,
        registry=REGISTRY,
    )
    _conflicts_detected = Counter(
        "agentmem_conflicts_detected_total",
        "Committed same-time structured-fact conflicts",
        registry=REGISTRY,
    )
    _conflicts_resolved = Counter(
        "agentmem_conflicts_resolved_total",
        "Committed conflict resolutions by bounded resolution",
        ["resolution"],
        registry=REGISTRY,
    )
    _conflict_inventory = Gauge(
        "lians_conflicts",
        "Durable conflict rows by bounded status, refreshed from the database",
        ["status"],
        registry=REGISTRY,
    )
    _otel_spans = Counter(
        "lians_otel_spans_accepted_total",
        "OTLP spans committed by the Lians evidence receiver",
        registry=REGISTRY,
    )
    _otel_decisions = Counter(
        "lians_otel_decisions_correlated_total",
        "Decision records correlated and committed from OTLP GenAI traces",
        registry=REGISTRY,
    )
    _http_requests = Counter(
        "lians_http_requests_total",
        "HTTP requests by bounded route group, method, and status class",
        ["route_group", "method", "status_class"],
        registry=REGISTRY,
    )
    _http_duration = Histogram(
        "lians_http_request_duration_seconds",
        "HTTP request wall time by bounded route group and method",
        ["route_group", "method"],
        buckets=_HTTP_BUCKETS,
        registry=REGISTRY,
    )
    _db_pool_size = Gauge(
        "lians_db_pool_size",
        "Configured SQLAlchemy pool size on this API replica",
        registry=REGISTRY,
    )
    _db_pool_checked_out = Gauge(
        "lians_db_pool_checked_out",
        "Database connections checked out by this API replica",
        registry=REGISTRY,
    )
    _db_pool_overflow = Gauge(
        "lians_db_pool_overflow",
        "Database overflow connections open by this API replica",
        registry=REGISTRY,
    )
    _metering_attempts = Counter(
        "lians_metering_delivery_attempts_total",
        "Durable Stripe metering provider attempts by bounded outcome",
        ["outcome"],
        registry=REGISTRY,
    )
    _metering_backlog = Gauge(
        "lians_metering_events",
        "Durable Stripe metering events by bounded status",
        ["status"],
        registry=REGISTRY,
    )
    _metering_oldest_due_age = Gauge(
        "lians_metering_oldest_due_age_seconds",
        "Age of the oldest non-terminal Stripe metering event",
        registry=REGISTRY,
    )
    _metering_delivery_enabled = Gauge(
        "lians_metering_delivery_enabled",
        "Whether Stripe delivery is enabled on this API replica",
        registry=REGISTRY,
    )
    _metering_worker_healthy = Gauge(
        "lians_metering_worker_healthy",
        "Whether this API replica's Stripe worker is polling successfully",
        registry=REGISTRY,
    )
    _gate_evaluations = Counter(
        "lians_gate_evaluations_total",
        "Committed Gate evaluations by bounded disposition",
        ["disposition"],
        registry=REGISTRY,
    )
    _gate_permit_events = Counter(
        "lians_gate_permit_events_total",
        "Gate execution-permit lifecycle events by bounded outcome",
        ["outcome"],
        registry=REGISTRY,
    )
    _idempotency_operations = Counter(
        "lians_idempotency_operations_total",
        "Transactional idempotency operations by bounded outcome",
        ["outcome"],
        registry=REGISTRY,
    )
    _recorder_events = Counter(
        "lians_recorder_events_total",
        "Recorder ingest events by committed or rejected outcome",
        ["outcome"],
        registry=REGISTRY,
    )
    _recorder_runs = Gauge(
        "lians_recorder_runs",
        "Durable Recorder runs by Decision Receipt readiness",
        ["readiness"],
        registry=REGISTRY,
    )
    _recorder_capture = Gauge(
        "lians_recorder_captured_events",
        "Durable Recorder events by bounded capture mode",
        ["capture_mode"],
        registry=REGISTRY,
    )
    _integration_attempts = Counter(
        "lians_integration_delivery_attempts_total",
        "Integration delivery attempts by bounded durable outcome",
        ["outcome"],
        registry=REGISTRY,
    )
    _integration_deliveries = Gauge(
        "lians_integration_deliveries",
        "Durable integration deliveries by bounded status",
        ["status"],
        registry=REGISTRY,
    )
    _integration_outbox_events = Gauge(
        "lians_integration_outbox_events",
        "Durable integration outbox events across all tenant boundaries",
        registry=REGISTRY,
    )
    _integration_oldest_due_age = Gauge(
        "lians_integration_oldest_due_age_seconds",
        "Age past due of the oldest non-terminal integration delivery",
        registry=REGISTRY,
    )
    _integration_delivery_enabled = Gauge(
        "lians_integration_delivery_enabled",
        "Whether integration delivery is enabled on this API replica",
        registry=REGISTRY,
    )
    _integration_worker_healthy = Gauge(
        "lians_integration_worker_healthy",
        "Whether this API replica's integration worker is polling successfully",
        registry=REGISTRY,
    )
    _impact_job_events = Counter(
        "lians_impact_job_events_total",
        "Exhaustive impact-assessment lifecycle events by bounded outcome",
        ["outcome"],
        registry=REGISTRY,
    )
    _impact_jobs = Gauge(
        "lians_impact_jobs",
        "Durable exhaustive impact-assessment jobs by bounded status",
        ["status"],
        registry=REGISTRY,
    )
    _impact_progress = Gauge(
        "lians_impact_scan_progress_ratio",
        "Exact aggregate frozen-snapshot row progress for active impact jobs",
        registry=REGISTRY,
    )
    _impact_oldest_active_age = Gauge(
        "lians_impact_oldest_active_age_seconds",
        "Age of the oldest pending or running impact-assessment job",
        registry=REGISTRY,
    )
    _impact_worker_enabled = Gauge(
        "lians_impact_worker_enabled",
        "Whether autonomous impact processing is enabled on this API replica",
        registry=REGISTRY,
    )
    _impact_worker_healthy = Gauge(
        "lians_impact_worker_healthy",
        "Whether this API replica's impact worker is polling successfully",
        registry=REGISTRY,
    )
    _impact_worker_heartbeat = Gauge(
        "lians_impact_worker_last_heartbeat_unixtime_seconds",
        "Unix time of this API replica's last impact-worker heartbeat",
        registry=REGISTRY,
    )
    _recorder_index_jobs = Gauge(
        "lians_recorder_evidence_index_jobs",
        "Durable fixed-snapshot Recorder evidence jobs by bounded status",
        ["status"],
        registry=REGISTRY,
    )
    _recorder_index_progress = Gauge(
        "lians_recorder_evidence_index_progress_ratio",
        "Exact aggregate event progress for active Recorder evidence snapshots",
        registry=REGISTRY,
    )
    _recorder_index_oldest_active_age = Gauge(
        "lians_recorder_evidence_index_oldest_active_age_seconds",
        "Age of the oldest pending or running Recorder evidence indexing job",
        registry=REGISTRY,
    )
    _recorder_index_worker_enabled = Gauge(
        "lians_recorder_evidence_index_worker_enabled",
        "Whether durable Recorder evidence indexing is enabled on this replica",
        registry=REGISTRY,
    )
    _recorder_index_worker_healthy = Gauge(
        "lians_recorder_evidence_index_worker_healthy",
        "Whether this replica's Recorder evidence indexing worker is healthy",
        registry=REGISTRY,
    )
    _recorder_index_worker_heartbeat = Gauge(
        "lians_recorder_evidence_index_worker_last_heartbeat_unixtime_seconds",
        "Unix time of this replica's last Recorder evidence indexing heartbeat",
        registry=REGISTRY,
    )
    _subject_erasure_jobs = Gauge(
        "lians_subject_erasure_jobs",
        "Durable fixed-snapshot subject-erasure jobs by bounded status",
        ["status"],
        registry=REGISTRY,
    )
    _subject_erasure_progress = Gauge(
        "lians_subject_erasure_progress_ratio",
        "Exact aggregate row progress for active subject-erasure snapshots",
        registry=REGISTRY,
    )
    _subject_erasure_oldest_active_age = Gauge(
        "lians_subject_erasure_oldest_active_age_seconds",
        "Age of the oldest pending or running subject-erasure job",
        registry=REGISTRY,
    )
    _subject_erasure_worker_enabled = Gauge(
        "lians_subject_erasure_worker_enabled",
        "Whether durable subject-erasure processing is enabled on this replica",
        registry=REGISTRY,
    )
    _subject_erasure_worker_healthy = Gauge(
        "lians_subject_erasure_worker_healthy",
        "Whether this replica's subject-erasure worker is healthy",
        registry=REGISTRY,
    )
    _subject_erasure_worker_heartbeat = Gauge(
        "lians_subject_erasure_worker_last_heartbeat_unixtime_seconds",
        "Unix time of this replica's last subject-erasure worker heartbeat",
        registry=REGISTRY,
    )
    _scim_reconciliation_jobs = Gauge(
        "lians_scim_binding_reconciliation_jobs",
        "Durable fixed-snapshot SCIM binding jobs by bounded status",
        ["status"],
        registry=REGISTRY,
    )
    _scim_reconciliation_progress = Gauge(
        "lians_scim_binding_reconciliation_progress_ratio",
        "Exact aggregate User progress for active SCIM binding snapshots",
        registry=REGISTRY,
    )
    _scim_reconciliation_oldest_active_age = Gauge(
        "lians_scim_binding_reconciliation_oldest_active_age_seconds",
        "Age of the oldest active SCIM binding reconciliation job",
        registry=REGISTRY,
    )
    _scim_reconciliation_worker_enabled = Gauge(
        "lians_scim_binding_reconciliation_worker_enabled",
        "Whether durable SCIM binding reconciliation is enabled on this replica",
        registry=REGISTRY,
    )
    _scim_reconciliation_worker_healthy = Gauge(
        "lians_scim_binding_reconciliation_worker_healthy",
        "Whether this replica's SCIM binding reconciliation worker is healthy",
        registry=REGISTRY,
    )
    _scim_reconciliation_worker_heartbeat = Gauge(
        "lians_scim_binding_reconciliation_worker_last_heartbeat_unixtime_seconds",
        "Unix time of this replica's last SCIM reconciliation heartbeat",
        registry=REGISTRY,
    )
    _decision_evidence_capacity_rejections = Counter(
        "lians_decision_evidence_capacity_rejections_total",
        "Fail-closed decision evidence candidate capacity rejections",
        ["endpoint", "reason"],
        registry=REGISTRY,
    )
    _protected_decisions = Gauge(
        "lians_protected_decisions",
        "Authoritative decision records protected by Lians",
        registry=REGISTRY,
    )
    _decision_evidence_complete_ratio = Gauge(
        "lians_decision_evidence_complete_ratio",
        "Ratio of protected decisions with all eight persisted evidence kinds complete",
        registry=REGISTRY,
    )
    _protected_actions = Gauge(
        "lians_protected_actions",
        "Successfully mediated actions with a consumed single-use Gate permit",
        registry=REGISTRY,
    )
    _impact_matches = Gauge(
        "lians_impact_matches",
        "Durable decision matches found across exhaustive impact assessments",
        registry=REGISTRY,
    )
    _investigation_cases = Gauge(
        "lians_investigation_cases",
        "Durable Investigator cases by bounded status",
        ["status"],
        registry=REGISTRY,
    )
    _remediation_tasks = Gauge(
        "lians_remediation_tasks",
        "Durable remediation tasks by bounded status",
        ["status"],
        registry=REGISTRY,
    )
    _remediation_overdue_tasks = Gauge(
        "lians_remediation_overdue_tasks",
        "Open remediation tasks whose due time has passed",
        registry=REGISTRY,
    )
    _closure_attestations = Gauge(
        "lians_closure_attestations",
        "Immutable human-attested case and remediation closures",
        registry=REGISTRY,
    )
    _retention_leader_elections = Counter(
        "lians_retention_leader_elections_total",
        "Retention cycle leadership decisions by bounded outcome",
        ["outcome"],
        registry=REGISTRY,
    )
    _retention_cycles = Counter(
        "lians_retention_cycles_total",
        "Retention prune cycles by bounded outcome",
        ["outcome"],
        registry=REGISTRY,
    )
    _retention_pruned = Counter(
        "lians_retention_memories_pruned_total",
        "Memory records pruned by committed retention cycles",
        registry=REGISTRY,
    )
    _retention_scheduler_enabled = Gauge(
        "lians_retention_scheduler_enabled",
        "Whether retention scheduling is enabled on this API replica",
        registry=REGISTRY,
    )
    _retention_scheduler_healthy = Gauge(
        "lians_retention_scheduler_healthy",
        "Whether this API replica's retention scheduler loop is healthy",
        registry=REGISTRY,
    )
    _retention_scheduler_interval = Gauge(
        "lians_retention_scheduler_interval_seconds",
        "Configured retention cycle interval on this API replica",
        registry=REGISTRY,
    )
    _retention_scheduler_heartbeat = Gauge(
        "lians_retention_scheduler_last_heartbeat_unixtime_seconds",
        "Unix time of this API replica's last retention scheduler heartbeat",
        registry=REGISTRY,
    )
    _audit_append_attempts = Counter(
        "lians_audit_append_boundary_attempts_total",
        "Authoritative audit append boundary calls by bounded outcome",
        ["outcome"],
        registry=REGISTRY,
    )
    _best_effort_failures = Counter(
        "lians_best_effort_failures_total",
        "Fail-open auxiliary failures by bounded, privacy-safe component",
        ["component"],
        registry=REGISTRY,
    )
    _inventory_refreshes = Counter(
        "lians_durable_inventory_refresh_total",
        "Durable observability inventory refreshes by bounded outcome",
        ["outcome"],
        registry=REGISTRY,
    )
    _inventory_refresh_healthy = Gauge(
        "lians_durable_inventory_refresh_healthy",
        "Whether this API replica's durable inventory refresh is healthy",
        registry=REGISTRY,
    )
    _inventory_last_success = Gauge(
        "lians_durable_inventory_last_success_unixtime_seconds",
        "Unix time of this API replica's last successful durable inventory refresh",
        registry=REGISTRY,
    )

    for value in _WRITE_RELATIONS:
        _writes.labels(relation=value)
    for router in _RECALL_ROUTERS:
        for cache_hit in ("true", "false"):
            _recalls.labels(router=router, cache_hit=cache_hit)
    for value in _CONFLICT_RESOLUTIONS:
        _conflicts_resolved.labels(resolution=value)
    for value in _CONFLICT_STATUSES:
        _conflict_inventory.labels(status=value)
    for value in ("pending", "leased", "retry", "delivered", "dead_letter"):
        _metering_backlog.labels(status=value)
    for value in ("delivered", "retry", "dead_letter", "lease_lost", "other"):
        _metering_attempts.labels(outcome=value)
    for value in ("allow", "deny", "review"):
        _gate_evaluations.labels(disposition=value)
    for value in ("issued", "consumed", "rejected", "expired", "replayed", "mismatched"):
        _gate_permit_events.labels(outcome=value)
    for value in (
        "claim_completed",
        "replay",
        "request_conflict",
        "invalid_key",
        "replay_unavailable",
    ):
        _idempotency_operations.labels(outcome=value)
    for value in _RECORDER_OUTCOMES:
        _recorder_events.labels(outcome=value)
    for value in _RECORDER_READINESS:
        _recorder_runs.labels(readiness=value)
    for value in _CAPTURE_MODES:
        _recorder_capture.labels(capture_mode=value)
    for value in _INTEGRATION_OUTCOMES:
        _integration_attempts.labels(outcome=value)
    for value in _INTEGRATION_STATUSES:
        _integration_deliveries.labels(status=value)
    for value in _IMPACT_OUTCOMES:
        _impact_job_events.labels(outcome=value)
    for value in _IMPACT_STATUSES:
        _impact_jobs.labels(status=value)
    for value in _INVESTIGATION_CASE_STATUSES:
        _investigation_cases.labels(status=value)
    for value in _REMEDIATION_TASK_STATUSES:
        _remediation_tasks.labels(status=value)
    for value in _RETENTION_LEADERSHIP:
        _retention_leader_elections.labels(outcome=value)
    for value in _RETENTION_OUTCOMES:
        _retention_cycles.labels(outcome=value)
    for value in _AUDIT_OUTCOMES:
        _audit_append_attempts.labels(outcome=value)
    for value in _BEST_EFFORT_COMPONENTS:
        _best_effort_failures.labels(component=value)
    for value in _REFRESH_OUTCOMES:
        _inventory_refreshes.labels(outcome=value)
else:
    REGISTRY = None  # type: ignore[assignment]
    _writes = _NOOP
    _recalls = _NOOP
    _erased = _NOOP
    _erase_requests = _NOOP
    _add_hist = _NOOP
    _recall_hist = _NOOP
    _conflicts_detected = _NOOP
    _conflicts_resolved = _NOOP
    _conflict_inventory = _NOOP
    _otel_spans = _NOOP
    _otel_decisions = _NOOP
    _http_requests = _NOOP
    _http_duration = _NOOP
    _db_pool_size = _NOOP
    _db_pool_checked_out = _NOOP
    _db_pool_overflow = _NOOP
    _metering_attempts = _NOOP
    _metering_backlog = _NOOP
    _metering_oldest_due_age = _NOOP
    _metering_delivery_enabled = _NOOP
    _metering_worker_healthy = _NOOP
    _gate_evaluations = _NOOP
    _gate_permit_events = _NOOP
    _idempotency_operations = _NOOP
    _recorder_events = _NOOP
    _recorder_runs = _NOOP
    _recorder_capture = _NOOP
    _integration_attempts = _NOOP
    _integration_deliveries = _NOOP
    _integration_outbox_events = _NOOP
    _integration_oldest_due_age = _NOOP
    _integration_delivery_enabled = _NOOP
    _integration_worker_healthy = _NOOP
    _impact_job_events = _NOOP
    _impact_jobs = _NOOP
    _impact_progress = _NOOP
    _impact_oldest_active_age = _NOOP
    _impact_worker_enabled = _NOOP
    _impact_worker_healthy = _NOOP
    _impact_worker_heartbeat = _NOOP
    _recorder_index_jobs = _NOOP
    _recorder_index_progress = _NOOP
    _recorder_index_oldest_active_age = _NOOP
    _recorder_index_worker_enabled = _NOOP
    _recorder_index_worker_healthy = _NOOP
    _recorder_index_worker_heartbeat = _NOOP
    _subject_erasure_jobs = _NOOP
    _subject_erasure_progress = _NOOP
    _subject_erasure_oldest_active_age = _NOOP
    _subject_erasure_worker_enabled = _NOOP
    _subject_erasure_worker_healthy = _NOOP
    _subject_erasure_worker_heartbeat = _NOOP
    _scim_reconciliation_jobs = _NOOP
    _scim_reconciliation_progress = _NOOP
    _scim_reconciliation_oldest_active_age = _NOOP
    _scim_reconciliation_worker_enabled = _NOOP
    _scim_reconciliation_worker_healthy = _NOOP
    _scim_reconciliation_worker_heartbeat = _NOOP
    _decision_evidence_capacity_rejections = _NOOP
    _protected_decisions = _NOOP
    _decision_evidence_complete_ratio = _NOOP
    _protected_actions = _NOOP
    _impact_matches = _NOOP
    _investigation_cases = _NOOP
    _remediation_tasks = _NOOP
    _remediation_overdue_tasks = _NOOP
    _closure_attestations = _NOOP
    _retention_leader_elections = _NOOP
    _retention_cycles = _NOOP
    _retention_pruned = _NOOP
    _retention_scheduler_enabled = _NOOP
    _retention_scheduler_healthy = _NOOP
    _retention_scheduler_interval = _NOOP
    _retention_scheduler_heartbeat = _NOOP
    _audit_append_attempts = _NOOP
    _best_effort_failures = _NOOP
    _inventory_refreshes = _NOOP
    _inventory_refresh_healthy = _NOOP
    _inventory_last_success = _NOOP


def _bounded(value: str, allowed: tuple[str, ...], fallback: str) -> str:
    return value if value in allowed else fallback


def _age_seconds(value: datetime | None) -> float:
    if value is None:
        return 0.0
    normalized = value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)
    return max(0.0, (datetime.now(UTC) - normalized).total_seconds())


def _route_group(route_template: str) -> str:
    """Collapse developer routes into a closed operational vocabulary."""

    if route_template in {"__unmatched__", "__oversized_template__"}:
        return "unmatched"
    if route_template in {"/health", "/livez", "/readyz", "/metrics"}:
        return "operations"
    if route_template in {"/v1/recall", "/v1/context"}:
        return "recall"
    prefixes = (
        ("/v1/memories", "memory"),
        ("/v1/erase", "memory"),
        ("/v1/recorder", "recorder"),
        ("/v1/traces", "recorder"),
        ("/v1/control/gate", "gate"),
        ("/v1/decisions", "decisions"),
        ("/v1/records", "decisions"),
        ("/v1/receipts", "decisions"),
        ("/v1/integrations", "integrations"),
        ("/v1/governance", "governance"),
        ("/v1/identity", "identity"),
        ("/v1/scim", "identity"),
        ("/v1/workload-credentials", "identity"),
        ("/v1/admin", "admin"),
    )
    for prefix, group in prefixes:
        if route_template.startswith(prefix):
            return group
    return "other_api" if route_template.startswith("/v1/") else "unmatched"


def set_db_pool_state(*, size: int, checked_out: int, overflow: int) -> None:
    _db_pool_size.set(max(0, size))
    _db_pool_checked_out.set(max(0, checked_out))
    _db_pool_overflow.set(max(0, overflow))


def record_metering_attempt(outcome: str) -> None:
    bounded = outcome if outcome in {"delivered", "retry", "dead_letter", "lease_lost"} else "other"
    _metering_attempts.labels(outcome=bounded).inc()


def set_metering_backlog(*, counts: dict[str, int], oldest_due_at: datetime | None) -> None:
    for status in ("pending", "leased", "retry", "delivered", "dead_letter"):
        _metering_backlog.labels(status=status).set(max(0, counts.get(status, 0)))
    _metering_oldest_due_age.set(_age_seconds(oldest_due_at))


def set_metering_worker_state(*, delivery_enabled: bool, healthy: bool) -> None:
    _metering_delivery_enabled.set(1 if delivery_enabled else 0)
    _metering_worker_healthy.set(1 if healthy else 0)


def record_gate_evaluation(disposition: str) -> None:
    _gate_evaluations.labels(
        disposition=_bounded(disposition, ("allow", "deny", "review"), "review")
    ).inc()


def record_gate_permit_outcome(outcome: str) -> None:
    allowed = ("issued", "consumed", "rejected", "expired", "replayed", "mismatched")
    _gate_permit_events.labels(outcome=_bounded(outcome, allowed, "rejected")).inc()


def record_idempotency_outcome(outcome: str) -> None:
    allowed = (
        "claim_completed",
        "replay",
        "request_conflict",
        "invalid_key",
        "replay_unavailable",
    )
    _idempotency_operations.labels(outcome=_bounded(outcome, allowed, "invalid_key")).inc()


def record_write(namespace: str, relation: str) -> None:
    """Record a committed write; ``namespace`` is intentionally ignored."""

    del namespace
    _writes.labels(relation=_bounded(relation, _WRITE_RELATIONS, "other")).inc()


def observe_add(namespace: str, seconds: float) -> None:
    del namespace
    _add_hist.observe(max(0.0, seconds))


def record_recall(namespace: str, router: str, cache_hit: bool) -> None:
    del namespace
    _recalls.labels(
        router=_bounded(router, _RECALL_ROUTERS, "other"),
        cache_hit="true" if cache_hit else "false",
    ).inc()


def observe_recall(namespace: str, seconds: float) -> None:
    del namespace
    _recall_hist.observe(max(0.0, seconds))


def record_erase(namespace: str, count: int) -> None:
    del namespace
    _erase_requests.inc()
    if count > 0:
        _erased.inc(count)


def record_conflict_detected(namespace: str, count: int = 1) -> None:
    del namespace
    if count > 0:
        _conflicts_detected.inc(count)


def record_conflict_resolved(namespace: str, resolution: str) -> None:
    del namespace
    _conflicts_resolved.labels(
        resolution=_bounded(resolution, _CONFLICT_RESOLUTIONS, "other")
    ).inc()


def set_conflict_inventory(counts: dict[str, int]) -> None:
    for status in _CONFLICT_STATUSES:
        _conflict_inventory.labels(status=status).set(max(0, counts.get(status, 0)))


def record_otel_ingest(namespace: str, spans: int, decisions: int) -> None:
    del namespace
    if spans > 0:
        _otel_spans.inc(spans)
    if decisions > 0:
        _otel_decisions.inc(decisions)


def record_http_request(
    route_template: str,
    method: str,
    status_class: str,
    seconds: float,
) -> None:
    route_group = _route_group(route_template)
    bounded_method = _bounded(method, _HTTP_METHODS, "OTHER")
    bounded_status = _bounded(status_class, _HTTP_STATUS_CLASSES, "other")
    _http_requests.labels(
        route_group=route_group,
        method=bounded_method,
        status_class=bounded_status,
    ).inc()
    _http_duration.labels(route_group=route_group, method=bounded_method).observe(
        max(0.0, seconds)
    )


def record_recorder_outcome(outcome: str, count: int = 1) -> None:
    if count > 0:
        _recorder_events.labels(
            outcome=_bounded(outcome, _RECORDER_OUTCOMES, "rejected")
        ).inc(count)


def set_recorder_inventory(
    *,
    run_counts: dict[str, int],
    capture_counts: dict[str, int],
) -> None:
    for readiness in _RECORDER_READINESS:
        _recorder_runs.labels(readiness=readiness).set(max(0, run_counts.get(readiness, 0)))
    for capture_mode in _CAPTURE_MODES:
        _recorder_capture.labels(capture_mode=capture_mode).set(
            max(0, capture_counts.get(capture_mode, 0))
        )


def record_integration_attempt(outcome: str) -> None:
    _integration_attempts.labels(
        outcome=_bounded(outcome, _INTEGRATION_OUTCOMES, "lease_lost")
    ).inc()


def set_integration_inventory(
    *,
    counts: dict[str, int],
    outbox_events: int,
    oldest_due_at: datetime | None,
) -> None:
    for status in _INTEGRATION_STATUSES:
        _integration_deliveries.labels(status=status).set(max(0, counts.get(status, 0)))
    _integration_outbox_events.set(max(0, outbox_events))
    _integration_oldest_due_age.set(_age_seconds(oldest_due_at))


def set_integration_worker_state(*, delivery_enabled: bool, healthy: bool) -> None:
    _integration_delivery_enabled.set(1 if delivery_enabled else 0)
    _integration_worker_healthy.set(1 if healthy else 0)


def record_impact_job_outcome(outcome: str) -> None:
    _impact_job_events.labels(
        outcome=_bounded(outcome, _IMPACT_OUTCOMES, "failed")
    ).inc()


def set_impact_inventory(
    *,
    counts: dict[str, int],
    progress_ratio: float,
    oldest_active_at: datetime | None,
) -> None:
    for status in _IMPACT_STATUSES:
        _impact_jobs.labels(status=status).set(max(0, counts.get(status, 0)))
    _impact_progress.set(max(0.0, min(1.0, progress_ratio)))
    _impact_oldest_active_age.set(_age_seconds(oldest_active_at))


def set_impact_worker_state(
    *,
    enabled: bool,
    healthy: bool,
    heartbeat_at: datetime | None,
) -> None:
    """Publish bounded, per-replica autonomous worker state."""

    _impact_worker_enabled.set(1 if enabled else 0)
    _impact_worker_healthy.set(1 if healthy else 0)
    if heartbeat_at is not None:
        normalized = (
            heartbeat_at.replace(tzinfo=UTC)
            if heartbeat_at.tzinfo is None
            else heartbeat_at.astimezone(UTC)
        )
        _impact_worker_heartbeat.set(normalized.timestamp())


def set_recorder_index_inventory(
    *,
    counts: dict[str, int],
    events_indexed: int,
    snapshot_events: int,
    oldest_active_at: datetime | None,
) -> None:
    for status in _RECORDER_INDEX_STATUSES:
        _recorder_index_jobs.labels(status=status).set(max(0, counts.get(status, 0)))
    ratio = events_indexed / snapshot_events if snapshot_events > 0 else 0.0
    _recorder_index_progress.set(max(0.0, min(1.0, ratio)))
    _recorder_index_oldest_active_age.set(_age_seconds(oldest_active_at))


def set_recorder_index_worker_state(
    *,
    enabled: bool,
    healthy: bool,
    heartbeat_at: datetime | None,
) -> None:
    _recorder_index_worker_enabled.set(1 if enabled else 0)
    _recorder_index_worker_healthy.set(1 if healthy else 0)
    if heartbeat_at is not None:
        normalized = (
            heartbeat_at.replace(tzinfo=UTC)
            if heartbeat_at.tzinfo is None
            else heartbeat_at.astimezone(UTC)
        )
        _recorder_index_worker_heartbeat.set(normalized.timestamp())


def set_subject_erasure_inventory(
    *,
    counts: dict[str, int],
    rows_scrubbed: int,
    snapshot_rows: int,
    oldest_active_at: datetime | None,
) -> None:
    for status in _SUBJECT_ERASURE_STATUSES:
        _subject_erasure_jobs.labels(status=status).set(
            max(0, counts.get(status, 0))
        )
    ratio = rows_scrubbed / snapshot_rows if snapshot_rows > 0 else 0.0
    _subject_erasure_progress.set(max(0.0, min(1.0, ratio)))
    _subject_erasure_oldest_active_age.set(_age_seconds(oldest_active_at))


def set_subject_erasure_worker_state(
    *,
    enabled: bool,
    healthy: bool,
    heartbeat_at: datetime | None,
) -> None:
    _subject_erasure_worker_enabled.set(1 if enabled else 0)
    _subject_erasure_worker_healthy.set(1 if healthy else 0)
    if heartbeat_at is not None:
        normalized = (
            heartbeat_at.replace(tzinfo=UTC)
            if heartbeat_at.tzinfo is None
            else heartbeat_at.astimezone(UTC)
        )
        _subject_erasure_worker_heartbeat.set(normalized.timestamp())


def set_scim_reconciliation_inventory(
    *,
    counts: dict[str, int],
    users_reconciled: int,
    snapshot_users: int,
    oldest_active_at: datetime | None,
) -> None:
    for status in _SCIM_RECONCILIATION_STATUSES:
        _scim_reconciliation_jobs.labels(status=status).set(
            max(0, counts.get(status, 0))
        )
    ratio = users_reconciled / snapshot_users if snapshot_users > 0 else 0.0
    _scim_reconciliation_progress.set(max(0.0, min(1.0, ratio)))
    _scim_reconciliation_oldest_active_age.set(_age_seconds(oldest_active_at))


def set_scim_reconciliation_worker_state(
    *,
    enabled: bool,
    healthy: bool,
    heartbeat_at: datetime | None,
) -> None:
    _scim_reconciliation_worker_enabled.set(1 if enabled else 0)
    _scim_reconciliation_worker_healthy.set(1 if healthy else 0)
    if heartbeat_at is not None:
        normalized = (
            heartbeat_at.replace(tzinfo=UTC)
            if heartbeat_at.tzinfo is None
            else heartbeat_at.astimezone(UTC)
        )
        _scim_reconciliation_worker_heartbeat.set(normalized.timestamp())


def record_decision_evidence_capacity_rejection(
    endpoint: str,
    *,
    count_exceeded: bool,
    bytes_exceeded: bool,
) -> None:
    bounded_endpoint = _bounded(
        endpoint,
        _DECISION_EVIDENCE_CAPACITY_ENDPOINTS,
        "create",
    )
    reason = (
        "both"
        if count_exceeded and bytes_exceeded
        else "bytes"
        if bytes_exceeded
        else "count"
    )
    _decision_evidence_capacity_rejections.labels(
        endpoint=bounded_endpoint,
        reason=_bounded(reason, _DECISION_EVIDENCE_CAPACITY_REASONS, "count"),
    ).inc()


def set_product_inventory(
    *,
    protected_decisions: int,
    evidence_complete_decisions: int,
    protected_actions: int,
    impact_matches: int,
    investigation_counts: dict[str, int],
    remediation_counts: dict[str, int],
    overdue_tasks: int,
    closure_attestations: int,
) -> None:
    """Publish durable, tenant-neutral customer outcome inventory."""

    bounded_decisions = max(0, protected_decisions)
    _protected_decisions.set(bounded_decisions)
    _decision_evidence_complete_ratio.set(
        min(
            1.0,
            max(
                0.0,
                evidence_complete_decisions / bounded_decisions
                if bounded_decisions
                else 0.0,
            ),
        )
    )
    _protected_actions.set(max(0, protected_actions))
    _impact_matches.set(max(0, impact_matches))
    for status in _INVESTIGATION_CASE_STATUSES:
        _investigation_cases.labels(status=status).set(
            max(0, investigation_counts.get(status, 0))
        )
    for status in _REMEDIATION_TASK_STATUSES:
        _remediation_tasks.labels(status=status).set(
            max(0, remediation_counts.get(status, 0))
        )
    _remediation_overdue_tasks.set(max(0, overdue_tasks))
    _closure_attestations.set(max(0, closure_attestations))


def record_retention_leadership(outcome: str) -> None:
    _retention_leader_elections.labels(
        outcome=_bounded(outcome, _RETENTION_LEADERSHIP, "contended")
    ).inc()


def record_retention_cycle(outcome: str) -> None:
    _retention_cycles.labels(
        outcome=_bounded(outcome, _RETENTION_OUTCOMES, "failed")
    ).inc()


def record_retention_pruned(count: int) -> None:
    """Record rows only after the namespace-level prune transaction commits."""

    if count > 0:
        _retention_pruned.inc(count)


def set_retention_scheduler_state(
    *,
    enabled: bool,
    healthy: bool,
    interval_seconds: float,
    heartbeat_at: datetime | None,
) -> None:
    _retention_scheduler_enabled.set(1 if enabled else 0)
    _retention_scheduler_healthy.set(1 if healthy else 0)
    _retention_scheduler_interval.set(max(0.0, interval_seconds))
    if heartbeat_at is not None:
        normalized = (
            heartbeat_at.replace(tzinfo=UTC)
            if heartbeat_at.tzinfo is None
            else heartbeat_at.astimezone(UTC)
        )
        _retention_scheduler_heartbeat.set(normalized.timestamp())


def record_audit_append_boundary(outcome: str) -> None:
    _audit_append_attempts.labels(
        outcome=_bounded(outcome, _AUDIT_OUTCOMES, "rejected")
    ).inc()


def record_best_effort_failure(component: str, *, count: int = 1) -> None:
    """Record a fail-open degradation without tenant or exception labels."""
    if count <= 0:
        return
    _best_effort_failures.labels(
        component=_bounded(component, _BEST_EFFORT_COMPONENTS, "other")
    ).inc(count)


def record_inventory_refresh(outcome: str, *, at: datetime | None = None) -> None:
    bounded = _bounded(outcome, _REFRESH_OUTCOMES, "failure")
    _inventory_refreshes.labels(outcome=bounded).inc()
    _inventory_refresh_healthy.set(1 if bounded == "success" else 0)
    if bounded == "success":
        _inventory_last_success.set((at or datetime.now(UTC)).timestamp())


def generate_metrics() -> tuple[bytes, str]:
    """Return a Prometheus text response using the isolated registry."""

    if not _PROM_AVAILABLE:
        return (
            b"# Lians: prometheus_client not installed.\n"
            b"# Install with: pip install lians-platform[metrics]\n",
            "text/plain; charset=utf-8",
        )

    from .impact_assessment_service import refresh_impact_worker_process_metrics
    from .integration_service import refresh_integration_process_metrics
    from .metering import refresh_metering_process_metrics
    from .scheduler import refresh_retention_process_metrics
    from .recorder_index_service import refresh_recorder_index_worker_process_metrics
    from .subject_erasure_service import refresh_subject_erasure_worker_process_metrics
    from .scim_reconciliation_service import (
        refresh_scim_reconciliation_worker_process_metrics,
    )

    refresh_metering_process_metrics()
    refresh_integration_process_metrics()
    refresh_impact_worker_process_metrics()
    refresh_recorder_index_worker_process_metrics()
    refresh_subject_erasure_worker_process_metrics()
    refresh_scim_reconciliation_worker_process_metrics()
    refresh_retention_process_metrics()
    return generate_latest(REGISTRY), CONTENT_TYPE_LATEST
