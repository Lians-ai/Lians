"""Focused tests for the hosted MCP OAuth resource-server boundary."""

from __future__ import annotations

import asyncio
import threading
import time
from types import SimpleNamespace

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa
from jwt.utils import base64url_encode
from mcp.server.auth.provider import AccessToken
from src.lians.openai_oauth import (
    JWTAccessTokenVerifier,
    _scope_values,
    configured_algorithms,
    principal_from_access_token,
    validate_openai_mcp_settings,
)

ISSUER = "https://issuer.example"
RESOURCE = "https://mcp.lians.ai"


class _StaticJwksClient:
    def __init__(self, public_key, *, key_id: str = "key-1"):
        self.public_key = public_key
        self.signing_keys = [SimpleNamespace(key=public_key, key_id=key_id, public_key_use="sig")]
        self.jwk_set_calls = []

    def get_jwk_set(self, refresh: bool = False):
        self.jwk_set_calls.append(refresh)
        return SimpleNamespace(keys=list(self.signing_keys))


@pytest.fixture(scope="module")
def signing_keys():
    private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    return private_key, private_key.public_key()


def _claims(**overrides):
    now = int(time.time())
    claims = {
        "iss": ISSUER,
        "aud": RESOURCE,
        "sub": "user_123",
        "tenant_id": "account_123",
        "azp": "client_123",
        "iat": now,
        "exp": now + 300,
        "resource": RESOURCE,
        "scope": "memory:read shared",
        "scp": ["memory:write", "shared"],
    }
    claims.update(overrides)
    return claims


def _token(private_key, **overrides) -> str:
    return jwt.encode(
        _claims(**overrides),
        private_key,
        algorithm="RS256",
        headers={"kid": "key-1"},
    )


def _verifier(public_key, *, jwks_client=None) -> JWTAccessTokenVerifier:
    return JWTAccessTokenVerifier(
        issuer_url=ISSUER,
        resource_url=RESOURCE,
        jwks_url=f"{ISSUER}/.well-known/jwks.json",
        algorithms=("RS256",),
        tenant_claim="tenant_id",
        max_token_lifetime_seconds=3600,
        leeway_seconds=30,
        jwks_client=jwks_client or _StaticJwksClient(public_key),
    )


@pytest.mark.parametrize("interval", [0, float("nan"), float("inf"), 24.1])
def test_hosted_settings_require_bounded_active_retention_scheduler(interval):
    settings = SimpleNamespace(
        hosted_mcp_enabled=True,
        hosted_mcp_resource_url=RESOURCE,
        hosted_mcp_issuer_url=ISSUER,
        hosted_mcp_jwks_url=f"{ISSUER}/.well-known/jwks.json",
        hosted_mcp_service_documentation_url="https://www.lians.ai/privacy",
        hosted_mcp_jwt_algorithms="RS256",
        hosted_mcp_tenant_claim="tenant_id",
        hosted_mcp_max_token_lifetime_seconds=3600,
        hosted_mcp_retention_days=365,
        retention_prune_interval_hours=interval,
        embedding_provider="sentence-transformers",
        sentence_transformer_revision="d4aa6901d3a41ba39fb536a557fa166f842b0e09",
        api_secret_seed="test-only-hosted-namespace-secret-32-bytes",
        openai_apps_challenge_token="",
    )

    with pytest.raises(ValueError, match="RETENTION_PRUNE_INTERVAL_HOURS"):
        validate_openai_mcp_settings(settings)


def test_jwks_key_cache_expires_with_the_bounded_set_cache(monkeypatch):
    captured = {}

    class FakeJwksClient:
        def __init__(self, _url, **kwargs):
            captured.update(kwargs)

    monkeypatch.setattr("src.lians.openai_oauth.PyJWKClient", FakeJwksClient)
    JWTAccessTokenVerifier(
        issuer_url=ISSUER,
        resource_url=RESOURCE,
        jwks_url=f"{ISSUER}/.well-known/jwks.json",
        algorithms=("RS256",),
        tenant_claim="tenant_id",
        max_token_lifetime_seconds=3600,
    )

    assert captured["cache_keys"] is False
    assert captured["cache_jwk_set"] is True
    assert captured["lifespan"] == 300
    assert captured["timeout"] == 5


async def test_valid_jwt_is_verified_and_scope_claims_are_combined(signing_keys):
    private_key, public_key = signing_keys
    client = _StaticJwksClient(public_key)
    verifier = _verifier(public_key, jwks_client=client)

    encoded = _token(private_key)
    verified = await verifier.verify_token(encoded)
    verified_again = await verifier.verify_token(encoded)

    assert verified is not None
    assert verified_again is not None
    assert verified.token == "verified"
    assert verified.subject == "user_123"
    assert verified.client_id == "client_123"
    assert verified.resource == RESOURCE
    assert verified.scopes == ["memory:read", "memory:write", "shared"]
    assert verified.claims == {"iss": ISSUER, "tenant": "account_123"}
    assert client.jwk_set_calls == [True]


