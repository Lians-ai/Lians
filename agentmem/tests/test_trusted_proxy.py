"""Focused tests for the application-owned trusted-proxy identity boundary."""

from __future__ import annotations

import base64
from unittest.mock import AsyncMock

import pytest
from lians.config import Settings
from lians.main import _validate_production_secrets
from lians.middleware import (
    RateLimitMiddleware,
    derive_client_address,
    parse_trusted_proxy_cidrs,
)
from starlette.requests import Request
from starlette.responses import Response

TRUSTED = parse_trusted_proxy_cidrs("10.0.0.0/8,2001:db8:abcd::/48")


def _production_settings(trusted_proxy_cidrs: str) -> Settings:
    return Settings(
        deployment_environment="production",
        deployment_region="us-east-1",
        api_surface="public",
        database_url=(
            "postgresql+asyncpg://lians:secret@db.example.com:5432/lians"
            "?sslmode=verify-full"
        ),
        redis_url="rediss://redis.example.com:6379/0",
        admin_secret="a" * 32,
        subject_reference_key="00" * 32,
        receipt_signing_private_key=base64.b64encode(bytes(range(32))).decode(),
        receipt_signing_key_id="receipt-key-v1",
        embedding_provider="sentence-transformers",
        cors_origins="https://app.example.com",
        rate_limit_backend_failure_mode="deny",
        metrics_enabled=False,
        kms_provider="azure",
        kms_azure_vault_url="https://example.vault.azure.net/",
        master_key_id="master-key-v1",
        trusted_proxy_cidrs=trusted_proxy_cidrs,
    )


def test_empty_trust_ignores_spoofed_forwarded_header() -> None:
    assert derive_client_address("203.0.113.9", ["198.51.100.1"], ()) == "203.0.113.9"


def test_untrusted_peer_ignores_spoofed_forwarded_header() -> None:
    assert derive_client_address("203.0.113.9", ["198.51.100.1"], TRUSTED) == "203.0.113.9"


def test_trusted_peer_uses_first_untrusted_hop_from_right() -> None:
    assert (
        derive_client_address(
            "10.0.0.5",
            ["1.1.1.1, 198.51.100.7, 10.0.1.8"],
            TRUSTED,
        )
        == "198.51.100.7"
    )


def test_all_trusted_chain_resolves_to_leftmost_hop() -> None:
    assert derive_client_address("10.0.0.5", ["10.1.0.1, 10.2.0.2"], TRUSTED) == "10.1.0.1"


@pytest.mark.asyncio
async def test_middleware_reuses_one_derived_address_for_state_and_limits() -> None:
    middleware = RateLimitMiddleware(
        AsyncMock(),
        trusted_proxy_cidrs="10.0.0.0/8",
    )
    request = Request(
        {
            "type": "http",
            "http_version": "1.1",
            "method": "POST",
            "scheme": "https",
            "path": "/v1/recall",
            "raw_path": b"/v1/recall",
            "query_string": b"",
            "headers": [(b"x-forwarded-for", b"198.51.100.7")],
            "client": ("10.0.0.5", 43123),
            "server": ("lians", 443),
        }
    )
    call_next = AsyncMock()
    middleware._dispatch_layered = AsyncMock(return_value=Response(status_code=200))

    await middleware.dispatch(request, call_next)

    assert request.state.client_address == "198.51.100.7"
    middleware._dispatch_layered.assert_awaited_once_with(
        request,
        call_next,
        "198.51.100.7",
    )


@pytest.mark.parametrize(
    "forwarded",
    [
        [],
        [""],
        ["unknown"],
        ["198.51.100.1:443"],
        ["198.51.100.1,,10.0.0.8"],
        ["198.51.100.1", "10.0.0.8"],
        [",".join(f"198.51.100.{index}" for index in range(1, 34))],
        ["1.1.1.1," + (" " * 2_048)],
    ],
)
def test_ambiguous_or_malformed_forwarded_chain_falls_back_to_peer(
    forwarded: list[str],
) -> None:
    assert derive_client_address("10.0.0.5", forwarded, TRUSTED) == "10.0.0.5"


