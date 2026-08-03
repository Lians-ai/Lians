"""Fail-closed configuration for the standalone Gate mediator."""

from __future__ import annotations

import ipaddress
import json
import os
import re
import stat
from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import Literal
from urllib.parse import SplitResult, parse_qsl, urlsplit

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from ..control_schemas import (
    CANONICAL_PRINCIPAL_REF_PATTERN,
    SAFE_ACTION_PATTERN,
    _is_canonical_target_ref,
)

_MAX_CONFIG_BYTES = 1_048_576
_MAX_SECRET_BYTES = 8_192
_ROUTE_ID_PATTERN = r"^[a-z0-9](?:[a-z0-9._-]{0,62}[a-z0-9])?$"
_ENVIRONMENT_REF_PATTERN = r"^[A-Za-z0-9](?:[A-Za-z0-9._:/~-]{0,254}[A-Za-z0-9])?$"
_HEADER_NAME = re.compile(r"^[!#$%&'*+\-.^_`|~0-9A-Za-z]+$")
_MEDIA_TYPE = re.compile(r"^[a-z0-9][a-z0-9!#$&^_.+-]{0,126}/[a-z0-9][a-z0-9!#$&^_.+-]{0,126}$")
_DNS_NAME = re.compile(
    r"^(?=.{1,253}$)(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)*"
    r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?$"
)
_NON_CANONICAL_ESCAPE = re.compile(r"%(?![0-9A-F]{2})")
_AMBIGUOUS_PATH_ESCAPE = re.compile(r"%(?:00|23|2E|2F|3F|5C)")
_SENSITIVE_QUERY_KEY = re.compile(
    r"(?:^|[_-])(?:api[_-]?key|auth|authorization|credential|key|secret|signature|token)"
    r"(?:$|[_-])"
)

HOP_BY_HOP_HEADERS = frozenset(
    {
        "connection",
        "keep-alive",
        "proxy-authenticate",
        "proxy-authorization",
        "proxy-connection",
        "te",
        "trailer",
        "transfer-encoding",
        "upgrade",
    }
)
CLIENT_CONTROLLED_HEADER_DENYLIST = HOP_BY_HOP_HEADERS | frozenset(
    {
        "authorization",
        "content-length",
        "content-type",
        "cookie",
        "expect",
        "forwarded",
        "host",
        "set-cookie",
        "via",
        "x-forwarded-for",
        "x-forwarded-host",
        "x-forwarded-port",
        "x-forwarded-proto",
        "api-key",
        "x-api-key",
        "x-auth-token",
    }
)
CREDENTIAL_HEADER_DENYLIST = HOP_BY_HOP_HEADERS | frozenset(
    {
        "accept",
        "accept-encoding",
        "content-length",
        "content-type",
        "cookie",
        "expect",
        "forwarded",
        "host",
        "set-cookie",
        "via",
        "x-forwarded-for",
        "x-forwarded-host",
        "x-forwarded-port",
        "x-forwarded-proto",
    }
)


class MediatorConfigError(RuntimeError):
    """Configuration or secret material is unavailable or unsafe."""


def _reject_json_constant(_: str) -> None:
    raise ValueError("non-finite JSON values are forbidden")


def _unique_json_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    document: dict[str, object] = {}
    for key, value in pairs:
        if key in document:
            raise ValueError("duplicate JSON object keys are forbidden")
        document[key] = value
    return document


def _absolute_file_reference(value: str, label: str) -> str:
    if (
        not value
        or "\x00" in value
        or "~" in value
        or not (PurePosixPath(value).is_absolute() or PureWindowsPath(value).is_absolute())
    ):
        raise ValueError(f"{label} must be an absolute file path")
    return value


def _canonical_header_name(value: str, label: str) -> str:
    normalized = value.strip().lower()
    if not normalized or len(normalized) > 128 or _HEADER_NAME.fullmatch(normalized) is None:
        raise ValueError(f"{label} is not a valid HTTP header name")
    return normalized