async def test_many_unknown_key_ids_do_not_force_jwks_refresh(signing_keys):
    private_key, public_key = signing_keys
    client = _StaticJwksClient(public_key)
    verifier = _verifier(public_key, jwks_client=client)
    await verifier.warm_jwks(force_refresh=True)

    for index in range(20):
        encoded = jwt.encode(
            _claims(),
            private_key,
            algorithm="RS256",
            headers={"kid": f"unknown-{index}"},
        )
        assert await verifier.verify_token(encoded) is None

    assert client.jwk_set_calls == [True]


async def test_forced_bounded_refresh_picks_up_rotated_key(signing_keys):
    _old_private_key, old_public_key = signing_keys
    new_private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    client = _StaticJwksClient(old_public_key)
    verifier = _verifier(old_public_key, jwks_client=client)
    rotated_token = jwt.encode(
        _claims(),
        new_private_key,
        algorithm="RS256",
        headers={"kid": "key-2"},
    )

    assert await verifier.verify_token(rotated_token) is None
    client.signing_keys = [
        SimpleNamespace(
            key=new_private_key.public_key(),
            key_id="key-2",
            public_key_use="sig",
        )
    ]
    await verifier.warm_jwks(force_refresh=True)

    assert await verifier.verify_token(rotated_token) is not None
    assert client.jwk_set_calls == [True, True]


@pytest.mark.parametrize(
    "key_id",
    [None, 7, "x" * 257],
    ids=["missing", "non-string", "oversized"],
)
async def test_invalid_key_id_is_rejected_before_jwks_io(signing_keys, key_id):
    private_key, public_key = signing_keys
    client = _StaticJwksClient(public_key)
    verifier = _verifier(public_key, jwks_client=client)
    if isinstance(key_id, int):
        valid = jwt.encode(
            _claims(),
            private_key,
            algorithm="RS256",
            headers={"kid": "temporary"},
        )
        malformed_header = base64url_encode(b'{"alg":"RS256","kid":7,"typ":"JWT"}').decode()
        encoded = malformed_header + "." + valid.split(".", 1)[1]
    else:
        headers = {} if key_id is None else {"kid": key_id}
        encoded = jwt.encode(
            _claims(),
            private_key,
            algorithm="RS256",
            headers=headers,
        )

    assert await verifier.verify_token(encoded) is None
    assert client.jwk_set_calls == []


async def test_jwks_readiness_reuses_last_success_and_force_bypasses_it(signing_keys):
    _private_key, public_key = signing_keys
    client = _StaticJwksClient(public_key)
    verifier = _verifier(public_key, jwks_client=client)

    await verifier.warm_jwks(force_refresh=True)
    await verifier.warm_jwks()
    await verifier.warm_jwks(force_refresh=True)

    assert client.jwk_set_calls == [True, True]


async def test_concurrent_forced_jwks_callers_share_one_fetch(signing_keys):
    _private_key, public_key = signing_keys

    class _BlockingJwksClient(_StaticJwksClient):
        def __init__(self, key):
            super().__init__(key)
            self.started = threading.Event()
            self.release = threading.Event()

        def get_jwk_set(self, refresh: bool = False):
            self.jwk_set_calls.append(refresh)
            self.started.set()
            if not self.release.wait(timeout=2):
                raise TimeoutError("test JWKS release timed out")
            return SimpleNamespace(keys=list(self.signing_keys))

    client = _BlockingJwksClient(public_key)
    verifier = _verifier(public_key, jwks_client=client)

    first = asyncio.create_task(verifier.warm_jwks(force_refresh=True))
    assert await asyncio.wait_for(asyncio.to_thread(client.started.wait, 1), timeout=2)
    second = asyncio.create_task(verifier.warm_jwks(force_refresh=True))
    await asyncio.sleep(0)
    client.release.set()
    await asyncio.gather(first, second)

    assert client.jwk_set_calls == [True]


