"""Public API contracts for durable enterprise integrations."""

from __future__ import annotations

import re
from datetime import datetime
from typing import Any, Literal
from uuid import UUID

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    SecretStr,
    field_validator,
    model_validator,
)

DestinationType = Literal["generic_http", "siem", "grc", "ticketing", "billing"]
CredentialKind = Literal["none", "bearer", "basic", "api_key_header", "splunk_hec"]
PayloadProfile = Literal["cloudevents", "raw"]
DeliveryStatus = Literal["pending", "leased", "retry", "delivered", "dead_letter", "cancelled"]

_EVENT_PATTERN = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9_.:-]{0,253}(?:\*)?$")
_EVENT_TYPE = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9_.:-]{0,254}$")
_HEADER_NAME = re.compile(r"^[!#$%&'*+.^_`|~0-9A-Za-z-]{1,100}$")
_BARRIER = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,254}$")
_RESERVED_HEADERS = {
    "authorization",
    "content-length",
    "content-type",
    "host",
    "idempotency-key",
    "x-lians-delivery-id",
    "x-lians-event-id",
    "x-lians-signature",
}


def _validate_patterns(values: list[str]) -> list[str]:
    normalized: list[str] = []
    for raw in values:
        value = raw.strip()
        if value == "*":
            normalized.append(value)
            continue
        if not _EVENT_PATTERN.fullmatch(value):
            raise ValueError(f"Invalid event pattern: {raw!r}")
        if "*" in value and not value.endswith(".*"):
            raise ValueError("Wildcards are only supported as '*' or a trailing '.*'")
        normalized.append(value)
    return sorted(set(normalized))


class DestinationCredentials(BaseModel):
    """Write-only destination credentials and custom header values."""

    kind: CredentialKind = "none"
    token: SecretStr | None = Field(default=None, min_length=1, max_length=8192)
    username: str | None = Field(default=None, min_length=1, max_length=512)
    password: SecretStr | None = Field(default=None, min_length=1, max_length=8192)
    header_name: str | None = Field(default=None, min_length=1, max_length=100)
    custom_headers: dict[str, SecretStr] = Field(default_factory=dict)

    @field_validator("header_name")
    @classmethod
    def validate_header_name(cls, value: str | None) -> str | None:
        if value is None:
            return None
        if not _HEADER_NAME.fullmatch(value):
            raise ValueError("header_name is not a valid HTTP header name")
        if value.casefold() in _RESERVED_HEADERS:
            raise ValueError("header_name is reserved by the delivery protocol")
        return value

    @field_validator("custom_headers")
    @classmethod
    def validate_custom_headers(cls, values: dict[str, SecretStr]) -> dict[str, SecretStr]:
        if len(values) > 20:
            raise ValueError("At most 20 custom headers are allowed")
        for name, value in values.items():
            if not _HEADER_NAME.fullmatch(name):
                raise ValueError(f"Invalid HTTP header name: {name!r}")
            if name.casefold() in _RESERVED_HEADERS:
                raise ValueError(f"Header {name!r} is reserved by the delivery protocol")
            if len(value.get_secret_value()) > 8192:
                raise ValueError(f"Header {name!r} exceeds 8192 characters")
        return values

    @model_validator(mode="after")
    def validate_kind_fields(self) -> DestinationCredentials:
        if self.kind in {"bearer", "splunk_hec"} and self.token is None:
            raise ValueError(f"token is required for credential kind {self.kind!r}")
        if self.kind == "basic" and (self.username is None or self.password is None):
            raise ValueError("username and password are required for basic credentials")
        if self.kind == "api_key_header" and (self.header_name is None or self.token is None):
            raise ValueError("header_name and token are required for api_key_header")
        if self.kind == "none" and any(
            item is not None
            for item in (self.token, self.username, self.password, self.header_name)
        ):
            raise ValueError("Authentication fields must be omitted when kind='none'")
        sensitive_values = [
            self.token.get_secret_value() if self.token else None,
            self.password.get_secret_value() if self.password else None,
            *(value.get_secret_value() for value in self.custom_headers.values()),
        ]
        if any(
            value is not None and ("\r" in value or "\n" in value) for value in sensitive_values
        ):
            raise ValueError("Credential and header values must not contain CR or LF")
        if self.username is not None and (
            ":" in self.username or "\r" in self.username or "\n" in self.username
        ):
            raise ValueError("Basic-auth username must not contain colon, CR, or LF")
        return self


