"""Consumer OAuth identity contracts for zero-knowledge cloud sync."""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from mcp.server.auth.provider import AccessToken
from src.lians.cloud_sync_oauth import (
    SYNC_OAUTH_SCOPE,
    principal_from_sync_access_token,
    validate_cloud_sync_oauth_settings,
)

ISSUER = "https://login.example/"
RESOURCE = "https://api.lians.ai"
SECRET = "test-only-cloud-sync-namespace-secret-32-bytes"


def _settings(**overrides):
    values = {
        "cloud_sync_oauth_enabled": True,
        "cloud_sync_oauth_resource_url": RESOURCE,
        "cloud_sync_oauth_issuer_url": ISSUER,
        "cloud_sync_oauth_jwks_url": f"{ISSUER}.well-known/jwks.json",
        "cloud_sync_oauth_jwt_algorithms": "RS256",
        "cloud_sync_oauth_organization_claim": "",
        "cloud_sync_oauth_max_token_lifetime_seconds": 3600,
        "api_secret_seed": SECRET,
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def _token(*, subject="auth0|user-1", organization=""):
    return AccessToken(
        token="verified",
        client_id="native-client",
        scopes=[SYNC_OAUTH_SCOPE],
        expires_at=2_000_000_000,
        resource=RESOURCE,
        subject=subject,
        claims={"iss": ISSUER, "tenant": organization},
    )


def test_disabled_consumer_oauth_needs_no_provider_configuration():
    validate_cloud_sync_oauth_settings(SimpleNamespace(cloud_sync_oauth_enabled=False))


def test_safe_personal_and_organization_oauth_configuration_is_accepted():
    validate_cloud_sync_oauth_settings(_settings())
    validate_cloud_sync_oauth_settings(
        _settings(cloud_sync_oauth_organization_claim="org_id")
    )


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("cloud_sync_oauth_resource_url", "http://api.lians.ai"),
        ("cloud_sync_oauth_issuer_url", ""),
        ("cloud_sync_oauth_jwks_url", "https://login.example/jwks?token=secret"),
        ("cloud_sync_oauth_jwt_algorithms", "HS256"),
        ("cloud_sync_oauth_organization_claim", "bad claim"),
        ("cloud_sync_oauth_max_token_lifetime_seconds", 86_401),
        ("api_secret_seed", "dev-seed-change-in-prod"),
    ],
)
def test_consumer_oauth_configuration_fails_closed(field, value):
    with pytest.raises(ValueError):
        validate_cloud_sync_oauth_settings(_settings(**{field: value}))


def test_consumer_principal_is_stable_opaque_and_identity_isolated():
    principal = principal_from_sync_access_token(_token(), SECRET)
    assert principal == principal_from_sync_access_token(_token(), SECRET)
    assert principal != principal_from_sync_access_token(
        _token(subject="auth0|user-2"), SECRET
    )
    assert principal != principal_from_sync_access_token(
        _token(organization="org_2"), SECRET
    )
    assert principal != principal_from_sync_access_token(
        _token(), "another-test-cloud-sync-secret-32-bytes"
    )
    assert principal.namespace.startswith("cloud-sync-")
    assert "user-1" not in principal.namespace
    assert len(principal.subject_fingerprint) == 64


def test_consumer_principal_rejects_unverified_identity_shape():
    missing_issuer = _token()
    missing_issuer.claims = {"tenant": ""}
    missing_subject = _token()
    missing_subject.subject = None

    with pytest.raises(ValueError):
        principal_from_sync_access_token(missing_issuer, SECRET)
    with pytest.raises(ValueError):
        principal_from_sync_access_token(missing_subject, SECRET)
    with pytest.raises(ValueError):
        principal_from_sync_access_token(_token(), "short")
