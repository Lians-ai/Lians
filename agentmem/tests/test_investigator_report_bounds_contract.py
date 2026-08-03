"""Deferred contracts for bounded, truthfully complete Investigator packets."""

from __future__ import annotations

import inspect

from lians.api.routes_investigator import investigate_decision
from lians.investigator_schemas import (
    DecisionInvestigationReport,
    InvestigatorCollectionWindow,
    InvestigatorIntegrity,
    InvestigatorReportCoverage,
)
from lians.investigator_service import build_decision_investigation


def test_report_v11_has_machine_readable_collection_coverage() -> None:
    assert DecisionInvestigationReport.model_fields["report_version"].default == "1.1"
    assert set(InvestigatorReportCoverage.model_fields) == {
        "complete",
        "audit_scope_complete",
        "receipt_evidence_scope_complete",
        "evidence_links",
        "evidence_artifacts",
        "timeline",
        "gate_evaluations",
        "approval_attestations",
        "review_history",
        "cases",
        "remediation_tasks",
        "closure_attestations",
    }
    assert set(InvestigatorCollectionWindow.model_fields) == {
        "limit",
        "returned",
        "total",
        "total_is_lower_bound",
        "truncated",
        "complete",
        "ordering",
        "scope",
    }


def test_integrity_contract_distinguishes_partial_from_invalid() -> None:
    review_annotation = str(
        InvestigatorIntegrity.model_fields["review_chain_status"].annotation
    )
    approval_annotation = str(
        InvestigatorIntegrity.model_fields["approval_attestations_status"].annotation
    )
    assert "partial" in review_annotation
    assert "partial" in approval_annotation
    assert "invalid" in approval_annotation
    assert "None" in str(
        InvestigatorIntegrity.model_fields["approval_attestations_valid"].annotation
    )


def test_report_route_exposes_independent_bounded_windows() -> None:
    parameters = inspect.signature(investigate_decision).parameters
    assert {
        "timeline_limit",
        "evidence_limit",
        "control_history_limit",
        "case_limit",
        "task_limit",
        "closure_limit",
    }.issubset(parameters)


def test_service_uses_limit_plus_one_and_unbounded_gate_case_linkage() -> None:
    source = inspect.getsource(build_decision_investigation)
    for expression in (
        ".limit(limit + 1)",
        ".limit(case_limit + 1)",
        ".limit(task_limit + 1)",
        ".limit(closure_limit + 1)",
        "InvestigationCase.gate_decision_id.in_(visible_gate_ids)",
        'review_status = "partial"',
        'approval_status = "partial"',
    ):
        assert expression in source
