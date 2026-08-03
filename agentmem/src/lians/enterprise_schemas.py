"""SCIM 2.0 and administrative contracts for enterprise provisioning."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from .barrier_policy import is_reserved_barrier_group


SCIM_USER_SCHEMA = "urn:ietf:params:scim:schemas:core:2.0:User"
SCIM_GROUP_SCHEMA = "urn:ietf:params:scim:schemas:core:2.0:Group"
SCIM_LIST_SCHEMA = "urn:ietf:params:scim:api:messages:2.0:ListResponse"
SCIM_PATCH_SCHEMA = "urn:ietf:params:scim:api:messages:2.0:PatchOp"
SCIM_ERROR_SCHEMA = "urn:ietf:params:scim:api:messages:2.0:Error"
SCIM_GROUP_MEMBER_LIMIT = 1_000
SCIM_GROUP_LIST_MEMBER_ROW_LIMIT = 10_000
SCIM_GROUP_LIST_RESPONSE_BYTES = 8 * 1024 * 1024
# Authorization reconciliation must never scale with an unbounded number of
# groups attached to one principal.  This is distinct from the maximum number
# of users in one Group: both directions of the membership relation have an
# independently enforced, complete-or-error contract.
SCIM_USER_GROUP_LIMIT = 1_000
SCIM_EFFECTIVE_SCOPE_LIMIT = 50
SCIM_SERVICE_PROVIDER_SCHEMA = (
    "urn:ietf:params:scim:schemas:core:2.0:ServiceProviderConfig"
)


def _clean_scopes(values: list[str]) -> list[str]:
    cleaned = sorted({value.strip() for value in values if value and value.strip()})
    for scope in cleaned:
        if len(scope) > 100 or not all(ch.isalnum() or ch in "_.:-" for ch in scope):
            raise ValueError(f"invalid scope name: {scope!r}")
    return cleaned


class ScimTenantCreate(BaseModel):
    namespace: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,254}$")
    provider_id: UUID
    subject_attribute: Literal["externalId", "userName"] = "externalId"
    enabled: bool = True
    credential_label: str | None = Field(default="primary", max_length=200)
    credential_expires_at: datetime | None = None

    @field_validator("credential_expires_at")
    @classmethod
    def future_expiry(cls, value: datetime | None) -> datetime | None:
        if value is not None:
            if value.tzinfo is None:
                raise ValueError("credential_expires_at must include a timezone")
            if value <= datetime.now(timezone.utc) + timedelta(minutes=1):
                raise ValueError("credential_expires_at must be at least one minute in the future")
        return value


class ScimTenantPatch(BaseModel):
    expected_version: int = Field(ge=1)
    enabled: bool


class ScimTenantOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    namespace: str
    provider_id: UUID
    subject_attribute: str
    enabled: bool
    version: int
    created_at: datetime
    updated_at: datetime
    revoked_at: datetime | None


class ScimTenantReconciliationOut(BaseModel):
    """Exact durable progress for one tenant-version binding snapshot."""

    id: UUID
    tenant_config_id: UUID
    namespace: str
    target_config_version: int = Field(ge=1)
    target_enabled: bool
    target_revoked_at: datetime | None
    status: Literal["pending", "running", "completed", "failed", "superseded"]
    snapshot_max_created_at: datetime | None
    snapshot_max_user_id: UUID | None
    snapshot_user_count: int = Field(ge=0)
    cursor_created_at: datetime | None
    cursor_user_id: UUID | None
    users_reconciled: int = Field(ge=0)
    pages_completed: int = Field(ge=0)
    processing_attempts: int = Field(ge=0)
    consecutive_failures: int = Field(ge=0)
    attempt_limit: int = Field(ge=1, le=100)
    next_attempt_at: datetime
    lease_expires_at: datetime | None
    heartbeat_at: datetime | None
    last_attempt_at: datetime | None
    last_error_code: str | None
    last_error_digest: str | None
    failure_code: str | None
    created_at: datetime
    started_at: datetime | None
    updated_at: datetime
    completed_at: datetime | None
    failed_at: datetime | None
    superseded_at: datetime | None
    snapshot_complete: bool
    progress_complete: bool
    completion_scope: Literal["tenant_user_created_at_id_snapshot"]


class ScimCredentialOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    tenant_config_id: UUID
    namespace: str
    token_hint: str
    label: str | None
    rotated_from_id: UUID | None
    replaced_by_id: UUID | None
    version: int
    created_at: datetime
    expires_at: datetime | None
    revoked_at: datetime | None


class ScimTenantCreated(BaseModel):
    tenant: ScimTenantOut
    credential: ScimCredentialOut
    bearer_token: str
    scim_base_path: str


class ScimCredentialRotate(BaseModel):
    expected_version: int = Field(ge=1)
    label: str | None = Field(default=None, max_length=200)
    expires_at: datetime | None = None
    revoke_prior: bool = True

    @field_validator("expires_at")
    @classmethod
    def future_expiry(cls, value: datetime | None) -> datetime | None:
        if value is not None:
            if value.tzinfo is None:
                raise ValueError("expires_at must include a timezone")
            if value <= datetime.now(timezone.utc) + timedelta(minutes=1):
                raise ValueError("expires_at must be at least one minute in the future")
        return value


class ScimCredentialCreated(BaseModel):
    credential: ScimCredentialOut
    bearer_token: str


class ScimEntitlementUpsert(BaseModel):
    expected_version: int | None = Field(default=None, ge=1)
    role: Literal["owner", "analyst", "compliance", "readonly"] | None = None
    scopes: list[str] = Field(default_factory=list, max_length=50)
    barrier_group: str | None = Field(default=None, min_length=1, max_length=255)

    @field_validator("barrier_group", mode="before")
    @classmethod
    def trim_barrier(cls, value: Any) -> Any:
        normalized = value.strip() if isinstance(value, str) else value
        if is_reserved_barrier_group(normalized):
            raise ValueError("This information-barrier name is reserved")
        return normalized

    @field_validator("scopes")
    @classmethod
    def validate_scopes(cls, value: list[str]) -> list[str]:
        return _clean_scopes(value)

    @model_validator(mode="after")
    def require_contribution(self):
        if self.role is None and not self.scopes and self.barrier_group is None:
            raise ValueError("an entitlement must contribute a role, scope, or barrier")
        return self


class ScimEntitlementOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    tenant_config_id: UUID
    namespace: str
    group_id: UUID
    role: str | None
    scopes: list[str]
    barrier_group: str | None
    version: int
    created_at: datetime
    updated_at: datetime


class ScimName(BaseModel):
    model_config = ConfigDict(extra="forbid")

    formatted: str | None = Field(default=None, max_length=1024)
    familyName: str | None = Field(default=None, max_length=512)
    givenName: str | None = Field(default=None, max_length=512)
    middleName: str | None = Field(default=None, max_length=512)
    honorificPrefix: str | None = Field(default=None, max_length=128)
    honorificSuffix: str | None = Field(default=None, max_length=128)


class ScimEmail(BaseModel):
    model_config = ConfigDict(extra="forbid")

    value: str = Field(min_length=3, max_length=512)
    type: str | None = Field(default=None, max_length=64)
    primary: bool = False
    display: str | None = Field(default=None, max_length=512)

    @field_validator("value", mode="before")
    @classmethod
    def normalize_email(cls, value: Any) -> Any:
        if not isinstance(value, str):
            return value
        value = value.strip()
        local, separator, domain = value.partition("@")
        if not separator or not local or not domain or any(ch.isspace() for ch in value):
            raise ValueError("email value must contain a valid local and domain part")
        return value


class ScimUserWrite(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schemas: list[str] = Field(default_factory=lambda: [SCIM_USER_SCHEMA])
    externalId: str | None = Field(default=None, min_length=1, max_length=512)
    userName: str = Field(min_length=1, max_length=512)
    displayName: str | None = Field(default=None, max_length=512)
    name: ScimName | None = None
    emails: list[ScimEmail] = Field(default_factory=list, max_length=50)
    active: bool = True

    @field_validator("schemas")
    @classmethod
    def validate_schema(cls, value: list[str]) -> list[str]:
        if SCIM_USER_SCHEMA not in value:
            raise ValueError(f"schemas must contain {SCIM_USER_SCHEMA}")
        return list(dict.fromkeys(value))

    @field_validator("userName", "externalId", "displayName", mode="before")
    @classmethod
    def trim_strings(cls, value: Any) -> Any:
        return value.strip() if isinstance(value, str) else value

    @field_validator("emails")
    @classmethod
    def validate_emails(cls, value: list[ScimEmail]) -> list[ScimEmail]:
        normalized = [email.value.casefold() for email in value]
        if len(normalized) != len(set(normalized)):
            raise ValueError("emails must not contain duplicate values")
        if sum(1 for email in value if email.primary) > 1:
            raise ValueError("at most one email may be primary")
        return value


class ScimMemberRef(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    value: UUID
    ref: str | None = Field(default=None, alias="$ref", max_length=2048)
    display: str | None = Field(default=None, max_length=512)
    type: Literal["User"] | None = "User"


class ScimGroupWrite(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schemas: list[str] = Field(default_factory=lambda: [SCIM_GROUP_SCHEMA])
    externalId: str | None = Field(default=None, min_length=1, max_length=512)
    displayName: str = Field(min_length=1, max_length=512)
    members: list[ScimMemberRef] = Field(
        default_factory=list,
        max_length=SCIM_GROUP_MEMBER_LIMIT,
        description=(
            "Complete desired Group membership (maximum 1,000 Users). A write "
            "also fails atomically if any User would exceed 1,000 Groups."
        ),
    )

    @field_validator("schemas")
    @classmethod
    def validate_schema(cls, value: list[str]) -> list[str]:
        if SCIM_GROUP_SCHEMA not in value:
            raise ValueError(f"schemas must contain {SCIM_GROUP_SCHEMA}")
        return list(dict.fromkeys(value))

    @field_validator("externalId", "displayName", mode="before")
    @classmethod
    def trim_strings(cls, value: Any) -> Any:
        return value.strip() if isinstance(value, str) else value

    @field_validator("members")
    @classmethod
    def unique_members(cls, value: list[ScimMemberRef]) -> list[ScimMemberRef]:
        ids = [member.value for member in value]
        if len(ids) != len(set(ids)):
            raise ValueError("members must not contain duplicates")
        return value


class ScimPatchOperation(BaseModel):
    model_config = ConfigDict(extra="forbid")

    op: Literal["add", "replace", "remove"]
    path: str | None = Field(default=None, max_length=1024)
    value: Any = None

    @field_validator("op", mode="before")
    @classmethod
    def normalize_op(cls, value: Any) -> Any:
        return value.casefold() if isinstance(value, str) else value


class ScimPatchRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schemas: list[str]
    Operations: list[ScimPatchOperation] = Field(min_length=1, max_length=100)

    @field_validator("schemas")
    @classmethod
    def validate_schema(cls, value: list[str]) -> list[str]:
        if SCIM_PATCH_SCHEMA not in value:
            raise ValueError(f"schemas must contain {SCIM_PATCH_SCHEMA}")
        return value


class ScimMeta(BaseModel):
    resourceType: Literal["User", "Group"]
    created: datetime
    lastModified: datetime
    version: str
    location: str


class ScimUserOut(BaseModel):
    schemas: list[str] = Field(default_factory=lambda: [SCIM_USER_SCHEMA])
    id: UUID
    externalId: str | None = None
    userName: str
    displayName: str | None = None
    name: dict[str, Any] = Field(default_factory=dict)
    emails: list[dict[str, Any]] = Field(default_factory=list)
    active: bool
    meta: ScimMeta


class ScimGroupOut(BaseModel):
    schemas: list[str] = Field(default_factory=lambda: [SCIM_GROUP_SCHEMA])
    id: UUID
    externalId: str | None = None
    displayName: str
    members: list[dict[str, Any]] = Field(default_factory=list)
    meta: ScimMeta


class ScimListResponse(BaseModel):
    schemas: list[str] = Field(default_factory=lambda: [SCIM_LIST_SCHEMA])
    totalResults: int
    startIndex: int
    itemsPerPage: int
    Resources: list[dict[str, Any]]


class ScimErrorBody(BaseModel):
    schemas: list[str] = Field(default_factory=lambda: [SCIM_ERROR_SCHEMA])
    status: str
    scimType: str | None = None
    detail: str
