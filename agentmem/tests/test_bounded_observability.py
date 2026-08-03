"""Security and durability contracts for the bounded Prometheus surface.

These tests are intentionally defined during the implementation phase and run
only in the later comprehensive validation phase.
"""
from __future__ import annotations

import inspect
from datetime import UTC, datetime
from types import SimpleNamespace

import pytest

pytest.importorskip("prometheus_client", reason="prometheus_client not installed")

from lians import metrics
from lians.observability_service import refresh_durable_inventory
from prometheus_client import generate_latest

_ALLOWED_LABELS = {
    "cache_hit",
    "capture_mode",
    "disposition",
    "le",
    "method",
    "outcome",
    "readiness",
    "relation",
    "resolution",
    "route_group",
    "status",
    "status_class",
}
_FORBIDDEN_LABELS = {
    "namespace",
    "tenant",
    "tenant_id",
    "decision_id",
    "event_id",
    "job_id",
    "subject_id",
    "principal_id",
    "destination",
    "url",
    "error",
    "policy",
}


def test_registry_has_only_closed_non_tenant_label_names():
    seen: set[str] = set()
    for family in metrics.REGISTRY.collect():
        for sample in family.samples:
            seen.update(sample.labels)
    assert not (seen & _FORBIDDEN_LABELS)
    assert seen <= _ALLOWED_LABELS


def test_hostile_values_cannot_create_prometheus_cardinality_or_leakage():
    secret_namespace = "customer-secret-namespace"
    attacker_value = "attacker-controlled-value"

    metrics.record_write(secret_namespace, attacker_value)
    metrics.record_recall(secret_namespace, attacker_value, False)
    metrics.record_conflict_resolved(secret_namespace, attacker_value)
    metrics.record_otel_ingest(secret_namespace, 1, 1)
    metrics.record_http_request(
        "/tenant/customer-secret-namespace/arbitrary/path",
        attacker_value,
        attacker_value,
        0.1,
    )
    rendered = generate_latest(metrics.REGISTRY).decode()

    assert secret_namespace not in rendered
    assert attacker_value not in rendered
    assert 'relation="other"' in rendered
    assert 'router="other"' in rendered
    assert 'resolution="other"' in rendered
    assert 'route_group="unmatched"' in rendered
    assert 'method="OTHER"' in rendered
    assert 'status_class="other"' in rendered


def test_process_local_conflict_queue_metric_was_removed():
    rendered = generate_latest(metrics.REGISTRY).decode()
    assert "agentmem_conflict_queue_depth" not in rendered
    assert 'lians_conflicts{status="open"}' in rendered


def test_durable_inventory_refresher_queries_global_status_not_namespace_series():
    source = inspect.getsource(refresh_durable_inventory)
    assert "ConflictFlag.status" in source
    assert "IntegrationDelivery.status" in source
    assert "DecisionImpactAssessmentJob.status" in source
    assert "DecisionImpactAssessmentJob.decisions_scanned" in source
    assert "DecisionImpactAssessmentJob.snapshot_decision_count" in source
    assert "DecisionImpactAssessmentJob.cursor_coverage_sequence" not in source
    assert "group_by(ConflictFlag.namespace" not in source
    assert "group_by(IntegrationDelivery.namespace" not in source
    assert "group_by(DecisionImpactAssessmentJob.namespace" not in source


def test_all_new_lifecycle_helpers_bound_unknown_outcomes():
    metrics.record_recorder_outcome("secret-error-code")
    metrics.record_integration_attempt("secret-destination")
    metrics.record_impact_job_outcome("secret-job")
    metrics.record_retention_leadership("secret-replica")
    metrics.record_retention_cycle("secret-policy")
    metrics.record_audit_append_boundary("secret-operation")
    metrics.record_inventory_refresh("secret-database-error")

    rendered = generate_latest(metrics.REGISTRY).decode()
    for secret in (
        "secret-error-code",
        "secret-destination",
        "secret-job",
        "secret-replica",
        "secret-policy",
        "secret-operation",
        "secret-database-error",
    ):
        assert secret not in rendered


def test_product_outcome_inventory_is_tenant_free_and_bounded():
    metrics.set_product_inventory(
        protected_decisions=2,
        evidence_complete_decisions=99,
        protected_actions=3,
        impact_matches=4,
        investigation_counts={"open": 1, "secret-case-status": 999},
        remediation_counts={"blocked": 2, "secret-task-status": 999},
        overdue_tasks=1,
        closure_attestations=5,
    )

    rendered = generate_latest(metrics.REGISTRY).decode()
    assert "secret-case-status" not in rendered
    assert "secret-task-status" not in rendered
    assert "lians_protected_decisions 2.0" in rendered
    assert "lians_decision_evidence_complete_ratio 1.0" in rendered
    assert 'lians_investigation_cases{status="open"} 1.0' in rendered
    assert 'lians_remediation_tasks{status="blocked"} 2.0' in rendered


def test_integration_scrape_refresh_does_not_mask_recent_iteration_failure(monkeypatch):
    import lians.integration_service as integrations

    now = datetime.now(UTC)
    monkeypatch.setattr(integrations, "_worker_last_poll_at", now)
    monkeypatch.setattr(integrations, "_worker_last_heartbeat_at", now)
    monkeypatch.setattr(integrations, "_worker_last_iteration_healthy", False)
    monkeypatch.setattr(
        integrations,
        "get_settings",
        lambda: SimpleNamespace(
            integration_worker_enabled=True,
            airgap_mode=False,
            integration_worker_poll_seconds=1.0,
        ),
    )

    integrations.refresh_integration_process_metrics()
    healthy, last_poll = integrations.integration_worker_status()

    assert healthy is False
    assert last_poll == now


def test_metering_scrape_refresh_does_not_mask_recent_iteration_failure(monkeypatch):
    from lians import metering

    now = datetime.now(UTC)
    monkeypatch.setattr(metering, "_worker_last_poll_at", now)
    monkeypatch.setattr(metering, "_worker_last_heartbeat_at", now)
    monkeypatch.setattr(metering, "_worker_last_iteration_healthy", False)
    monkeypatch.setattr(metering, "_worker_terminal_error", None)
    monkeypatch.setattr(
        metering,
        "get_settings",
        lambda: SimpleNamespace(
            stripe_api_key="sk_live_configured",
            stripe_meter_worker_enabled=True,
            stripe_meter_worker_poll_seconds=1.0,
            airgap_mode=False,
        ),
    )

    metering.refresh_metering_process_metrics()
    healthy, last_poll = metering.metering_worker_status()

    assert healthy is False
    assert last_poll == now


def test_impact_scrape_refresh_does_not_mask_recent_iteration_failure(monkeypatch):
    import lians.impact_assessment_service as impact_worker

    now = datetime.now(UTC)
    monkeypatch.setattr(impact_worker, "_worker_last_poll_at", now)
    monkeypatch.setattr(impact_worker, "_worker_last_heartbeat_at", now)
    monkeypatch.setattr(impact_worker, "_worker_last_iteration_healthy", False)
    monkeypatch.setattr(
        impact_worker,
        "get_settings",
        lambda: SimpleNamespace(
            impact_assessment_worker_enabled=True,
            impact_assessment_worker_poll_seconds=1.0,
        ),
    )

    impact_worker.refresh_impact_worker_process_metrics()
    healthy, heartbeat = impact_worker.impact_worker_status()

    assert healthy is False
    assert heartbeat == now