def _safe_header_value(value: str, label: str) -> str:
    if (
        not value.isascii()
        or len(value) > 2_048
        or any(ord(character) < 32 or ord(character) == 127 for character in value)
    ):
        raise ValueError(f"{label} must contain only visible ASCII characters")
    return value


def _parse_exact_https_url(value: str, *, allow_query: bool) -> SplitResult:
    if (
        not value.isascii()
        or len(value) > 2_048
        or any(character.isspace() or ord(character) < 32 for character in value)
        or "\\" in value
    ):
        raise ValueError("URL must be a bounded ASCII HTTPS URL")
    parsed = urlsplit(value)
    if parsed.scheme != "https" or not parsed.netloc or parsed.fragment:
        raise ValueError("URL must use HTTPS and must not contain a fragment")
    if parsed.username is not None or parsed.password is not None:
        raise ValueError("URL user information is forbidden")
    if not allow_query and parsed.query:
        raise ValueError("control-plane base URL must not contain a query")
    if _NON_CANONICAL_ESCAPE.search(parsed.path) or _NON_CANONICAL_ESCAPE.search(parsed.query):
        raise ValueError("percent escapes must use uppercase hexadecimal")
    if _AMBIGUOUS_PATH_ESCAPE.search(parsed.path):
        raise ValueError("encoded separators and dot segments are forbidden")
    if any(
        _SENSITIVE_QUERY_KEY.search(key.lower()) is not None
        for key, _ in parse_qsl(parsed.query, keep_blank_values=True, strict_parsing=False)
    ):
        raise ValueError("credentials and signatures are forbidden in URL queries")

    hostname = parsed.hostname
    if hostname is None or hostname.endswith(".") or _DNS_NAME.fullmatch(hostname) is None:
        raise ValueError("URL hostname must be a canonical lowercase ASCII DNS name")
    try:
        ipaddress.ip_address(hostname)
    except ValueError:
        pass
    else:
        raise ValueError("IP-literal destinations are forbidden")
    try:
        port = parsed.port
    except ValueError as exc:
        raise ValueError("URL port is invalid") from exc
    if port is not None and not 1 <= port <= 65_535:
        raise ValueError("URL port is invalid")
    canonical_authority = hostname if port in {None, 443} else f"{hostname}:{port}"
    if parsed.netloc != canonical_authority:
        raise ValueError(
            "URL authority must be canonical lowercase DNS without a default-port alias"
        )
    if not parsed.path.startswith("/") or not parsed.path or parsed.path.startswith("//"):
        raise ValueError("URL must contain an absolute path")
    if any(segment in {".", ".."} for segment in parsed.path.split("/")):
        raise ValueError("URL path traversal segments are forbidden")
    return parsed


def _validated_cidrs(values: list[str], label: str) -> list[str]:
    if len(values) > 32:
        raise ValueError(f"{label} may contain at most 32 networks")
    normalized: list[str] = []
    for value in values:
        try:
            network = ipaddress.ip_network(value, strict=True)
        except ValueError as exc:
            raise ValueError(f"{label} contains an invalid strict CIDR") from exc
        if network.prefixlen == 0:
            raise ValueError(f"{label} may not contain a world-open network")
        candidate = str(network)
        if candidate in normalized:
            raise ValueError(f"{label} must not contain duplicate networks")
        normalized.append(candidate)
    return normalized