async def test_cancelled_jwks_waiter_does_not_cancel_or_clear_shared_fetch(signing_keys):
    _private_key, public_key = signing_keys

    class _BlockingJwksClient(_StaticJwksClient):
        def __init__(self, key):
            super().__init__(key)
            self.started = threading.Event()
            self.release = threading.Event()

        def get_jwk_set(self, refresh: bool = False):
            self.jwk_set_calls.append(refresh)
            self.started.set()
            if not self.release.wait(timeout=2):
                raise TimeoutError("test JWKS release timed out")
            return SimpleNamespace(keys=list(self.signing_keys))

    client = _BlockingJwksClient(public_key)
    verifier = _verifier(public_key, jwks_client=client)
    cancelled_waiter = asyncio.create_task(verifier.warm_jwks(force_refresh=True))
    assert await asyncio.wait_for(asyncio.to_thread(client.started.wait, 1), timeout=2)
    surviving_waiter = asyncio.create_task(verifier.warm_jwks(force_refresh=True))
    await asyncio.sleep(0)

    cancelled_waiter.cancel()
    with pytest.raises(asyncio.CancelledError):
        await cancelled_waiter
    assert not surviving_waiter.done()

    client.release.set()
    await surviving_waiter

    assert client.jwk_set_calls == [True]


async def test_failed_jwks_flight_is_shared_and_next_call_retries(signing_keys):
    _private_key, public_key = signing_keys

    class _FailOnceJwksClient(_StaticJwksClient):
        def __init__(self, key):
            super().__init__(key)
            self.started = threading.Event()
            self.release = threading.Event()
            self.fail_next = True

        def get_jwk_set(self, refresh: bool = False):
            self.jwk_set_calls.append(refresh)
            if self.fail_next:
                self.started.set()
                if not self.release.wait(timeout=2):
                    raise TimeoutError("test JWKS release timed out")
                self.fail_next = False
                raise ValueError("invalid JWKS")
            return SimpleNamespace(keys=list(self.signing_keys))

    client = _FailOnceJwksClient(public_key)
    verifier = _verifier(public_key, jwks_client=client)
    first = asyncio.create_task(verifier.warm_jwks(force_refresh=True))
    assert await asyncio.wait_for(asyncio.to_thread(client.started.wait, 1), timeout=2)
    second = asyncio.create_task(verifier.warm_jwks(force_refresh=True))
    await asyncio.sleep(0)
    client.release.set()

    results = await asyncio.gather(first, second, return_exceptions=True)
    assert all(isinstance(result, RuntimeError) for result in results)
    assert client.jwk_set_calls == [True]

    await verifier.warm_jwks(force_refresh=True)
    assert client.jwk_set_calls == [True, True]


@pytest.mark.parametrize(
    ("claim", "value"),
    [
        ("iss", "https://wrong-issuer.example"),
        ("aud", "https://wrong-resource.example"),
        ("exp", lambda: int(time.time()) - 120),
        ("resource", "https://wrong-resource.example"),
    ],
    ids=["wrong-issuer", "wrong-audience", "expired", "wrong-resource-claim"],
)
async def test_invalid_jwt_identity_contract_is_rejected(signing_keys, claim, value):
    private_key, public_key = signing_keys
    resolved = value() if callable(value) else value

    verified = await _verifier(public_key).verify_token(_token(private_key, **{claim: resolved}))

    assert verified is None


async def test_missing_or_overlong_issued_token_is_rejected(signing_keys):
    private_key, public_key = signing_keys
    verifier = _verifier(public_key)
    now = int(time.time())
    missing_iat_claims = _claims()
    missing_iat_claims.pop("iat")
    missing_iat = jwt.encode(
        missing_iat_claims,
        private_key,
        algorithm="RS256",
        headers={"kid": "key-1"},
    )
    long_lived = _token(private_key, iat=now, exp=now + 3601)

    assert await verifier.verify_token(missing_iat) is None
    assert await verifier.verify_token(long_lived) is None


async def test_missing_or_unsafe_subject_is_rejected(signing_keys):
    private_key, public_key = signing_keys
    verifier = _verifier(public_key)

    assert await verifier.verify_token(_token(private_key, sub="")) is None
    assert await verifier.verify_token(_token(private_key, sub="user\nadmin")) is None
    assert await verifier.verify_token(_token(private_key, tenant_id="")) is None
    assert await verifier.verify_token(_token(private_key, tenant_id="account\nadmin")) is None


def test_scope_parser_supports_oauth_scope_and_scp_shapes():
    assert _scope_values("memory:read   memory:write") == {
        "memory:read",
        "memory:write",
    }
    assert _scope_values(["memory:read", "", "memory:write"]) == {
        "memory:read",
        "memory:write",
    }
    assert _scope_values(("memory:read", "memory:read")) == {"memory:read"}
    assert _scope_values({"scope": "memory:read"}) == set()
    assert _scope_values(b"memory:read") == set()
    assert _scope_values(None) == set()