class DestinationCreate(BaseModel):
    name: str = Field(min_length=1, max_length=255)
    destination_type: DestinationType
    url: str = Field(min_length=1, max_length=2048)
    event_patterns: list[str] = Field(min_length=1, max_length=100)
    payload_profile: PayloadProfile = "cloudevents"
    credentials: DestinationCredentials = Field(default_factory=DestinationCredentials)
    signing_secret: SecretStr | None = Field(default=None, min_length=32, max_length=8192)
    barrier_group: str | None = Field(default=None, max_length=255)
    max_attempts: int = Field(default=12, ge=1, le=50)
    timeout_seconds: int = Field(default=10, ge=1, le=120)
    enabled: bool = True
    description: str | None = Field(default=None, max_length=2000)

    @field_validator("name")
    @classmethod
    def normalize_name(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("name must not be blank")
        return value

    @field_validator("event_patterns")
    @classmethod
    def validate_event_patterns(cls, values: list[str]) -> list[str]:
        return _validate_patterns(values)

    @field_validator("barrier_group")
    @classmethod
    def validate_barrier(cls, value: str | None) -> str | None:
        if value is None:
            return None
        if not _BARRIER.fullmatch(value):
            raise ValueError("barrier_group contains unsupported characters")
        return value


class DestinationPatch(BaseModel):
    expected_version: int = Field(ge=1)
    name: str | None = Field(default=None, min_length=1, max_length=255)
    url: str | None = Field(default=None, min_length=1, max_length=2048)
    event_patterns: list[str] | None = Field(default=None, min_length=1, max_length=100)
    payload_profile: PayloadProfile | None = None
    max_attempts: int | None = Field(default=None, ge=1, le=50)
    timeout_seconds: int | None = Field(default=None, ge=1, le=120)
    enabled: bool | None = None
    description: str | None = Field(default=None, max_length=2000)

    @field_validator("name")
    @classmethod
    def normalize_name(cls, value: str | None) -> str | None:
        if value is None:
            return None
        value = value.strip()
        if not value:
            raise ValueError("name must not be blank")
        return value

    @field_validator("event_patterns")
    @classmethod
    def validate_event_patterns(cls, values: list[str] | None) -> list[str] | None:
        return None if values is None else _validate_patterns(values)


class DestinationSecretRotate(BaseModel):
    expected_version: int = Field(ge=1)
    credentials: DestinationCredentials | None = None
    signing_secret: SecretStr | None = Field(default=None, min_length=32, max_length=8192)


class DestinationOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    namespace: str
    barrier_group: str | None
    name: str
    destination_type: DestinationType
    url_origin: str
    url_fingerprint: str
    event_patterns: list[str]
    payload_profile: PayloadProfile
    credential_kind: CredentialKind
    credential_configured: bool
    custom_header_names: list[str]
    signing_secret_fingerprint: str
    max_attempts: int
    timeout_seconds: int
    enabled: bool
    description: str | None
    version: int
    created_at: datetime
    updated_at: datetime
    revoked_at: datetime | None
    last_success_at: datetime | None
    last_failure_at: datetime | None


class DestinationCreated(BaseModel):
    destination: DestinationOut
    signing_secret: str


class DestinationSecretRotated(BaseModel):
    destination: DestinationOut
    signing_secret: str


class IntegrationEventCreate(BaseModel):
    event_type: str = Field(min_length=1, max_length=255)
    schema_version: str = Field(default="1", min_length=1, max_length=32)
    aggregate_type: str | None = Field(default=None, max_length=100)
    aggregate_id: str | None = Field(default=None, max_length=512)
    correlation_id: str | None = Field(default=None, max_length=255)
    idempotency_key: str | None = Field(default=None, min_length=1, max_length=255)
    occurred_at: datetime | None = None
    barrier_group: str | None = Field(default=None, max_length=255)
    payload: dict[str, Any] = Field(default_factory=dict)

    @field_validator("event_type")
    @classmethod
    def validate_event_type(cls, value: str) -> str:
        value = value.strip()
        if not _EVENT_TYPE.fullmatch(value):
            raise ValueError("event_type contains unsupported characters")
        return value

    @field_validator("barrier_group")
    @classmethod
    def validate_barrier(cls, value: str | None) -> str | None:
        if value is None:
            return None
        if not _BARRIER.fullmatch(value):
            raise ValueError("barrier_group contains unsupported characters")
        return value


class IntegrationEventOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    namespace: str
    barrier_group: str | None
    event_type: str
    schema_version: str
    aggregate_type: str | None
    aggregate_id: str | None
    source_event_id: UUID | None
    correlation_id: str | None
    idempotency_key: str
    payload: dict[str, Any] | None = None
    payload_included: bool = False
    payload_hash: str
    occurred_at: datetime
    enqueued_at: datetime
    delivery_count: int = 0


class DeliveryOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    namespace: str
    barrier_group: str | None
    event_id: UUID
    destination_id: UUID
    run_sequence: int
    replayed_from_id: UUID | None
    idempotency_key: str
    status: DeliveryStatus
    attempt_count: int
    next_attempt_at: datetime
    lease_expires_at: datetime | None
    last_attempt_at: datetime | None
    delivered_at: datetime | None
    dead_lettered_at: datetime | None
    cancelled_at: datetime | None
    last_status_code: int | None
    last_error_code: str | None
    last_error_digest: str | None
    last_response_digest: str | None
    created_at: datetime
    updated_at: datetime


class DeliveryAttemptOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    delivery_id: UUID
    attempt_number: int
    worker_id: str
    outcome: Literal["delivered", "retry", "dead_letter", "cancelled", "lease_lost"]
    status_code: int | None
    error_code: str | None
    error_digest: str | None
    response_digest: str | None
    duration_ms: int
    started_at: datetime
    finished_at: datetime
    next_attempt_at: datetime | None


class DestinationTestResult(BaseModel):
    event: IntegrationEventOut
    delivery: DeliveryOut


class IntegrationReadiness(BaseModel):
    generated_at: datetime
    status: Literal["ready", "degraded", "configuration_required", "egress_disabled"]
    worker_enabled: bool
    worker_healthy: bool
    worker_last_poll_at: datetime | None
    egress_allowed: bool
    active_destinations: int
    pending_deliveries: int
    retry_deliveries: int
    leased_deliveries: int
    dead_letter_deliveries: int
    oldest_due_at: datetime | None
    disclosures: list[str]
