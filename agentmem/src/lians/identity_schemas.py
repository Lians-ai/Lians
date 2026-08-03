"""Validated API contracts for OIDC provider and subject administration."""
from __future__ import annotations

from datetime import datetime
from typing import Literal
from urllib.parse import urlsplit
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from .barrier_policy import is_reserved_barrier_group


ASYMMETRIC_JWT_ALGORITHMS = frozenset(
    {"RS256", "RS384", "RS512", "PS256", "PS384", "PS512", "ES256", "ES384", "ES512", "EdDSA"}
)
BASE_REQUIRED_CLAIMS = frozenset({"iss", "sub", "aud", "exp", "iat"})


def _validate_url(value: str, *, allow_insecure_http: bool) -> str:
    parsed = urlsplit(value)
    try:
        parsed.port
    except ValueError as exc:
        raise ValueError("JWKS URI contains an invalid port") from exc
    if not parsed.hostname or parsed.username or parsed.password or parsed.fragment:
        raise ValueError("JWKS URI must be an absolute URL without userinfo or fragments")
    if parsed.scheme != "https" and not (allow_insecure_http and parsed.scheme == "http"):
        raise ValueError("JWKS URI must use HTTPS unless insecure HTTP is explicitly enabled")
    return value


def _unique_nonempty(values: list[str], label: str) -> list[str]:
    cleaned = [value.strip() for value in values if value and value.strip()]
    if not cleaned:
        raise ValueError(f"{label} must contain at least one value")
    if len(cleaned) != len(set(cleaned)):
        raise ValueError(f"{label} must not contain duplicates")
    return cleaned


class IdentityProviderCreate(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    issuer: str = Field(min_length=1, max_length=2048)
    jwks_uri: str = Field(min_length=1, max_length=2048)
    audiences: list[str] = Field(min_length=1, max_length=20)
    allowed_algorithms: list[str] = Field(default_factory=lambda: ["RS256"], min_length=1, max_length=10)
    required_claims: list[str] = Field(
        default_factory=lambda: ["iss", "sub", "aud", "exp", "iat"],
        min_length=5,
        max_length=30,
    )
    required_typ: str | None = Field(default=None, max_length=100)
    clock_skew_seconds: int = Field(default=30, ge=0, le=300)
    max_token_age_seconds: int = Field(default=900, ge=30, le=86_400)
    jwks_cache_seconds: int = Field(default=300, ge=30, le=86_400)
    allow_private_network: bool = False
    allow_insecure_http: bool = False
    enabled: bool = True

    @field_validator("issuer")
    @classmethod
    def validate_issuer(cls, value: str) -> str:
        parsed = urlsplit(value)
        try:
            parsed.port
        except ValueError as exc:
            raise ValueError("issuer contains an invalid port") from exc
        if (
            parsed.scheme != "https"
            or not parsed.hostname
            or parsed.username
            or parsed.password
            or parsed.query
            or parsed.fragment
        ):
            raise ValueError("issuer must be an HTTPS URL without userinfo, query, or fragment")
        return value.rstrip("/") if parsed.path == "/" else value

    @field_validator("audiences")
    @classmethod
    def validate_audiences(cls, value: list[str]) -> list[str]:
        return _unique_nonempty(value, "audiences")

    @field_validator("allowed_algorithms")
    @classmethod
    def validate_algorithms(cls, value: list[str]) -> list[str]:
        cleaned = _unique_nonempty(value, "allowed_algorithms")
        unsupported = set(cleaned) - ASYMMETRIC_JWT_ALGORITHMS
        if unsupported:
            raise ValueError(f"only asymmetric JWT algorithms are accepted: {sorted(unsupported)}")
        return cleaned

    @field_validator("required_claims")
    @classmethod
    def validate_required_claims(cls, value: list[str]) -> list[str]:
        cleaned = _unique_nonempty(value, "required_claims")
        missing = BASE_REQUIRED_CLAIMS - set(cleaned)
        if missing:
            raise ValueError(f"required_claims must include {sorted(missing)}")
        return cleaned

    @model_validator(mode="after")
    def validate_jwks_uri(self):
        self.jwks_uri = _validate_url(
            self.jwks_uri,
            allow_insecure_http=self.allow_insecure_http,
        )
        return self


class IdentityProviderPatch(BaseModel):
    expected_version: int = Field(ge=1)
    name: str | None = Field(default=None, min_length=1, max_length=200)
    jwks_uri: str | None = Field(default=None, min_length=1, max_length=2048)
    audiences: list[str] | None = Field(default=None, min_length=1, max_length=20)
    allowed_algorithms: list[str] | None = Field(default=None, min_length=1, max_length=10)
    required_claims: list[str] | None = Field(default=None, min_length=5, max_length=30)
    required_typ: str | None = Field(default=None, max_length=100)
    clock_skew_seconds: int | None = Field(default=None, ge=0, le=300)
    max_token_age_seconds: int | None = Field(default=None, ge=30, le=86_400)
    jwks_cache_seconds: int | None = Field(default=None, ge=30, le=86_400)
    allow_private_network: bool | None = None
    allow_insecure_http: bool | None = None
    enabled: bool | None = None

    @field_validator("audiences")
    @classmethod
    def validate_audiences(cls, value: list[str] | None) -> list[str] | None:
        return _unique_nonempty(value, "audiences") if value is not None else value

    @field_validator("allowed_algorithms")
    @classmethod
    def validate_algorithms(cls, value: list[str] | None) -> list[str] | None:
        if value is None:
            return value
        cleaned = _unique_nonempty(value, "allowed_algorithms")
        unsupported = set(cleaned) - ASYMMETRIC_JWT_ALGORITHMS
        if unsupported:
            raise ValueError(f"only asymmetric JWT algorithms are accepted: {sorted(unsupported)}")
        return cleaned

    @field_validator("required_claims")
    @classmethod
    def validate_required_claims(cls, value: list[str] | None) -> list[str] | None:
        if value is None:
            return value
        cleaned = _unique_nonempty(value, "required_claims")
        missing = BASE_REQUIRED_CLAIMS - set(cleaned)
        if missing:
            raise ValueError(f"required_claims must include {sorted(missing)}")
        return cleaned


class IdentityProviderOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    name: str
    issuer: str
    jwks_uri: str
    audiences: list[str]
    allowed_algorithms: list[str]
    required_claims: list[str]
    required_typ: str | None
    clock_skew_seconds: int
    max_token_age_seconds: int
    jwks_cache_seconds: int
    allow_private_network: bool
    allow_insecure_http: bool
    enabled: bool
    version: int
    created_at: datetime
    updated_at: datetime
    revoked_at: datetime | None


class IdentityBindingCreate(BaseModel):
    provider_id: UUID
    external_subject: str = Field(min_length=1, max_length=512)
    namespace: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,254}$")
    principal_type: Literal["human", "workload"]
    display_name: str | None = Field(default=None, max_length=255)
    role: Literal["owner", "analyst", "compliance", "readonly"] | None = None
    scopes: list[str] = Field(default_factory=list, max_length=50)
    barrier_group: str | None = Field(default=None, max_length=255)
    authorized_party: str | None = Field(default=None, max_length=512)
    enabled: bool = True

    @field_validator("barrier_group")
    @classmethod
    def validate_barrier_group(cls, value: str | None) -> str | None:
        if is_reserved_barrier_group(value):
            raise ValueError("This information-barrier name is reserved")
        return value

    @field_validator("scopes")
    @classmethod
    def validate_scopes(cls, value: list[str]) -> list[str]:
        cleaned = _unique_nonempty(value, "scopes") if value else []
        for scope in cleaned:
            if len(scope) > 100 or not all(ch.isalnum() or ch in "_.:-" for ch in scope):
                raise ValueError(f"invalid scope name: {scope!r}")
        return cleaned

    @model_validator(mode="after")
    def require_authorization(self):
        if self.role is None and not self.scopes:
            raise ValueError("a binding requires a role, at least one scope, or both")
        return self


