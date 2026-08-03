"""Public contracts for namespace governance policy and daily usage."""

from __future__ import annotations

from datetime import date, datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

CaptureMode = Literal["metadata_only", "hash_only", "full"]
GovernanceStatus = Literal["unconfigured", "active", "disabled"]
_MAX_DAILY_QUOTA = 9_223_372_036_854_775_807


class NamespaceGovernancePolicyUpdate(BaseModel):
    """Complete replacement of the enforceable portion of a namespace policy."""

    model_config = ConfigDict(extra="forbid")

    expected_version: int = Field(
        ge=0,
        description="Current policy_version, or 0 to assert first configuration",
    )
    allowed_processing_regions: list[str] | None = Field(default=None, max_length=100)
    allowed_recorder_capture_modes: list[CaptureMode] | None = Field(
        default=None,
        max_length=3,
    )
    recorder_events_daily_limit: int | None = Field(
        default=None,
        ge=0,
        le=_MAX_DAILY_QUOTA,
    )
    decision_records_daily_limit: int | None = Field(
        default=None,
        ge=0,
        le=_MAX_DAILY_QUOTA,
    )
    protected_actions_daily_limit: int | None = Field(
        default=None,
        ge=0,
        le=_MAX_DAILY_QUOTA,
    )
    memory_writes_daily_limit: int | None = Field(
        default=None,
        ge=0,
        le=_MAX_DAILY_QUOTA,
    )
    recalls_daily_limit: int | None = Field(
        default=None,
        ge=0,
        le=_MAX_DAILY_QUOTA,
    )
    estimated_ingest_bytes_daily_limit: int | None = Field(
        default=None,
        ge=0,
        le=_MAX_DAILY_QUOTA,
    )

    @field_validator("allowed_processing_regions")
    @classmethod
    def normalize_regions(cls, value: list[str] | None) -> list[str] | None:
        if value is None:
            return None
        normalized: set[str] = set()
        for raw in value:
            region = raw.strip().lower()
            if not region or len(region) > 100:
                raise ValueError("processing regions must contain 1 to 100 characters")
            if not all(character.isalnum() or character in {"-", "_", "."} for character in region):
                raise ValueError(
                    "processing regions may contain only letters, numbers, '-', '_', and '.'"
                )
            normalized.add(region)
        return sorted(normalized)

    @field_validator("allowed_recorder_capture_modes")
    @classmethod
    def normalize_capture_modes(
        cls,
        value: list[CaptureMode] | None,
    ) -> list[CaptureMode] | None:
        if value is None:
            return None
        order = {"metadata_only": 0, "hash_only": 1, "full": 2}
        return sorted(set(value), key=order.__getitem__)


class NamespaceGovernanceStatusUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    expected_version: int = Field(ge=1)
    status: Literal["active", "disabled"]


class NamespaceGovernancePolicyOut(BaseModel):
    namespace: str
    configured: bool
    status: GovernanceStatus
    policy_version: int
    deployment_region: str
    processing_region_allowed: bool
    allowed_processing_regions: list[str] | None
    allowed_recorder_capture_modes: list[CaptureMode] | None
    effective_recorder_capture_modes: list[CaptureMode]
    recorder_events_daily_limit: int | None
    decision_records_daily_limit: int | None
    protected_actions_daily_limit: int | None
    memory_writes_daily_limit: int | None
    recalls_daily_limit: int | None
    estimated_ingest_bytes_daily_limit: int | None
    governance_created_at: datetime | None
    governance_created_by: str | None
    governance_updated_at: datetime | None
    governance_updated_by: str | None


class NamespaceDailyUsageOut(BaseModel):
    namespace: str
    usage_date: date
    period_start: datetime
    reset_at: datetime
    recorder_events: int
    decision_records: int
    protected_actions: int
    memory_writes: int
    recalls: int
    estimated_ingest_bytes: int
    recorder_events_remaining: int | None
    decision_records_remaining: int | None
    protected_actions_remaining: int | None
    memory_writes_remaining: int | None
    recalls_remaining: int | None
    estimated_ingest_bytes_remaining: int | None


class EffectiveNamespaceGovernanceOut(BaseModel):
    generated_at: datetime
    policy: NamespaceGovernancePolicyOut
    usage: NamespaceDailyUsageOut
    enforcement_basis: Literal["server_configuration"] = "server_configuration"
    disclosures: list[str]


class NamespaceGovernanceStatusOut(BaseModel):
    effective: EffectiveNamespaceGovernanceOut
    revision_count: int
    latest_snapshot_hash: str | None


class NamespacePolicyRevisionOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    namespace: str
    policy_version: int
    action: Literal["created", "updated", "enabled", "disabled", "cleared"]
    actor_id: str
    policy_snapshot: dict
    snapshot_hash: str
    created_at: datetime
