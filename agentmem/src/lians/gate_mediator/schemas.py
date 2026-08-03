"""Private wire contracts for the standalone mediator."""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, SecretStr, field_validator, model_validator

from ..control_schemas import CANONICAL_PRINCIPAL_REF_PATTERN, SAFE_ACTION_PATTERN


class PreparedExecution(BaseModel):
    model_config = ConfigDict(extra="forbid")

    route_id: str
    route_config_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    canonicalization: str
    enforcement_principal_id: str = Field(pattern=CANONICAL_PRINCIPAL_REF_PATTERN)
    action: str = Field(pattern=SAFE_ACTION_PATTERN)
    target_ref: str
    decision_id: UUID
    request_body_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    request_body_bytes: int = Field(ge=0)
    execution_request_hash: str = Field(pattern=r"^[0-9a-f]{64}$")


class PresentedExecutionPermit(BaseModel):
    """Complete issuance claims received in fixed, redacted ingress headers."""

    model_config = ConfigDict(extra="forbid")

    permit_id: UUID
    enforcement_principal_id: str = Field(pattern=CANONICAL_PRINCIPAL_REF_PATTERN)
    action: str = Field(min_length=1, max_length=255, pattern=SAFE_ACTION_PATTERN)
    target_ref: str = Field(min_length=1, max_length=2_048)
    decision_id: UUID
    execution_request_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    issued_at: datetime
    expires_at: datetime
    token: SecretStr = Field(repr=False)

    @field_validator("token")
    @classmethod
    def _permit_token(cls, value: SecretStr) -> SecretStr:
        token = value.get_secret_value()
        import re

        if re.fullmatch(r"lians_permit_v1_[A-Za-z0-9_-]{43}", token) is None:
            raise ValueError("invalid permit")
        return value

    @model_validator(mode="after")
    def _time_order(self):
        if self.issued_at.tzinfo is None or self.expires_at.tzinfo is None:
            raise ValueError("permit timestamps must be timezone-aware")
        if self.expires_at <= self.issued_at:
            raise ValueError("permit expiry is invalid")
        return self


class GatePrincipal(BaseModel):
    model_config = ConfigDict(extra="ignore")

    namespace: str
    scopes: list[str]
    barrier_group: str | None
    principal_id: str | None
    auth_method: str


class GateConsumptionReceipt(BaseModel):
    model_config = ConfigDict(extra="ignore")

    id: UUID
    permit_id: UUID
    evaluation_id: UUID
    decision_id: UUID
    consuming_principal_id: str
    action: str
    target_ref: str
    execution_request_hash: str
    consumed_at: datetime
    consumption_hash: str = Field(pattern=r"^[0-9a-f]{64}$")


class ProviderDispatchResult(BaseModel):
    model_config = ConfigDict(arbitrary_types_allowed=True)

    status_code: int = Field(ge=100, le=599)
    content_type: str | None
    body: bytes
