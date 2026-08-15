"""Consumer OAuth resource-server identity for zero-knowledge cloud sync.

Lians Cloud never receives a Google token or user profile. The native Bridge
obtains a short-lived access token from the configured authorization server;
the API verifies it and derives an opaque namespace from verified JWT claims.
"""

from __future__ import annotations

import hashlib
import hmac
from dataclasses import dataclass
from functools import lru_cache
from typing import Any

from mcp.server.auth.provider import AccessToken

from .config import get_settings
from .openai_oauth import (
    _CLAIM_NAME,
    JWTAccessTokenVerifier,
    _clean_url,
    configured_algorithms,
)

SYNC_OAUTH_SCOPE = "memory:sync"


@dataclass(frozen=True)
class CloudSyncPrincipal:
    """Opaque tenant identity derived from a verified consumer access token."""

    namespace: str
    subject_fingerprint: str


@dataclass(frozen=True)
class CloudSyncOAuthRuntime:
    verifier: JWTAccessTokenVerifier
    resource_url: str


def validate_cloud_sync_oauth_settings(settings: Any) -> None:
    """Fail closed when consumer sync OAuth is enabled but unsafe."""

    if not settings.cloud_sync_oauth_enabled:
        return
    _clean_url(
        settings.cloud_sync_oauth_resource_url,
        label="CLOUD_SYNC_OAUTH_RESOURCE_URL",
    )
    _clean_url(
        settings.cloud_sync_oauth_issuer_url,
        label="CLOUD_SYNC_OAUTH_ISSUER_URL",
    )
    _clean_url(
        settings.cloud_sync_oauth_jwks_url,
        label="CLOUD_SYNC_OAUTH_JWKS_URL",
    )
    configured_algorithms(settings.cloud_sync_oauth_jwt_algorithms)
    organization_claim = settings.cloud_sync_oauth_organization_claim
    if organization_claim and not _CLAIM_NAME.fullmatch(organization_claim):
        raise ValueError("CLOUD_SYNC_OAUTH_ORGANIZATION_CLAIM is invalid")
    if not 60 <= settings.cloud_sync_oauth_max_token_lifetime_seconds <= 86_400:
        raise ValueError("CLOUD_SYNC_OAUTH_MAX_TOKEN_LIFETIME_SECONDS is out of range")
    if len(settings.api_secret_seed) < 32 or settings.api_secret_seed.startswith("dev-seed-"):
        raise ValueError(
            "API_SECRET_SEED must be a private random value of at least 32 characters "
            "when CLOUD_SYNC_OAUTH_ENABLED=true"
        )


def principal_from_sync_access_token(
    token: AccessToken,
    namespace_secret: str,
) -> CloudSyncPrincipal:
    """Map verified issuer/account/subject claims to a non-reversible namespace."""

    issuer = str((token.claims or {}).get("iss", ""))
    organization = str((token.claims or {}).get("tenant", ""))
    subject = token.subject or ""
    if not issuer or not subject:
        raise ValueError("Verified sync access token is missing issuer or subject")
    if len(namespace_secret) < 32:
        raise ValueError("Cloud sync namespace secret is not configured")
    digest = hmac.new(
        namespace_secret.encode(),
        (
            "lians-cloud-sync-namespace-v1\x00"
            f"{issuer}\x00{organization}\x00{subject}"
        ).encode(),
        hashlib.sha256,
    ).hexdigest()
    return CloudSyncPrincipal(
        namespace=f"cloud-sync-{digest[:40]}",
        subject_fingerprint=digest,
    )


def build_cloud_sync_oauth_runtime(settings: Any) -> CloudSyncOAuthRuntime:
    validate_cloud_sync_oauth_settings(settings)
    if not settings.cloud_sync_oauth_enabled:
        raise ValueError("Consumer cloud sync OAuth is disabled")
    verifier = JWTAccessTokenVerifier(
        issuer_url=settings.cloud_sync_oauth_issuer_url,
        resource_url=settings.cloud_sync_oauth_resource_url,
        jwks_url=settings.cloud_sync_oauth_jwks_url,
        algorithms=configured_algorithms(settings.cloud_sync_oauth_jwt_algorithms),
        tenant_claim=settings.cloud_sync_oauth_organization_claim or None,
        max_token_lifetime_seconds=settings.cloud_sync_oauth_max_token_lifetime_seconds,
        leeway_seconds=settings.cloud_sync_oauth_jwt_leeway_seconds,
    )
    return CloudSyncOAuthRuntime(
        verifier=verifier,
        resource_url=settings.cloud_sync_oauth_resource_url,
    )


@lru_cache(maxsize=1)
def get_cloud_sync_oauth_runtime() -> CloudSyncOAuthRuntime | None:
    settings = get_settings()
    if not settings.cloud_sync_oauth_enabled:
        return None
    return build_cloud_sync_oauth_runtime(settings)
