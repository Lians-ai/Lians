"""Fail-closed OIDC verification and subject-to-tenant authorization binding."""
from __future__ import annotations

import asyncio
import hashlib
import ipaddress
import json
import socket
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any
from urllib.parse import urlsplit, urlunsplit
from uuid import UUID

import httpx
import jwt
from jwt import PyJWK
from sqlalchemy import and_, select
from sqlalchemy.ext.asyncio import AsyncSession

from .auth_lookup import AuthLookupInvariantError, lookup_identity_binding
from .authz import effective_scopes, oidc_principal_ref
from .barrier_policy import is_reserved_barrier_group
from .identity_models import TrustedIdentityProvider

_MAX_BEARER_BYTES = 131_072
_MAX_JWKS_BYTES = 1_048_576
_MAX_JWKS_KEYS = 100
_HTTP_TIMEOUT_SECONDS = 5.0
_UNKNOWN_KID_REFRESH_COOLDOWN_SECONDS = 10.0

_ALGORITHM_KEY_TYPES = {
    "RS256": "RSA",
    "RS384": "RSA",
    "RS512": "RSA",
    "PS256": "RSA",
    "PS384": "RSA",
    "PS512": "RSA",
    "ES256": "EC",
    "ES384": "EC",
    "ES512": "EC",
    "EdDSA": "OKP",
}
_ALGORITHM_CURVES = {
    "ES256": "P-256",
    "ES384": "P-384",
    "ES512": "P-521",
}


class IdentityAuthenticationError(Exception):
    """An intentionally non-sensitive authentication failure."""

    def __init__(self, code: str, message: str = "Bearer token authentication failed"):
        super().__init__(message)
        self.code = code


class IdentityProviderFetchError(Exception):
    """A trusted provider's key material could not be obtained safely."""


@dataclass(frozen=True)
class ProviderConfig:
    id: UUID
    issuer: str
    jwks_uri: str
    audiences: tuple[str, ...]
    allowed_algorithms: frozenset[str]
    required_claims: tuple[str, ...]
    required_typ: str | None
    clock_skew_seconds: int
    max_token_age_seconds: int
    jwks_cache_seconds: int
    allow_private_network: bool
    allow_insecure_http: bool

    @classmethod
    def from_row(cls, row: TrustedIdentityProvider) -> "ProviderConfig":
        return cls(
            id=row.id,
            issuer=row.issuer,
            jwks_uri=row.jwks_uri,
            audiences=tuple(row.audiences or ()),
            allowed_algorithms=frozenset(row.allowed_algorithms or ()),
            required_claims=tuple(row.required_claims or ()),
            required_typ=row.required_typ,
            clock_skew_seconds=row.clock_skew_seconds,
            max_token_age_seconds=row.max_token_age_seconds,
            jwks_cache_seconds=row.jwks_cache_seconds,
            allow_private_network=row.allow_private_network,
            allow_insecure_http=row.allow_insecure_http,
        )


@dataclass(frozen=True)
class FederatedPrincipal:
    namespace: str
    scopes: list[str]
    barrier_group: str | None
    role: str | None
    principal_id: str
    principal_type: str
    credential_id: str
    provider_id: UUID


@dataclass(frozen=True)
class _VerifiedKey:
    kid: str
    jwk: dict[str, Any]
    key: Any


@dataclass(frozen=True)
class _JwksEntry:
    expires_at: float
    configuration_fingerprint: str
    keys: dict[str, _VerifiedKey]


_jwks_cache: dict[UUID, _JwksEntry] = {}
_jwks_locks: dict[UUID, asyncio.Lock] = {}
_jwks_forced_refresh_attempts: dict[UUID, tuple[str, float]] = {}


def clear_jwks_cache(provider_id: UUID) -> None:
    """Immediately discard keys after a provider policy change or revocation."""
    _jwks_cache.pop(provider_id, None)
    _jwks_forced_refresh_attempts.pop(provider_id, None)


