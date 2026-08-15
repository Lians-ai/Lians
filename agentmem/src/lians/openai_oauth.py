"""OAuth resource-server primitives for the public OpenAI MCP endpoint.

The authorization server is intentionally external.  Lians acts only as the
OAuth 2.1 resource server: it verifies short-lived JWT access tokens on every
request and derives an opaque tenant namespace from the verified issuer,
required tenant claim, and subject. Raw identifiers and bearer tokens are
never persisted.
"""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import math
import re
from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlsplit

import jwt
from jwt import PyJWKClient
from mcp.server.auth.provider import AccessToken

_SAFE_ALGORITHMS = {"RS256", "RS384", "RS512", "ES256", "ES384", "EdDSA"}
_CONTROL_CHARACTERS = re.compile(r"[\x00-\x1f\x7f]")
_CLAIM_NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_.:-]{0,63}$")
_JWKS_CACHE_LIFESPAN_SECONDS = 300
_JWKS_NETWORK_TIMEOUT_SECONDS = 5


def _clean_url(
    value: str,
    *,
    label: str,
    origin_only: bool = False,
) -> str:
    """Return a normalized HTTPS URL or raise a secret-safe error."""
    candidate = value.strip()
    parsed = urlsplit(candidate)
    if (
        not candidate
        or parsed.scheme != "https"
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
        or parsed.query
        or parsed.fragment
        or _CONTROL_CHARACTERS.search(candidate)
        or any(character.isspace() for character in candidate)
    ):
        raise ValueError(f"{label} must be a non-secret HTTPS URL")
    try:
        _port = parsed.port
    except ValueError as exc:
        raise ValueError(f"{label} must use a valid HTTPS port") from exc
    if origin_only and parsed.path not in ("", "/"):
        raise ValueError(f"{label} must be an HTTPS origin without a path")
    return candidate.rstrip("/")


def configured_algorithms(value: str) -> tuple[str, ...]:
    algorithms = tuple(algorithm.strip() for algorithm in value.split(",") if algorithm.strip())
    if not algorithms or any(algorithm not in _SAFE_ALGORITHMS for algorithm in algorithms):
        raise ValueError("HOSTED_MCP_JWT_ALGORITHMS contains an unsupported algorithm")
    return algorithms


def validate_openai_mcp_settings(settings: Any) -> None:
    """Fail closed when the hosted MCP endpoint has an unsafe auth contract."""
    challenge = getattr(settings, "openai_apps_challenge_token", "")
    if challenge and (
        len(challenge) > 1024
        or challenge != challenge.strip()
        or _CONTROL_CHARACTERS.search(challenge)
    ):
        raise ValueError("OPENAI_APPS_CHALLENGE_TOKEN has an invalid format")
    if not settings.hosted_mcp_enabled:
        return
    _clean_url(
        settings.hosted_mcp_resource_url,
        label="HOSTED_MCP_RESOURCE_URL",
        origin_only=True,
    )
    _clean_url(settings.hosted_mcp_issuer_url, label="HOSTED_MCP_ISSUER_URL")
    _clean_url(settings.hosted_mcp_jwks_url, label="HOSTED_MCP_JWKS_URL")
    _clean_url(
        settings.hosted_mcp_service_documentation_url,
        label="HOSTED_MCP_SERVICE_DOCUMENTATION_URL",
    )
    configured_algorithms(settings.hosted_mcp_jwt_algorithms)
    if not _CLAIM_NAME.fullmatch(settings.hosted_mcp_tenant_claim):
        raise ValueError("HOSTED_MCP_TENANT_CLAIM is invalid")
    if not 60 <= settings.hosted_mcp_max_token_lifetime_seconds <= 86_400:
        raise ValueError("HOSTED_MCP_MAX_TOKEN_LIFETIME_SECONDS is out of range")
    if not 1 <= settings.hosted_mcp_retention_days <= 3650:
        raise ValueError("HOSTED_MCP_RETENTION_DAYS must be between 1 and 3650 days")
    retention_interval = float(getattr(settings, "retention_prune_interval_hours", 0))
    if not math.isfinite(retention_interval) or not 0 < retention_interval <= 24:
        raise ValueError(
            "RETENTION_PRUNE_INTERVAL_HOURS must be finite and between 0 and 24 "
            "when hosted MCP is enabled"
        )
    embedding_provider = getattr(settings, "embedding_provider", None)
    if embedding_provider is not None and embedding_provider not in {
        "sentence-transformers",
        "bge-onnx",
    }:
        raise ValueError("Hosted MCP requires a pinned, self-hosted semantic embedding provider")
    if embedding_provider == "sentence-transformers" and not re.fullmatch(
        r"[0-9a-f]{40}",
        getattr(settings, "sentence_transformer_revision", ""),
    ):
        raise ValueError(
            "Hosted MCP sentence-transformers requires a 40-character immutable revision"
        )
    namespace_secret = getattr(settings, "api_secret_seed", "")
    if len(namespace_secret) < 32 or namespace_secret.startswith("dev-seed-"):
        raise ValueError(
            "API_SECRET_SEED must be a private random value of at least 32 characters "
            "when HOSTED_MCP_ENABLED=true"
        )


