"""Stable discovery and deployment-readiness contracts."""
from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel


class LiansDiscoveryDocument(BaseModel):
    name: Literal["Lians"] = "Lians"
    category: Literal["decision_evidence_infrastructure"] = (
        "decision_evidence_infrastructure"
    )
    api_version: str
    decision_receipt_version: str
    universal_recorder_version: str
    protocols: list[str]
    authentication: list[str]
    links: dict[str, str]


class PlatformCapabilities(BaseModel):
    generated_at: datetime
    namespace: str
    principal_type: str
    authentication_method: str
    information_barrier_scoped: bool
    components: dict[str, dict[str, Any]]
    standards: dict[str, dict[str, Any]]
    privacy: dict[str, Any]
    links: dict[str, str]


class ReadinessCheck(BaseModel):
    id: str
    status: Literal["pass", "warning", "fail", "not_configured"]
    message: str
    required_for: list[str]


class PlatformReadiness(BaseModel):
    generated_at: datetime
    namespace: str
    status: Literal["ready", "degraded", "configuration_required"]
    production_baseline_ready: bool
    control_plane_ready: bool
    enterprise_identity_ready: bool
    checks: list[ReadinessCheck]
    inventory: dict[str, int]
    disclosures: list[str]
