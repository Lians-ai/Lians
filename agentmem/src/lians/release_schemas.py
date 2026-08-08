"""Contracts for release candidates, signed approvals, deployment evidence, and rollback."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class RolloutPlan(BaseModel):
    model_config = ConfigDict(extra="forbid")

    require_shadow: bool = True
    require_canary: bool = True
    canary_percentage: float = Field(default=5, gt=0, lt=100)
    rollback_on_statuses: list[str] = Field(default_factory=lambda: ["failed"], max_length=100)
    monitoring_window_seconds: int = Field(default=3600, ge=60, le=2_592_000)


class ReleaseCandidateCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=255)
    version: str = Field(min_length=1, max_length=255)
    agent_version_id: UUID
    evaluation_attestation_id: UUID
    optimization_study_id: UUID | None = None
    environment_manifest: dict[str, Any] = Field(min_length=1, max_length=1000)
    rollout_plan: RolloutPlan = Field(default_factory=RolloutPlan)


class ReleaseCandidateOut(BaseModel):
    id: UUID
    namespace: str
    barrier_group: str | None
    name: str
    version: str
    agent_version_id: UUID
    evaluation_attestation_id: UUID
    optimization_study_id: UUID | None
    environment_manifest: dict[str, Any]
    rollout_plan: RolloutPlan
    release_hash: str
    created_by_principal_ref: str
    created_at: datetime


class ReleaseAttestationCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    release_candidate_id: UUID
    approval_attestation_ids: list[UUID] = Field(min_length=1, max_length=100)

    @field_validator("approval_attestation_ids")
    @classmethod
    def unique_approvals(cls, values: list[UUID]):
        if len(values) != len(set(values)):
            raise ValueError("approval attestation ids must be unique")
        return values


class ReleaseAttestationOut(BaseModel):
    id: UUID
    namespace: str
    barrier_group: str | None
    schema_version: Literal["0.1"]
    release_candidate_id: UUID
    evaluation_attestation_id: UUID
    approval_attestation_ids: list[UUID]
    payload: dict[str, Any]
    payload_hash: str
    signature_algorithm: Literal["ed25519"]
    signing_key_id: str
    signing_public_key: str
    signature: str
    attested_by_principal_ref: str
    attested_at: datetime


class ReleaseAttestationVerifyRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    attestation: ReleaseAttestationOut
    trusted_public_key: str | None = None


class ReleaseAttestationVerification(BaseModel):
    valid: bool
    payload_hash_valid: bool
    signature_valid: bool
    errors: list[str]


class DeploymentCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    release_attestation_id: UUID
    stage: Literal["shadow", "canary", "production"]
    traffic_percentage: float = Field(ge=0, le=100)
    environment: str = Field(min_length=1, max_length=255)
    external_deployment_reference: str = Field(min_length=1, max_length=2048)
    prior_deployment_id: UUID | None = None
    evidence: dict[str, Any] = Field(default_factory=dict, max_length=1000)
    status: Literal["observed", "healthy", "failed"]
    deployed_at: datetime

    @model_validator(mode="after")
    def stage_shape(self):
        if self.stage == "shadow" and self.traffic_percentage != 0:
            raise ValueError("shadow deployment traffic must be zero")
        if self.stage == "canary" and not 0 < self.traffic_percentage < 100:
            raise ValueError("canary traffic must be between zero and 100")
        if self.stage == "production" and self.traffic_percentage != 100:
            raise ValueError("production deployment traffic must be 100 percent")
        return self


class DeploymentOut(BaseModel):
    id: UUID
    release_attestation_id: UUID
    stage: str
    traffic_percentage: float
    environment: str
    external_deployment_ref_hash: str
    prior_deployment_id: UUID | None
    evidence: dict[str, Any]
    status: str
    deployment_hash: str
    recorded_by_principal_ref: str
    deployed_at: datetime
    recorded_at: datetime


class RollbackCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    deployment_id: UUID
    target_deployment_id: UUID
    reason_code: str = Field(min_length=1, max_length=128, pattern=r"^[A-Za-z0-9][A-Za-z0-9._:-]*$")
    evidence: dict[str, Any] = Field(default_factory=dict, max_length=1000)
    external_rollback_reference: str = Field(min_length=1, max_length=2048)
    rolled_back_at: datetime

    @model_validator(mode="after")
    def distinct_deployments(self):
        if self.deployment_id == self.target_deployment_id:
            raise ValueError("rollback source and target deployments must differ")
        return self


class RollbackOut(BaseModel):
    id: UUID
    deployment_id: UUID
    target_deployment_id: UUID
    reason_code: str
    evidence: dict[str, Any]
    external_rollback_ref_hash: str
    rollback_hash: str
    recorded_by_principal_ref: str
    rolled_back_at: datetime
    recorded_at: datetime


__all__ = [name for name in globals() if not name.startswith("_")]