def _scope_values(claim: Any) -> set[str]:
    if isinstance(claim, str):
        return {scope for scope in claim.split() if scope}
    if isinstance(claim, Iterable) and not isinstance(claim, (bytes, dict)):
        return {str(scope) for scope in claim if str(scope)}
    return set()


class JWTAccessTokenVerifier:
    """Validate OAuth JWTs against an issuer-controlled JWKS endpoint."""

    def __init__(
        self,
        *,
        issuer_url: str,
        resource_url: str,
        jwks_url: str,
        algorithms: tuple[str, ...],
        tenant_claim: str | None,
        max_token_lifetime_seconds: int = 3600,
        leeway_seconds: int = 30,
        jwks_client: PyJWKClient | None = None,
    ) -> None:
        # These values are canonical OAuth identifiers, not navigation URLs.
        # Preserve them byte-for-byte so JWT `iss`/`aud` checks match the
        # protected-resource metadata advertised to the client.
        self.issuer_url = issuer_url
        self.resource_url = resource_url
        self.algorithms = algorithms
        self.tenant_claim = tenant_claim
        self.max_token_lifetime_seconds = max_token_lifetime_seconds
        self.leeway_seconds = leeway_seconds
        self._jwks_client = jwks_client or PyJWKClient(
            jwks_url,
            # A removed signing key must stop validating after the bounded
            # JWKS-set TTL. PyJWT's per-kid cache has no TTL, so leave it off.
            cache_keys=False,
            cache_jwk_set=True,
            lifespan=_JWKS_CACHE_LIFESPAN_SECONDS,
            timeout=_JWKS_NETWORK_TIMEOUT_SECONDS,
        )
        self._jwks_last_success: float | None = None
        self._jwks_signing_keys: tuple[Any, ...] = ()
        self._jwks_refresh_lock = asyncio.Lock()
        self._jwks_refresh_task: asyncio.Task[None] | None = None
        self._jwks_key_lookup_lock = asyncio.Lock()

    def _has_recent_jwks_success(self) -> bool:
        if self._jwks_last_success is None:
            return False
        age = asyncio.get_running_loop().time() - self._jwks_last_success
        return 0 <= age < _JWKS_CACHE_LIFESPAN_SECONDS

    async def _refresh_jwks(self) -> None:
        try:
            # Every real refresh bypasses PyJWT's cache. The caller-facing
            # force flag controls only whether our recent-success fast path is
            # bypassed, so a forced startup caller can safely join any flight.
            jwk_set = await asyncio.to_thread(self._jwks_client.get_jwk_set, True)
        except Exception as exc:  # PyJWT wraps network and document errors inconsistently.
            raise RuntimeError("Hosted MCP JWKS is unavailable or invalid") from exc
        signing_keys = tuple(
            key
            for key in getattr(jwk_set, "keys", ())
            if getattr(key, "public_key_use", None) in (None, "sig")
            and isinstance(getattr(key, "key_id", None), str)
            and key.key_id
        )
        if not signing_keys:
            raise RuntimeError("Hosted MCP JWKS contains no signing keys")
        # Retain the exact validated snapshot returned by the bounded refresh.
        # Token lookup must never consult PyJWKClient's independently expiring
        # cache, where an attacker-selected unknown `kid` could trigger I/O.
        self._jwks_signing_keys = signing_keys
        self._jwks_last_success = asyncio.get_running_loop().time()

    def _jwks_refresh_finished(self, task: asyncio.Task[None]) -> None:
        if self._jwks_refresh_task is task:
            self._jwks_refresh_task = None
        # Retrieve an otherwise-unobserved failure if every waiter was
        # cancelled. Awaiting the completed task still receives the exception.
        try:
            task.exception()
        except asyncio.CancelledError:
            pass

    async def warm_jwks(self, *, force_refresh: bool = False) -> None:
        """Require a recent JWKS success, coalescing concurrent refreshes.

        Startup requests one forced network refresh. Readiness probes reuse that
        last success until the bounded JWKS cache lifetime expires, preventing
        health traffic from turning into an authorization-server dependency on
        every probe.
        """
        if not force_refresh and self._has_recent_jwks_success():
            return

        async with self._jwks_refresh_lock:
            if not force_refresh and self._has_recent_jwks_success():
                return
            refresh_task = self._jwks_refresh_task
            if refresh_task is None:
                refresh_task = asyncio.create_task(
                    self._refresh_jwks(),
                    name="hosted-mcp-jwks-refresh",
                )
                self._jwks_refresh_task = refresh_task
                refresh_task.add_done_callback(self._jwks_refresh_finished)

        # A timed-out readiness request must not cancel the shared bounded
        # network operation and allow a second request to start a duplicate.
        await asyncio.shield(refresh_task)

    async def verify_token(self, token: str) -> AccessToken | None:
        """Return verified request identity, or ``None`` without leaking details."""
        if not token or len(token) > 16_384:
            return None
        try:
            header = jwt.get_unverified_header(token)
            key_id = header.get("kid")
            algorithm = header.get("alg")
            if (
                not isinstance(key_id, str)
                or not key_id
                or len(key_id) > 256
                or _CONTROL_CHARACTERS.search(key_id)
                or algorithm not in self.algorithms
            ):
                return None

            # Participate in the same bounded refresh flight as startup and
            # readiness, then select only from our retained snapshot.
            # PyJWKClient's convenience lookup force-refreshes for every
            # attacker-selected unknown `kid`; snapshot-only selection bounds
            # issuer traffic while the normal 300-second refresh picks up
            # overlapping rotations.
            await self.warm_jwks()
            async with self._jwks_key_lookup_lock:
                signing_key = next(
                    (key for key in self._jwks_signing_keys if key.key_id == key_id),
                    None,
                )
            if signing_key is None:
                return None
            claims = jwt.decode(
                token,
                key=signing_key.key,
                algorithms=list(self.algorithms),
                audience=self.resource_url,
                issuer=self.issuer_url,
                leeway=self.leeway_seconds,
                options={"require": ["aud", "exp", "iat", "iss", "sub"]},
            )
        except (
            AttributeError,
            RuntimeError,
            jwt.PyJWTError,
            ValueError,
            TypeError,
            KeyError,
        ):
            return None

        subject = str(claims.get("sub", ""))
        if not subject or len(subject) > 512 or _CONTROL_CHARACTERS.search(subject):
            return None
        tenant = ""
        if self.tenant_claim:
            tenant_claim = claims.get(self.tenant_claim)
            if (
                not isinstance(tenant_claim, str)
                or not tenant_claim
                or len(tenant_claim) > 512
                or _CONTROL_CHARACTERS.search(tenant_claim)
            ):
                return None
            tenant = tenant_claim
        issued_at = claims.get("iat")
        expires_at = claims.get("exp")
        if (
            not isinstance(issued_at, (int, float))
            or not isinstance(expires_at, (int, float))
            or expires_at - issued_at > self.max_token_lifetime_seconds
        ):
            return None
        resource_claim = claims.get("resource")
        if resource_claim is not None and str(resource_claim) != self.resource_url:
            return None

        scopes = _scope_values(claims.get("scope")) | _scope_values(claims.get("scp"))
        client_id = str(claims.get("azp") or claims.get("client_id") or subject)
        return AccessToken(
            token="verified",
            client_id=client_id,
            scopes=sorted(scopes),
            expires_at=int(claims["exp"]),
            resource=self.resource_url,
            subject=subject,
            claims={"iss": self.issuer_url, "tenant": tenant},
        )


@dataclass(frozen=True)
class OAuthPrincipal:
    """Opaque storage identity derived only from a verified OAuth token."""

    namespace: str
    subject_fingerprint: str


def principal_from_access_token(token: AccessToken, namespace_secret: str) -> OAuthPrincipal:
    issuer = str((token.claims or {}).get("iss", ""))
    tenant = str((token.claims or {}).get("tenant", ""))
    subject = token.subject or ""
    if not issuer or not tenant or not subject:
        raise ValueError("Verified access token is missing issuer, tenant, or subject")
    if len(namespace_secret) < 32:
        raise ValueError("Hosted MCP namespace secret is not configured")
    digest = hmac.new(
        namespace_secret.encode(),
        f"lians-openai-mcp-namespace-v1\x00{issuer}\x00{tenant}\x00{subject}".encode(),
        hashlib.sha256,
    ).hexdigest()
    return OAuthPrincipal(
        namespace=f"openai-mcp-{digest[:40]}",
        subject_fingerprint=digest,
    )