def _configuration_fingerprint(config: ProviderConfig) -> str:
    material = json.dumps(
        {
            "jwks_uri": config.jwks_uri,
            "algorithms": sorted(config.allowed_algorithms),
            "cache_seconds": config.jwks_cache_seconds,
            "allow_private_network": config.allow_private_network,
            "allow_insecure_http": config.allow_insecure_http,
        },
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(material.encode("utf-8")).hexdigest()


async def _resolve_safe_network_target(
    config: ProviderConfig,
) -> tuple[str, int, list[ipaddress.IPv4Address | ipaddress.IPv6Address]]:
    parsed = urlsplit(config.jwks_uri)
    allowed_schemes = {"https"}
    if config.allow_insecure_http:
        allowed_schemes.add("http")
    if (
        parsed.scheme not in allowed_schemes
        or not parsed.hostname
        or parsed.username
        or parsed.password
        or parsed.fragment
    ):
        raise IdentityProviderFetchError("provider has an unsafe JWKS URI")

    raw_host = parsed.hostname.casefold().rstrip(".")
    try:
        literal = ipaddress.ip_address(raw_host)
        host = raw_host
    except ValueError:
        try:
            host = raw_host.encode("idna").decode("ascii")
        except UnicodeError as exc:
            raise IdentityProviderFetchError(
                "provider has an invalid hostname"
            ) from exc
        literal = None
    port = parsed.port or (443 if parsed.scheme == "https" else 80)
    if literal is not None:
        addresses = [
            (
                socket.AF_INET6 if literal.version == 6 else socket.AF_INET,
                socket.SOCK_STREAM,
                6,
                "",
                (literal.compressed, port),
            )
        ]
    else:
        try:
            addresses = await asyncio.wait_for(
                asyncio.get_running_loop().getaddrinfo(
                    host,
                    port,
                    type=socket.SOCK_STREAM,
                ),
                timeout=_HTTP_TIMEOUT_SECONDS,
            )
        except (OSError, TimeoutError) as exc:
            raise IdentityProviderFetchError(
                "provider hostname could not be resolved"
            ) from exc

    if not addresses:
        raise IdentityProviderFetchError("provider hostname returned no addresses")
    resolved: set[ipaddress.IPv4Address | ipaddress.IPv6Address] = set()
    for info in addresses:
        raw_address = str(info[4][0]).split("%", 1)[0]
        try:
            address = ipaddress.ip_address(raw_address)
        except ValueError as exc:
            raise IdentityProviderFetchError("provider returned an invalid address") from exc
        if (
            address.is_loopback
            or address.is_link_local
            or address.is_multicast
            or address.is_unspecified
            or address.is_reserved
        ):
            raise IdentityProviderFetchError(
                "provider resolves to a prohibited network address"
            )
        if address.is_private and not config.allow_private_network:
            raise IdentityProviderFetchError(
                "provider resolves to a non-public address without explicit approval"
            )
        if not (
            address.is_global
            or (config.allow_private_network and address.is_private)
        ):
            raise IdentityProviderFetchError(
                "provider resolves to a prohibited network address"
            )
        resolved.add(address)
    return host, port, sorted(
        resolved,
        key=lambda address: (address.version, int(address)),
    )


async def _assert_safe_network_target(config: ProviderConfig) -> None:
    """Compatibility validation wrapper used by focused callers/tests."""
    await _resolve_safe_network_target(config)


def _pinned_jwks_url(
    config: ProviderConfig,
    host: str,
    port: int,
    address: ipaddress.IPv4Address | ipaddress.IPv6Address,
) -> tuple[str, str]:
    parsed = urlsplit(config.jwks_uri)
    default_port = 443 if parsed.scheme.casefold() == "https" else 80
    ip_host = f"[{address.compressed}]" if address.version == 6 else address.compressed
    pinned_authority = ip_host if port == default_port else f"{ip_host}:{port}"
    host_authority = host if ":" not in host else f"[{host}]"
    if port != default_port:
        host_authority = f"{host_authority}:{port}"
    return (
        urlunsplit(
            (
                parsed.scheme.casefold(),
                pinned_authority,
                parsed.path or "/",
                parsed.query,
                "",
            )
        ),
        host_authority,
    )


async def _download_jwks(config: ProviderConfig) -> dict[str, _VerifiedKey]:
    host, port, addresses = await _resolve_safe_network_target(config)
    pinned_url, host_authority = _pinned_jwks_url(
        config, host, port, addresses[0]
    )
    timeout = httpx.Timeout(_HTTP_TIMEOUT_SECONDS)
    limits = httpx.Limits(max_connections=4, max_keepalive_connections=2)
    try:
        async with httpx.AsyncClient(
            follow_redirects=False,
            timeout=timeout,
            limits=limits,
            trust_env=False,
        ) as client:
            async with client.stream(
                "GET",
                pinned_url,
                headers={
                    "Accept": "application/jwk-set+json, application/json",
                    "Host": host_authority,
                },
                extensions={"sni_hostname": host},
            ) as response:
                if response.status_code != 200:
                    raise IdentityProviderFetchError(
                        f"provider returned HTTP {response.status_code}"
                    )
                chunks: list[bytes] = []
                received = 0
                async for chunk in response.aiter_bytes():
                    received += len(chunk)
                    if received > _MAX_JWKS_BYTES:
                        raise IdentityProviderFetchError("provider JWKS exceeds the size limit")
                    chunks.append(chunk)
    except IdentityProviderFetchError:
        raise
    except (httpx.HTTPError, OSError) as exc:
        raise IdentityProviderFetchError("provider JWKS request failed") from exc

    try:
        document = json.loads(b"".join(chunks))
    except (UnicodeDecodeError, json.JSONDecodeError, RecursionError, MemoryError) as exc:
        raise IdentityProviderFetchError("provider JWKS is not valid JSON") from exc
    raw_keys = document.get("keys") if isinstance(document, dict) else None
    if not isinstance(raw_keys, list) or not raw_keys or len(raw_keys) > _MAX_JWKS_KEYS:
        raise IdentityProviderFetchError("provider JWKS has an invalid key set")

    keys: dict[str, _VerifiedKey] = {}
    for raw in raw_keys:
        if not isinstance(raw, dict):
            continue
        kid = raw.get("kid")
        if not isinstance(kid, str) or not kid or len(kid) > 512:
            continue
        if kid in keys:
            raise IdentityProviderFetchError("provider JWKS contains duplicate key IDs")
        if raw.get("use") not in (None, "sig"):
            continue
        key_ops = raw.get("key_ops")
        if key_ops is not None and (
            not isinstance(key_ops, list) or "verify" not in key_ops
        ):
            continue
        try:
            parsed = PyJWK.from_dict(raw)
        except (KeyError, ValueError, TypeError, jwt.PyJWTError):
            continue
        keys[kid] = _VerifiedKey(kid=kid, jwk=raw, key=parsed.key)
    if not keys:
        raise IdentityProviderFetchError("provider JWKS has no usable signing keys")
    return keys


async def _get_key_set(
    config: ProviderConfig,
    *,
    force: bool = False,
    force_cooldown_seconds: float | None = None,
) -> dict[str, _VerifiedKey]:
    now = time.monotonic()
    fingerprint = _configuration_fingerprint(config)
    cached = _jwks_cache.get(config.id)
    if (
        not force
        and cached is not None
        and cached.configuration_fingerprint == fingerprint
        and cached.expires_at > now
    ):
        return cached.keys

    lock = _jwks_locks.setdefault(config.id, asyncio.Lock())
    async with lock:
        now = time.monotonic()
        cached = _jwks_cache.get(config.id)
        if force and force_cooldown_seconds is not None and cached is not None:
            prior_attempt = _jwks_forced_refresh_attempts.get(config.id)
            if (
                cached.configuration_fingerprint == fingerprint
                and prior_attempt is not None
                and prior_attempt[0] == fingerprint
                and now - prior_attempt[1] < force_cooldown_seconds
            ):
                # An attacker can manufacture arbitrary kid values. Coalesce
                # their cache-miss refreshes per provider while still allowing
                # explicit administrative probes to bypass this cooldown.
                return cached.keys
        if (
            not force
            and cached is not None
            and cached.configuration_fingerprint == fingerprint
            and cached.expires_at > now
        ):
            return cached.keys
        if force and force_cooldown_seconds is not None:
            # Record attempts, not just successes. A failing or unavailable IdP
            # must not become an attacker-controlled outbound request amplifier.
            _jwks_forced_refresh_attempts[config.id] = (fingerprint, now)
        keys = await _download_jwks(config)
        now = time.monotonic()
        _jwks_cache[config.id] = _JwksEntry(
            expires_at=now + config.jwks_cache_seconds,
            configuration_fingerprint=fingerprint,
            keys=keys,
        )
        return keys


async def _get_key(config: ProviderConfig, kid: str) -> _VerifiedKey:
    keys = await _get_key_set(config)
    key = keys.get(kid)
    if key is not None:
        return key
    # An unknown kid is the normal signal for IdP key rotation. Refresh exactly
    # once; continued absence fails closed rather than accepting a stale key.
    keys = await _get_key_set(
        config,
        force=True,
        force_cooldown_seconds=_UNKNOWN_KID_REFRESH_COOLDOWN_SECONDS,
    )
    key = keys.get(kid)
    if key is None:
        raise IdentityAuthenticationError("unknown_signing_key")
    return key


def _validate_key_algorithm(key: _VerifiedKey, algorithm: str) -> None:
    expected_type = _ALGORITHM_KEY_TYPES.get(algorithm)
    if key.jwk.get("kty") != expected_type:
        raise IdentityAuthenticationError("signing_key_type_mismatch")
    declared_algorithm = key.jwk.get("alg")
    if declared_algorithm is not None and declared_algorithm != algorithm:
        raise IdentityAuthenticationError("signing_key_algorithm_mismatch")
    expected_curve = _ALGORITHM_CURVES.get(algorithm)
    if expected_curve is not None and key.jwk.get("crv") != expected_curve:
        raise IdentityAuthenticationError("signing_key_curve_mismatch")
    if expected_type == "OKP" and key.jwk.get("crv") not in {"Ed25519", "Ed448"}:
        raise IdentityAuthenticationError("signing_key_curve_mismatch")
    if expected_type == "RSA" and getattr(key.key, "key_size", 0) < 2048:
        raise IdentityAuthenticationError("signing_key_too_small")


def _unverified_token_parts(token: str) -> tuple[dict[str, Any], dict[str, Any]]:
    if not token or len(token.encode("utf-8")) > _MAX_BEARER_BYTES:
        raise IdentityAuthenticationError("invalid_token_size")
    try:
        header = jwt.get_unverified_header(token)
        claims = jwt.decode(
            token,
            options={
                "verify_signature": False,
                "verify_exp": False,
                "verify_nbf": False,
                "verify_iat": False,
                "verify_aud": False,
                "verify_iss": False,
            },
        )
    except jwt.PyJWTError as exc:
        raise IdentityAuthenticationError("malformed_token") from exc
    if not isinstance(header, dict) or not isinstance(claims, dict):
        raise IdentityAuthenticationError("malformed_token")
    return header, claims


async def _load_provider(db: AsyncSession, issuer: str) -> ProviderConfig:
    result = await db.execute(
        select(TrustedIdentityProvider).where(
            and_(
                TrustedIdentityProvider.issuer == issuer,
                TrustedIdentityProvider.enabled.is_(True),
                TrustedIdentityProvider.revoked_at.is_(None),
            )
        )
    )
    row = result.scalar_one_or_none()
    if row is None:
        raise IdentityAuthenticationError("untrusted_issuer")
    config = ProviderConfig.from_row(row)
    # Release the pre-authentication DB transaction before an external JWKS
    # request. A slow IdP must not pin a database connection or open snapshot.
    await db.rollback()
    return config


def _verify_registered_claims(
    token: str,
    header: dict[str, Any],
    config: ProviderConfig,
    key: _VerifiedKey,
) -> dict[str, Any]:
    algorithm = header.get("alg")
    if not isinstance(algorithm, str) or algorithm not in config.allowed_algorithms:
        raise IdentityAuthenticationError("algorithm_not_allowed")
    if header.get("crit"):
        raise IdentityAuthenticationError("unsupported_critical_header")
    if "jku" in header or "x5u" in header:
        raise IdentityAuthenticationError("remote_key_header_not_allowed")
    if config.required_typ is not None and header.get("typ") != config.required_typ:
        raise IdentityAuthenticationError("token_type_mismatch")
    _validate_key_algorithm(key, algorithm)
    try:
        claims = jwt.decode(
            token,
            key=key.key,
            algorithms=[algorithm],
            issuer=config.issuer,
            audience=list(config.audiences),
            leeway=config.clock_skew_seconds,
            options={
                "require": list(config.required_claims),
                "verify_signature": True,
                "verify_exp": True,
                "verify_nbf": True,
                "verify_iat": True,
                "verify_aud": True,
                "verify_iss": True,
            },
        )
    except jwt.PyJWTError as exc:
        raise IdentityAuthenticationError("token_validation_failed") from exc

    now = datetime.now(timezone.utc).timestamp()
    issued_at = claims.get("iat")
    expires_at = claims.get("exp")
    if (
        isinstance(issued_at, bool)
        or not isinstance(issued_at, (int, float))
        or isinstance(expires_at, bool)
        or not isinstance(expires_at, (int, float))
    ):
        raise IdentityAuthenticationError("invalid_token_timestamps")
    maximum = config.max_token_age_seconds + config.clock_skew_seconds
    if now - float(issued_at) > maximum:
        raise IdentityAuthenticationError("token_too_old")
    if float(expires_at) - float(issued_at) > maximum:
        raise IdentityAuthenticationError("token_lifetime_too_long")
    return claims


async def authenticate_bearer(db: AsyncSession, token: str) -> FederatedPrincipal:
    """Verify a bearer JWT, then resolve its exact administrator-owned binding."""
    header, unverified_claims = _unverified_token_parts(token)
    issuer = unverified_claims.get("iss")
    if not isinstance(issuer, str) or not issuer:
        raise IdentityAuthenticationError("missing_issuer")
    config = await _load_provider(db, issuer)

    kid = header.get("kid")
    if not isinstance(kid, str) or not kid or len(kid) > 512:
        raise IdentityAuthenticationError("missing_signing_key_id")
    try:
        key = await _get_key(config, kid)
    except IdentityProviderFetchError as exc:
        raise IdentityAuthenticationError("identity_provider_unavailable") from exc
    claims = _verify_registered_claims(token, header, config, key)

    subject = claims.get("sub")
    if not isinstance(subject, str) or not subject or len(subject) > 512:
        raise IdentityAuthenticationError("invalid_subject")
    try:
        binding = await lookup_identity_binding(
            db,
            provider_id=config.id,
            external_subject=subject,
        )
    except AuthLookupInvariantError as exc:
        raise IdentityAuthenticationError("invalid_binding_record") from exc
    if binding is None:
        raise IdentityAuthenticationError("subject_not_bound")
    if is_reserved_barrier_group(binding.barrier_group):
        raise IdentityAuthenticationError("binding_uses_reserved_barrier")

    if binding.authorized_party:
        party = claims.get("azp") or claims.get("client_id")
        if party != binding.authorized_party:
            raise IdentityAuthenticationError("authorized_party_mismatch")

    scopes = effective_scopes(binding.role, binding.scopes)
    if not scopes:
        raise IdentityAuthenticationError("binding_has_no_permissions")
    return FederatedPrincipal(
        namespace=binding.namespace,
        scopes=scopes,
        barrier_group=binding.barrier_group,
        role=binding.role,
        principal_id=oidc_principal_ref(config.id, binding.id),
        principal_type=binding.principal_type,
        credential_id=str(binding.id),
        provider_id=config.id,
    )


async def probe_provider(row: TrustedIdentityProvider) -> int:
    """Force-refresh a provider and return the number of usable signing keys."""
    config = ProviderConfig.from_row(row)
    keys = await _get_key_set(config, force=True)
    return len(keys)
