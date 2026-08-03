"""OpenAPI contracts for tenant-managed workload credentials."""
from __future__ import annotations

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from .barrier_policy import is_reserved_barrier_group

WorkloadRole = Literal["owner", "analyst", "compliance", "readonly"]
CredentialStatus = Literal["active", "expired", "revoked", "rotated"]


def _normalize_scopes(values: list[str]) -> list[str]:
    normalized = [value.strip() for value in values]
    if any(
        not value
        or len(value) > 100
        or not all(character.isalnum() or character in "_.:-" for character in value)
        for value in normalized
    ):
        raise ValueError("scopes must contain valid 1-100 character scope names")
    if len(normalized) != len(set(normalized)):
        raise ValueError("scopes must not contain duplicates")
    return normalized


class WorkloadCredentialCreate(BaseModel):
    """Requested grants; namespace and caller identity are never body fields."""

    label: str | None = Field(default=None, max_length=255)
    role: WorkloadRole | None = None
    scopes: list[str] = Field(default_factory=list, max_length=50)
    barrier_group: str | None = Field(
        default=None,
        pattern=r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,254}$",
    )
    ttl_seconds: int = Field(ge=60, le=31_536_000)

    @field_validator("scopes")
    @classmethod
    def validate_scopes(cls, values: list[str]) -> list[str]:
        return _normalize_scopes(values)

    @field_validator("barrier_group", mode="before")
    @classmethod
    def normalize_barrier(cls, value):
        if value is None or not isinstance(value, str):
            return value
        normalized = value.strip()
        if is_reserved_barrier_group(normalized):
            raise ValueError("This information-barrier name is reserved")
        return normalized or None

    @model_validator(mode="after")
    def require_authorization(self):
        if self.role is None and not self.scopes:
            raise ValueError("a workload credential requires a role, at least one scope, or both")
        return self


class WorkloadCredentialRotate(BaseModel):
    expected_version: int = Field(ge=1)
    ttl_seconds: int = Field(ge=60, le=31_536_000)


class WorkloadCredentialOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    namespace: str
    label: str | None
    scopes: list[str]
    effective_scopes: list[str]
    role: WorkloadRole | None
    barrier_group: str | None
    provisioning_source: Literal["tenant_oidc"]
    created_by: str
    created_at: datetime
    expires_at: datetime
    last_used_at: datetime | None
    rotated_from_id: UUID | None
    rotated_at: datetime | None
    revoked_at: datetime | None
    version: int
    status: CredentialStatus


class WorkloadCredentialCreated(WorkloadCredentialOut):
    secret: str = Field(
        description="Plaintext credential returned once; Lians stores only its SHA-256 digest."
    )