def _settings(**overrides):
    values = {
        "hosted_mcp_enabled": True,
        "hosted_mcp_resource_url": RESOURCE,
        "hosted_mcp_issuer_url": ISSUER,
        "hosted_mcp_jwks_url": f"{ISSUER}/.well-known/jwks.json",
        "hosted_mcp_service_documentation_url": "https://www.lians.ai/privacy",
        "hosted_mcp_jwt_algorithms": "RS256",
        "hosted_mcp_max_token_lifetime_seconds": 3600,
        "hosted_mcp_tenant_claim": "tenant_id",
        "hosted_mcp_retention_days": 365,
        "retention_prune_interval_hours": 24,
        "api_secret_seed": "test-only-hosted-namespace-secret-32-bytes",
        "embedding_provider": "sentence-transformers",
        "sentence_transformer_revision": "d4aa6901d3a41ba39fb536a557fa166f842b0e09",
        "openai_apps_challenge_token": "",
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def test_disabled_hosted_mcp_does_not_require_oauth_configuration():
    validate_openai_mcp_settings(SimpleNamespace(hosted_mcp_enabled=False))


def test_disabled_hosted_mcp_still_rejects_an_invalid_domain_challenge():
    with pytest.raises(ValueError, match="OPENAI_APPS_CHALLENGE_TOKEN"):
        validate_openai_mcp_settings(
            SimpleNamespace(
                hosted_mcp_enabled=False,
                openai_apps_challenge_token="invalid\nchallenge",
            )
        )


def test_safe_hosted_mcp_configuration_is_accepted():
    validate_openai_mcp_settings(_settings())
    assert configured_algorithms("RS256, ES256") == ("RS256", "ES256")


def test_hosted_mcp_requires_a_private_namespace_secret():
    with pytest.raises(ValueError, match="API_SECRET_SEED"):
        validate_openai_mcp_settings(_settings(api_secret_seed="dev-seed-change-in-prod"))


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("hosted_mcp_resource_url", "http://mcp.lians.ai"),
        ("hosted_mcp_resource_url", "https://mcp.lians.ai/mcp"),
        ("hosted_mcp_issuer_url", ""),
        ("hosted_mcp_jwks_url", "https://issuer.example/jwks?token=secret"),
        ("hosted_mcp_service_documentation_url", "ftp://www.lians.ai/privacy"),
        ("hosted_mcp_jwt_algorithms", "HS256"),
        ("hosted_mcp_retention_days", 0),
        ("hosted_mcp_retention_days", 3651),
        ("hosted_mcp_max_token_lifetime_seconds", 86_401),
        ("embedding_provider", "local"),
        ("sentence_transformer_revision", "main"),
        ("openai_apps_challenge_token", " leading-space"),
    ],
)
def test_hosted_mcp_configuration_fails_closed(field, value):
    with pytest.raises(ValueError):
        validate_openai_mcp_settings(_settings(**{field: value}))


def test_principal_mapping_is_stable_opaque_and_identity_isolated():
    def token(*, issuer=ISSUER, tenant="account_123", subject="user_123"):
        return AccessToken(
            token="verified",
            client_id="client",
            scopes=["memory:read"],
            resource=RESOURCE,
            subject=subject,
            claims={"iss": issuer, "tenant": tenant},
        )

    secret = "test-only-hosted-namespace-secret-32-bytes"
    principal = principal_from_access_token(token(), secret)
    assert principal == principal_from_access_token(token(), secret)
    assert principal != principal_from_access_token(token(subject="user_456"), secret)
    assert principal != principal_from_access_token(
        token(issuer="https://another-issuer.example"), secret
    )
    assert principal != principal_from_access_token(token(tenant="account_456"), secret)
    assert principal != principal_from_access_token(
        token(), "another-test-namespace-secret-32-bytes"
    )
    assert principal.namespace.startswith("openai-mcp-")
    assert "user_123" not in principal.namespace
    assert len(principal.subject_fingerprint) == 64


def test_principal_mapping_requires_verified_issuer_and_subject():
    missing_issuer = AccessToken(
        token="verified",
        client_id="client",
        scopes=[],
        subject="user_123",
        claims={"tenant": "account_123"},
    )
    missing_subject = AccessToken(
        token="verified",
        client_id="client",
        scopes=[],
        subject=None,
        claims={"iss": ISSUER, "tenant": "account_123"},
    )

    with pytest.raises(ValueError):
        principal_from_access_token(missing_issuer, "test-only-hosted-namespace-secret-32-bytes")
    with pytest.raises(ValueError):
        principal_from_access_token(missing_subject, "test-only-hosted-namespace-secret-32-bytes")