def test_missing_or_invalid_peer_never_trusts_forwarded_header() -> None:
    assert derive_client_address(None, ["198.51.100.7"], TRUSTED) == "unknown"
    assert derive_client_address("not-an-ip", ["198.51.100.7"], TRUSTED) == "unknown"


def test_ipv6_and_ipv4_mapped_addresses_are_canonicalized() -> None:
    assert (
        derive_client_address(
            "2001:db8:abcd::5",
            ["2001:0db8:0000:0000:0000:0000:0000:0001"],
            TRUSTED,
        )
        == "2001:db8::1"
    )
    assert (
        derive_client_address(
            "::ffff:10.0.0.5",
            ["::ffff:198.51.100.7"],
            TRUSTED,
        )
        == "198.51.100.7"
    )


@pytest.mark.parametrize(
    "configured",
    [
        "*",
        "0.0.0.0/0",
        "::/0",
        "10.0.0.1/8",
        "not-a-cidr",
        "10.0.0.0/8,,192.0.2.0/24",
        "10.0.0.0/8,10.0.0.0/8",
        "::ffff:10.0.0.0/104",
        "0.0.0.0/1,128.0.0.0/1",
        "::/1,8000::/1",
    ],
)
def test_invalid_or_world_open_proxy_configuration_is_rejected(
    configured: str,
) -> None:
    with pytest.raises(ValueError, match="TRUSTED_PROXY_CIDRS"):
        parse_trusted_proxy_cidrs(configured)


def test_proxy_configuration_count_is_bounded() -> None:
    configured = ",".join(f"10.0.{index}.0/24" for index in range(65))
    with pytest.raises(ValueError, match="at most 64"):
        parse_trusted_proxy_cidrs(configured)


def test_production_validation_accepts_empty_or_explicit_proxy_trust() -> None:
    _validate_production_secrets(_production_settings(""))
    _validate_production_secrets(_production_settings("10.24.0.0/16,fd00:24::/64"))


def test_production_validation_accepts_configured_bge_onnx_provider() -> None:
    settings = _production_settings("").model_copy(
        update={
            "embedding_provider": "bge-onnx",
            "bge_onnx_artifact_dir": "/opt/lians/bge-large-en-v1.5-onnx",
        }
    )

    _validate_production_secrets(settings)


@pytest.mark.parametrize(
    "configured",
    ["*", "0.0.0.0/0", "10.0.0.1/8", "0.0.0.0/1,128.0.0.0/1", "invalid"],
)
def test_production_startup_rejects_unsafe_proxy_trust(configured: str) -> None:
    with pytest.raises(RuntimeError, match="TRUSTED_PROXY_CIDRS"):
        _validate_production_secrets(_production_settings(configured))


@pytest.mark.parametrize(
    ("updates", "message"),
    [
        ({"embedding_provider": "local"}, "deterministic test-only"),
        (
            {
                "embedding_provider": "voyage",
                "voyage_api_key": "",
            },
            "VOYAGE_API_KEY",
        ),
        (
            {
                "embedding_provider": "openai",
                "openai_api_key": "",
            },
            "OPENAI_API_KEY",
        ),
        (
            {
                "embedding_provider": "bge-onnx",
                "bge_onnx_artifact_dir": "",
            },
            "BGE_ONNX_ARTIFACT_DIR",
        ),
        ({"receipt_signing_key_id": "lians-receipt-key"}, "published trust key"),
        ({"receipt_signing_key_id": "bad/key"}, "1-64 ASCII"),
        (
            {
                "supersession_llm_stage": True,
                "anthropic_api_key": "",
            },
            "ANTHROPIC_API_KEY",
        ),
    ],
)
def test_production_startup_rejects_nonproduction_model_and_signing_posture(
    updates: dict[str, object],
    message: str,
) -> None:
    settings = _production_settings("").model_copy(update=updates)
    with pytest.raises(RuntimeError, match=message):
        _validate_production_secrets(settings)