class TLSFiles(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    ca_file: str | None = None
    client_certificate_file: str | None = None
    client_private_key_file: str | None = None

    @field_validator("ca_file", "client_certificate_file", "client_private_key_file")
    @classmethod
    def _absolute_files(cls, value: str | None, info):
        if value is None:
            return None
        return _absolute_file_reference(value, info.field_name)

    @model_validator(mode="after")
    def _client_pair(self):
        if bool(self.client_certificate_file) != bool(self.client_private_key_file):
            raise ValueError("client certificate and private key files must be configured together")
        return self


class IngressTLSFiles(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    certificate_file: str
    private_key_file: str
    client_ca_file: str | None = None
    require_client_certificate: bool = False

    @field_validator("certificate_file", "private_key_file", "client_ca_file")
    @classmethod
    def _absolute_files(cls, value: str | None, info):
        if value is None:
            return None
        return _absolute_file_reference(value, info.field_name)

    @model_validator(mode="after")
    def _client_ca_required(self):
        if self.require_client_certificate and self.client_ca_file is None:
            raise ValueError("client_ca_file is required when client certificates are mandatory")
        return self


class GateControlPlaneConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    base_url: str
    api_key_file: str
    allowed_ip_cidrs: list[str] = Field(min_length=1, max_length=32)
    tls: TLSFiles = Field(default_factory=TLSFiles)
    timeout_seconds: float = Field(default=5.0, ge=0.5, le=30.0)

    @field_validator("base_url")
    @classmethod
    def _exact_base_url(cls, value: str) -> str:
        candidate = f"{value}/" if urlsplit(value).path == "" else value
        parsed = _parse_exact_https_url(candidate, allow_query=False)
        if parsed.path != "/":
            raise ValueError("control-plane base URL must end at the origin root")
        return candidate[:-1]

    @field_validator("api_key_file")
    @classmethod
    def _api_key_path(cls, value: str) -> str:
        return _absolute_file_reference(value, "api_key_file")

    @field_validator("allowed_ip_cidrs")
    @classmethod
    def _gate_cidrs(cls, values: list[str]) -> list[str]:
        return _validated_cidrs(values, "gate.allowed_ip_cidrs")


class UpstreamCredentialConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    header_name: str
    secret_file: str
    binding_ref: str = Field(pattern=_ENVIRONMENT_REF_PATTERN)
    value_prefix: str = "Bearer "

    @field_validator("header_name")
    @classmethod
    def _credential_header(cls, value: str) -> str:
        normalized = _canonical_header_name(value, "credential.header_name")
        if normalized in CREDENTIAL_HEADER_DENYLIST:
            raise ValueError("credential header cannot control HTTP framing or routing")
        return normalized

    @field_validator("secret_file")
    @classmethod
    def _secret_path(cls, value: str) -> str:
        return _absolute_file_reference(value, "credential.secret_file")

    @field_validator("value_prefix")
    @classmethod
    def _prefix(cls, value: str) -> str:
        if (
            len(value) > 64
            or not value.isascii()
            or any(ord(character) < 32 or ord(character) == 127 for character in value)
        ):
            raise ValueError("credential value_prefix must be short visible ASCII")
        if value and not value.endswith(" "):
            raise ValueError("nonempty credential value_prefix must end with one space")
        return value


class MediatorRouteConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    route_id: str = Field(pattern=_ROUTE_ID_PATTERN)
    route_version: str = Field(min_length=1, max_length=64, pattern=_ENVIRONMENT_REF_PATTERN)
    action: str = Field(min_length=1, max_length=255, pattern=SAFE_ACTION_PATTERN)
    target_ref: str = Field(min_length=1, max_length=2_048)
    target_binding: Literal["fixed-route-authority-v1"]
    upstream_url: str
    method: Literal["POST", "PUT", "PATCH", "DELETE"]
    request_content_type: str = "application/json"
    request_contract_ref: str = Field(
        min_length=1, max_length=255, pattern=_ENVIRONMENT_REF_PATTERN
    )
    allowed_json_top_level_fields: list[str] | None = Field(
        default=None, min_length=1, max_length=256
    )
    required_json_top_level_fields: list[str] = Field(default_factory=list, max_length=256)
    max_json_depth: int = Field(default=16, ge=1, le=32)
    max_json_nodes: int = Field(default=10_000, ge=1, le=100_000)
    response_content_types: list[str] = Field(
        default_factory=lambda: ["application/json"], min_length=1, max_length=16
    )
    max_request_bytes: int = Field(default=262_144, ge=1, le=2_097_152)
    max_response_bytes: int = Field(default=1_048_576, ge=0, le=8_388_608)
    timeout_seconds: float = Field(default=10.0, ge=0.5, le=30.0)
    allowed_ip_cidrs: list[str] = Field(default_factory=list, max_length=32)
    fixed_headers: dict[str, str] = Field(default_factory=dict)
    credential: UpstreamCredentialConfig
    idempotency_header_name: str | None = None
    audit_correlation_header_name: str = "x-lians-audit-correlation"
    tls_identity_ref: str | None = Field(
        default=None, min_length=1, max_length=255, pattern=_ENVIRONMENT_REF_PATTERN
    )
    tls: TLSFiles = Field(default_factory=TLSFiles)

    @field_validator("target_ref")
    @classmethod
    def _canonical_target(cls, value: str) -> str:
        if not _is_canonical_target_ref(value):
            raise ValueError("target_ref must be a canonical ASCII absolute resource URI")
        return value

    @field_validator("upstream_url")
    @classmethod
    def _exact_upstream(cls, value: str) -> str:
        _parse_exact_https_url(value, allow_query=True)
        return value

    @field_validator("request_content_type")
    @classmethod
    def _request_type(cls, value: str) -> str:
        normalized = value.strip().lower()
        if _MEDIA_TYPE.fullmatch(normalized) is None:
            raise ValueError("request_content_type must be one exact media type without parameters")
        return normalized

    @field_validator("response_content_types")
    @classmethod
    def _response_types(cls, values: list[str]) -> list[str]:
        normalized = [value.strip().lower() for value in values]
        if any(_MEDIA_TYPE.fullmatch(value) is None for value in normalized):
            raise ValueError("response_content_types must contain exact media types")
        if len(normalized) != len(set(normalized)):
            raise ValueError("response_content_types must not contain duplicates")
        return normalized

    @field_validator("allowed_json_top_level_fields", "required_json_top_level_fields")
    @classmethod
    def _json_fields(cls, values: list[str] | None, info):
        if values is None:
            return None
        normalized = [value.strip() for value in values]
        if any(
            not value
            or len(value) > 128
            or not value.isascii()
            or re.fullmatch(r"[A-Za-z0-9_.-]+", value) is None
            for value in normalized
        ):
            raise ValueError(f"{info.field_name} contain an invalid field name")
        if len(normalized) != len(set(normalized)):
            raise ValueError(f"{info.field_name} must not contain duplicates")
        return sorted(normalized)

    @field_validator("allowed_ip_cidrs")
    @classmethod
    def _route_cidrs(cls, values: list[str]) -> list[str]:
        return sorted(_validated_cidrs(values, "route.allowed_ip_cidrs"))

    @field_validator("timeout_seconds")
    @classmethod
    def _millisecond_timeout(cls, value: float) -> float:
        return round(value, 3)

    @field_validator("fixed_headers")
    @classmethod
    def _fixed_headers(cls, values: dict[str, str]) -> dict[str, str]:
        if len(values) > 16:
            raise ValueError("fixed_headers may contain at most 16 entries")
        normalized: dict[str, str] = {}
        for name, value in values.items():
            header = _canonical_header_name(name, "fixed header")
            if header in CLIENT_CONTROLLED_HEADER_DENYLIST or header in {
                "accept",
                "accept-encoding",
            }:
                raise ValueError("fixed_headers cannot control credentials, routing, or framing")
            if header in normalized:
                raise ValueError("fixed_headers contain a case-insensitive duplicate")
            normalized[header] = _safe_header_value(value, f"fixed header {header}")
        if sum(len(name) + len(value) + 4 for name, value in normalized.items()) > 8_192:
            raise ValueError("fixed_headers exceed the aggregate byte limit")
        return normalized

    @field_validator("idempotency_header_name", "audit_correlation_header_name")
    @classmethod
    def _mediator_headers(cls, value: str | None, info):
        if value is None:
            return None
        normalized = _canonical_header_name(value, info.field_name)
        if normalized in CLIENT_CONTROLLED_HEADER_DENYLIST | {"accept", "accept-encoding"}:
            raise ValueError(f"{info.field_name} cannot control routing, credentials, or framing")
        return normalized

    @model_validator(mode="after")
    def _distinct_headers_and_global_allowlist(self):
        if not self.route_id.endswith(f".{self.route_version}"):
            raise ValueError("route_id must end with its immutable .route_version suffix")
        if self.credential.header_name in {
            "accept",
            "accept-encoding",
            self.audit_correlation_header_name,
        }:
            raise ValueError("credential header collides with another mediator-owned header")
        reserved = {
            self.credential.header_name,
            self.audit_correlation_header_name,
            "accept",
            "accept-encoding",
            "content-length",
            "content-type",
            "host",
        }
        if self.idempotency_header_name:
            if self.idempotency_header_name in reserved:
                raise ValueError("idempotency header collides with another mediator-owned header")
            reserved.add(self.idempotency_header_name)
        if any(name in reserved for name in self.fixed_headers):
            raise ValueError("fixed header collides with another mediator-owned header")
        if self.tls.client_certificate_file and self.tls_identity_ref is None:
            raise ValueError("tls_identity_ref is required for a provider mTLS identity")
        if self.tls_identity_ref is not None and not self.tls.client_certificate_file:
            raise ValueError("tls_identity_ref requires a provider client certificate")
        is_json = (
            self.request_content_type == "application/json"
            or self.request_content_type.endswith("+json")
        )
        if is_json and self.allowed_json_top_level_fields is None:
            raise ValueError("JSON routes require an explicit top-level field allowlist")
        if not is_json and (
            self.allowed_json_top_level_fields is not None or self.required_json_top_level_fields
        ):
            raise ValueError("JSON field contracts require a JSON request content type")
        if self.allowed_json_top_level_fields is not None and not set(
            self.required_json_top_level_fields
        ).issubset(self.allowed_json_top_level_fields):
            raise ValueError("required JSON fields must be a subset of allowed fields")
        for value in self.allowed_ip_cidrs:
            network = ipaddress.ip_network(value)
            if not network.is_global:
                raise ValueError(
                    "upstream allowed_ip_cidrs may contain only globally routed networks"
                )
        return self

    def security_manifest(self) -> dict[str, object]:
        """Return the non-secret immutable provider request contract."""
        return {
            "route_id": self.route_id,
            "route_version": self.route_version,
            "action": self.action,
            "target_ref": self.target_ref,
            "target_binding": self.target_binding,
            "method": self.method,
            "upstream_url": self.upstream_url,
            "request_content_type": self.request_content_type,
            "request_contract_ref": self.request_contract_ref,
            "allowed_json_top_level_fields": self.allowed_json_top_level_fields,
            "required_json_top_level_fields": list(self.required_json_top_level_fields),
            "max_json_depth": self.max_json_depth,
            "max_json_nodes": self.max_json_nodes,
            "response_content_types": list(self.response_content_types),
            "max_request_bytes": self.max_request_bytes,
            "max_response_bytes": self.max_response_bytes,
            "timeout_milliseconds": round(self.timeout_seconds * 1_000),
            "allowed_ip_cidrs": list(self.allowed_ip_cidrs),
            "fixed_headers": dict(sorted(self.fixed_headers.items())),
            "credential_binding_ref": self.credential.binding_ref,
            "credential_header_name": self.credential.header_name,
            "credential_value_prefix": self.credential.value_prefix,
            "idempotency_header_name": self.idempotency_header_name,
            "idempotency_strategy": (
                "route-and-decision-v1" if self.idempotency_header_name else None
            ),
            "audit_correlation_header_name": self.audit_correlation_header_name,
            "audit_correlation_security_semantics": "non-authorizing-permit-id",
            "tls_identity_ref": self.tls_identity_ref,
        }


class MediatorConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal[1]
    server_tls: IngressTLSFiles
    gate: GateControlPlaneConfig
    expected_mediator_principal_id: str = Field(
        pattern=CANONICAL_PRINCIPAL_REF_PATTERN, min_length=1, max_length=512
    )
    expected_namespace: str = Field(
        min_length=1,
        max_length=255,
        pattern=r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,254}$",
    )
    expected_barrier_group: str = Field(min_length=1, max_length=255)
    caller_token_file: str
    metrics_bearer_token_file: str
    max_in_flight: int = Field(default=32, ge=1, le=512)
    queue_timeout_seconds: float = Field(default=0.25, ge=0.01, le=5.0)
    dns_timeout_seconds: float = Field(default=2.0, ge=0.1, le=10.0)
    identity_recheck_seconds: int = Field(default=60, ge=10, le=300)
    routes: list[MediatorRouteConfig] = Field(min_length=1, max_length=100)

    @field_validator("caller_token_file", "metrics_bearer_token_file")
    @classmethod
    def _service_token_path(cls, value: str, info) -> str:
        return _absolute_file_reference(value, info.field_name)

    @field_validator("expected_mediator_principal_id")
    @classmethod
    def _api_key_mediator_identity(cls, value: str) -> str:
        if not value.startswith("lians:principal:v1:api-key:"):
            raise ValueError(
                "the file-backed mediator credential must resolve to an API-key principal"
            )
        return value

    @field_validator("expected_barrier_group")
    @classmethod
    def _dedicated_barrier_scope(cls, value: str) -> str:
        if not value.isprintable() or "\r" in value or "\n" in value:
            raise ValueError("expected_barrier_group must be a printable dedicated scope")
        return value

    @model_validator(mode="after")
    def _unique_routes(self):
        route_ids = [route.route_id for route in self.routes]
        if len(route_ids) != len(set(route_ids)):
            raise ValueError("route_id values must be unique")
        return self

    def route_map(self) -> dict[str, MediatorRouteConfig]:
        return {route.route_id: route for route in self.routes}


def load_mediator_config(path: str | os.PathLike[str]) -> MediatorConfig:
    """Load one bounded JSON config file without environment interpolation."""
    config_path = Path(path)
    if not config_path.is_absolute():
        raise MediatorConfigError("mediator configuration path must be absolute")
    try:
        metadata = config_path.stat()
        if not stat.S_ISREG(metadata.st_mode):
            raise MediatorConfigError("mediator configuration is not a regular file")
        if os.name != "nt" and metadata.st_mode & (stat.S_IWGRP | stat.S_IWOTH):
            raise MediatorConfigError("mediator configuration must not be group/world writable")
        with config_path.open("rb") as stream:
            raw = stream.read(_MAX_CONFIG_BYTES + 1)
    except MediatorConfigError:
        raise
    except OSError as exc:
        raise MediatorConfigError("mediator configuration is unreadable") from exc
    if not raw or len(raw) > _MAX_CONFIG_BYTES:
        raise MediatorConfigError("mediator configuration size is invalid")
    try:
        document = json.loads(
            raw.decode("utf-8"),
            object_pairs_hook=_unique_json_object,
            parse_constant=_reject_json_constant,
        )
        return MediatorConfig.model_validate(document)
    except (UnicodeDecodeError, json.JSONDecodeError, ValueError) as exc:
        raise MediatorConfigError("mediator configuration is invalid") from exc


def read_header_secret(path: str, *, minimum_bytes: int) -> str:
    """Read a rotating header secret without trimming or rendering it."""
    secret_path = Path(path)
    try:
        metadata = secret_path.stat()
        if not stat.S_ISREG(metadata.st_mode):
            raise MediatorConfigError("a mediator secret reference is not a regular file")
        if os.name != "nt" and metadata.st_mode & (stat.S_IROTH | stat.S_IWGRP | stat.S_IWOTH):
            raise MediatorConfigError("a mediator secret file has unsafe permissions")
        with secret_path.open("rb") as stream:
            raw = stream.read(_MAX_SECRET_BYTES + 1)
    except MediatorConfigError:
        raise
    except OSError as exc:
        raise MediatorConfigError("a mediator secret file is unreadable") from exc
    if not minimum_bytes <= len(raw) <= _MAX_SECRET_BYTES:
        raise MediatorConfigError("a mediator secret file has an invalid size")
    if any(byte < 33 or byte > 126 for byte in raw):
        raise MediatorConfigError(
            "a mediator header secret must be visible ASCII without whitespace"
        )
    return raw.decode("ascii")