class IdentityBindingPatch(BaseModel):
    expected_version: int = Field(ge=1)
    principal_type: Literal["human", "workload"] | None = None
    display_name: str | None = Field(default=None, max_length=255)
    role: Literal["owner", "analyst", "compliance", "readonly"] | None = None
    scopes: list[str] | None = Field(default=None, max_length=50)
    barrier_group: str | None = Field(default=None, max_length=255)
    authorized_party: str | None = Field(default=None, max_length=512)
    enabled: bool | None = None

    @field_validator("barrier_group")
    @classmethod
    def validate_barrier_group(cls, value: str | None) -> str | None:
        if is_reserved_barrier_group(value):
            raise ValueError("This information-barrier name is reserved")
        return value

    @field_validator("scopes")
    @classmethod
    def validate_scopes(cls, value: list[str] | None) -> list[str] | None:
        if value is None:
            return value
        cleaned = _unique_nonempty(value, "scopes") if value else []
        for scope in cleaned:
            if len(scope) > 100 or not all(ch.isalnum() or ch in "_.:-" for ch in scope):
                raise ValueError(f"invalid scope name: {scope!r}")
        return cleaned


class IdentityBindingOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    provider_id: UUID
    external_subject: str
    namespace: str
    principal_type: str
    display_name: str | None
    role: str | None
    scopes: list[str]
    barrier_group: str | None
    authorized_party: str | None
    enabled: bool
    version: int
    created_at: datetime
    updated_at: datetime
    revoked_at: datetime | None


class IdentityProviderProbe(BaseModel):
    provider_id: UUID
    issuer: str
    reachable: bool
    signing_key_count: int = 0
    error: str | None = None


class PrincipalOut(BaseModel):
    namespace: str
    scopes: list[str]
    barrier_group: str | None
    role: str | None = None
    principal_id: str | None
    principal_type: str | None
    auth_method: str
    credential_id: str | None
