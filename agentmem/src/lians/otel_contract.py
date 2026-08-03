"""Lians OpenTelemetry attribute contract.

Standard ``gen_ai.*`` attributes remain authoritative for model operations.
The ``lians.*`` namespace adds only decision/evidence correlation fields that
do not currently exist in the OpenTelemetry semantic conventions.
"""
from __future__ import annotations

DECISION_ID = "lians.decision.id"
DECISION_TYPE = "lians.decision.type"
DECISION_OUTCOME = "lians.decision.outcome"
WORKFLOW_ID = "lians.workflow.id"
MEMORY_IDS = "lians.memory.ids"
EVIDENCE_IDS = "lians.evidence.ids"
POLICY_VERSION = "lians.policy.version"
KNOWLEDGE_AS_OF = "lians.knowledge.as_of"
CAPTURE_STATUS = "lians.capture.status"
WORKSPACE_ID = "lians.workspace.id"
GRAFANA_TRACE_URL = "lians.grafana.trace_url"

CAPTURE_STATUSES = {
    "complete",
    "complete_with_exclusions",
    "partial",
    "delayed",
    "failed",
    "unverifiable",
}

ALL_ATTRIBUTES = {
    DECISION_ID,
    DECISION_TYPE,
    DECISION_OUTCOME,
    WORKFLOW_ID,
    MEMORY_IDS,
    EVIDENCE_IDS,
    POLICY_VERSION,
    KNOWLEDGE_AS_OF,
    CAPTURE_STATUS,
    WORKSPACE_ID,
    GRAFANA_TRACE_URL,
}
