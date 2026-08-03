"""Focused contracts for local and Vault Transit Decision Receipt signing."""

from __future__ import annotations

import base64
import hashlib
import json
import stat
from datetime import UTC, datetime
from uuid import UUID

import httpx
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from lians.config import Settings
from lians.decision_receipt import build_decision_receipt, verify_decision_receipt
from lians.receipt_signer import (
    ReceiptSignerConfigurationError,
    ReceiptSigningUnavailable,
    VaultTransitEd25519ReceiptSigner,
    build_decision_receipt_with_signer,
    create_receipt_signer,
    load_receipt_signer,
    receipt_signer_identity,
    reset_receipt_signer_cache_for_tests,
    validate_receipt_signer_configuration,
)

PRIVATE_RAW = bytes(range(32))
PRIVATE = Ed25519PrivateKey.from_private_bytes(PRIVATE_RAW)
PUBLIC_RAW = PRIVATE.public_key().public_bytes(
    encoding=serialization.Encoding.Raw,
    format=serialization.PublicFormat.Raw,
)
PRIVATE_B64 = base64.b64encode(PRIVATE_RAW).decode("ascii")
PUBLIC_B64 = base64.b64encode(PUBLIC_RAW).decode("ascii")
PINNED_VERSION = 7


def _vault_settings(**updates) -> Settings:
    values = {
        "deployment_environment": "production",
        "receipt_signing_provider": "vault-transit",
        "receipt_signing_key_id": "lians-us-east-receipts-v7",
        "receipt_signing_private_key": "",
        "receipt_vault_addr": "https://vault.example.com",
        "receipt_vault_token": "vault-test-token",
        "receipt_vault_mount_point": "lians-receipts",
        "receipt_vault_key_name": "production-receipts",
        "receipt_vault_key_version": PINNED_VERSION,
        "receipt_vault_public_key": PUBLIC_B64,
        "receipt_vault_timeout_seconds": 2.0,
    }
    values.update(updates)
    return Settings(**values)


def _metadata(*, public_key: str = PUBLIC_B64) -> dict:
    return {
        "data": {
            "name": "production-receipts",
            "type": "ed25519",
            "derived": False,
            "supports_signing": True,
            "latest_version": PINNED_VERSION + 1,
            "min_encryption_version": 1,
            "keys": {
                str(PINNED_VERSION): {
                    "creation_time": "2026-08-02T00:00:00Z",
                    "public_key": public_key,
                },
                str(PINNED_VERSION + 1): {
                    "creation_time": "2026-08-02T01:00:00Z",
                    "public_key": base64.b64encode(b"x" * 32).decode("ascii"),
                },
            },
        }
    }


def _decision() -> dict:
    timestamp = datetime(2026, 8, 2, 12, 0, tzinfo=UTC).isoformat()
    return {
        "id": UUID("11111111-1111-4111-8111-111111111111"),
        "namespace": "receipt-signer-test",
        "agent_id": "underwriter",
        "recorded_by_principal_ref": (
            "lians:principal:v1:api-key:11111111-1111-4111-8111-111111111111"
        ),
        "recorded_by_auth_method": "api_key",
        "recorded_by_credential_ref": (
            "lians:credential:v1:sha256:" + "a" * 64
        ),
        "decision_type": "credit",
        "outcome": "review",
        "reason_codes": ["EVIDENCE_GAP"],
        "decided_at": timestamp,
        "recorded_at": timestamp,
        "knowledge_as_of": timestamp,
        "knowledge_recorded_as_of": timestamp,
        "record_hash": "b" * 64,
        "record_hash_version": 2,
        "record_integrity_status": "verified",
        "metadata": {},
    }


@pytest.mark.asyncio
async def test_local_provider_preserves_existing_receipt_bytes() -> None:
    settings = Settings(
        receipt_signing_provider="local",
        receipt_signing_private_key=PRIVATE_B64,
        receipt_signing_key_id="local-receipt-v1",
    )
    signer = await create_receipt_signer(settings)
    assert signer is not None
    through_provider = await build_decision_receipt_with_signer(
        signer=signer,
        decision=_decision(),
        knowledge_snapshot=[],
        cited_evidence=[],
        audit_chain={"status": "unchecked"},
    )
    compatibility = build_decision_receipt(
        decision=_decision(),
        knowledge_snapshot=[],
        cited_evidence=[],
        audit_chain={"status": "unchecked"},
        signing_private_key=PRIVATE_B64,
        signing_key_id="local-receipt-v1",
    )
    assert through_provider == compatibility
    assert verify_decision_receipt(through_provider, require_signature=True)["valid"] is True


@pytest.mark.asyncio
async def test_vault_signs_digest_message_with_exact_pinned_version() -> None:
    observed: dict[str, object] = {}

    async def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers["X-Vault-Token"] == "vault-test-token"
        if request.method == "GET":
            assert request.url.path == "/v1/lians-receipts/keys/production-receipts"
            return httpx.Response(200, json=_metadata())
        assert request.url.path == "/v1/lians-receipts/sign/production-receipts"
        body = json.loads(request.content)
        observed.update(body)
        message = base64.b64decode(body["input"], validate=True)
        signature = base64.b64encode(PRIVATE.sign(message)).decode("ascii")
        return httpx.Response(
            200,
            json={"data": {"signature": f"vault:v{PINNED_VERSION}:{signature}"}},
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        signer = await create_receipt_signer(_vault_settings(), client=client)
        assert signer is not None
        digest = bytes.fromhex("ab" * 32)
        signature = await signer.sign_digest(digest)

    assert observed == {
        "input": base64.b64encode(digest).decode("ascii"),
        "key_version": PINNED_VERSION,
        "prehashed": False,
    }
    assert signature.key_id == "lians-us-east-receipts-v7"
    assert signature.public_key == PUBLIC_B64
    assert len(base64.b64decode(signature.value, validate=True)) == 64


@pytest.mark.asyncio
async def test_owned_vault_client_never_inherits_environment_proxy() -> None:
    config = validate_receipt_signer_configuration(_vault_settings())
    signer = VaultTransitEd25519ReceiptSigner(config)
    try:
        assert signer._client._trust_env is False
    finally:
        await signer.aclose()


@pytest.mark.asyncio
async def test_vault_streaming_response_is_bounded_before_json_decode() -> None:
    class OversizedStream(httpx.AsyncByteStream):
        async def __aiter__(self):
            yield b"x" * 700_000
            yield b"y" * 700_000

    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, stream=OversizedStream())

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(ReceiptSigningUnavailable, match="safety limit"):
            await create_receipt_signer(_vault_settings(), client=client)


@pytest.mark.asyncio
async def test_vault_rereads_read_only_token_file_and_sends_enterprise_namespace(
    tmp_path,
) -> None:
    token_file = tmp_path / "vault-token"
    token_file.write_text("first-token\n", encoding="ascii")
    original_mode = stat.S_IMODE(token_file.stat().st_mode)
    token_file.chmod(stat.S_IREAD)
    observed_tokens: list[str] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        observed_tokens.append(request.headers["X-Vault-Token"])
        assert request.headers["X-Vault-Namespace"] == "admin/payments"
        if request.method == "GET":
            return httpx.Response(200, json=_metadata())
        body = json.loads(request.content)
        message = base64.b64decode(body["input"], validate=True)
        signature = base64.b64encode(PRIVATE.sign(message)).decode("ascii")
        return httpx.Response(
            200,
            json={"data": {"signature": f"vault:v{PINNED_VERSION}:{signature}"}},
        )

    try:
        settings = _vault_settings(
            receipt_vault_token="",
            receipt_vault_token_file=str(token_file.absolute()),
            receipt_vault_namespace="admin/payments",
        )
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            signer = await create_receipt_signer(settings, client=client)
            token_file.chmod(stat.S_IWRITE | stat.S_IREAD)
            token_file.write_text("rotated-token\n", encoding="ascii")
            token_file.chmod(stat.S_IREAD)
            await signer.sign_digest(b"r" * 32)
            identity = receipt_signer_identity(signer)
    finally:
        token_file.chmod(original_mode)

    assert observed_tokens == ["first-token", "rotated-token"]
    assert identity == {
        "configured": True,
        "provider": "vault-transit",
        "algorithm": "ed25519",
        "key_id": "lians-us-east-receipts-v7",
        "key_version": PINNED_VERSION,
        "public_key_sha256": hashlib.sha256(PUBLIC_RAW).hexdigest(),
    }
    assert str(token_file) not in json.dumps(identity)


@pytest.mark.asyncio
async def test_vault_receipt_keeps_provider_neutral_v01_signature_shape() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "GET":
            return httpx.Response(200, json=_metadata())
        body = json.loads(request.content)
        message = base64.b64decode(body["input"], validate=True)
        signature = base64.b64encode(PRIVATE.sign(message)).decode("ascii")
        return httpx.Response(
            200,
            json={"data": {"signature": f"vault:v{PINNED_VERSION}:{signature}"}},
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        signer = await create_receipt_signer(_vault_settings(), client=client)
        receipt = await build_decision_receipt_with_signer(
            signer=signer,
            decision=_decision(),
            knowledge_snapshot=[],
            cited_evidence=[],
            audit_chain={"status": "unchecked"},
        )

    signature = receipt["integrity"]["signature"]
    assert set(signature) == {"algorithm", "key_id", "public_key", "value"}
    assert signature["algorithm"] == "ed25519"
    assert not signature["value"].startswith("vault:")
    assert verify_decision_receipt(receipt, require_signature=True)["valid"] is True


@pytest.mark.asyncio
async def test_vault_load_rejects_metadata_public_key_mismatch() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json=_metadata(public_key=base64.b64encode(b"z" * 32).decode("ascii")),
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(ReceiptSigningUnavailable, match="does not match"):
            await create_receipt_signer(_vault_settings(), client=client)


@pytest.mark.asyncio
@pytest.mark.parametrize("returned_version", [PINNED_VERSION - 1, PINNED_VERSION + 1])
async def test_vault_rejects_signature_from_any_unpinned_version(
    returned_version: int,
) -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "GET":
            return httpx.Response(200, json=_metadata())
        body = json.loads(request.content)
        message = base64.b64decode(body["input"], validate=True)
        signature = base64.b64encode(PRIVATE.sign(message)).decode("ascii")
        return httpx.Response(
            200,
            json={"data": {"signature": f"vault:v{returned_version}:{signature}"}},
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        signer = await create_receipt_signer(_vault_settings(), client=client)
        with pytest.raises(ReceiptSigningUnavailable, match="exact pinned key version"):
            await signer.sign_digest(b"d" * 32)


@pytest.mark.asyncio
async def test_vault_locally_rejects_wrong_signature() -> None:
    other_private = Ed25519PrivateKey.generate()

    async def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "GET":
            return httpx.Response(200, json=_metadata())
        body = json.loads(request.content)
        message = base64.b64decode(body["input"], validate=True)
        signature = base64.b64encode(other_private.sign(message)).decode("ascii")
        return httpx.Response(
            200,
            json={"data": {"signature": f"vault:v{PINNED_VERSION}:{signature}"}},
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        signer = await create_receipt_signer(_vault_settings(), client=client)
        with pytest.raises(ReceiptSigningUnavailable, match="local Ed25519 verification"):
            await signer.sign_digest(b"d" * 32)


@pytest.mark.asyncio
async def test_vault_error_does_not_echo_response_or_token() -> None:
    secret = "vault-test-token"

    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(403, json={"errors": [secret, "private-key-material"]})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(ReceiptSigningUnavailable) as raised:
            await create_receipt_signer(_vault_settings(), client=client)
    rendered = str(raised.value)
    assert secret not in rendered
    assert "private-key-material" not in rendered
    assert "HTTP 403" in rendered


@pytest.mark.asyncio
async def test_vault_rejects_writable_token_file_before_network(tmp_path) -> None:
    token_file = tmp_path / "writable-vault-token"
    token_file.write_text("unsafe-token", encoding="ascii")
    token_file.chmod(stat.S_IWRITE | stat.S_IREAD)

    async def handler(request: httpx.Request) -> httpx.Response:
        raise AssertionError("writable token files must fail before a Vault request")

    settings = _vault_settings(
        receipt_vault_token="",
        receipt_vault_token_file=str(token_file.absolute()),
    )
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(ReceiptSigningUnavailable, match="read-only"):
            await create_receipt_signer(settings, client=client)


@pytest.mark.parametrize(
    ("updates", "message"),
    [
        ({"receipt_vault_addr": "http://vault.example.com"}, "HTTPS"),
        ({"receipt_vault_addr": "https://user@vault.example.com"}, "without credentials"),
        ({"receipt_vault_mount_point": "../transit"}, "safe"),
        ({"receipt_vault_key_name": "team/key"}, "safe"),
        ({"receipt_vault_key_version": 0}, "positive integer"),
        ({"receipt_vault_timeout_seconds": 11}, "between 0.25 and 10"),
        ({"receipt_vault_public_key": "not-a-key"}, "raw 32-byte"),
        (
            {"receipt_vault_token_file": "/run/secrets/vault-token"},
            "exactly one",
        ),
        ({"receipt_vault_token": "", "receipt_vault_token_file": "relative"}, "absolute"),
        ({"receipt_vault_namespace": "admin//payments"}, "printable path"),
        ({"receipt_vault_namespace": "admin\npayments"}, "printable path"),
        ({"receipt_vault_namespace": "admin/payments\n"}, "printable path"),
    ],
)
def test_vault_configuration_rejects_unsafe_or_unpinned_values(
    updates: dict[str, object],
    message: str,
) -> None:
    with pytest.raises(ReceiptSignerConfigurationError, match=message):
        validate_receipt_signer_configuration(_vault_settings(**updates))


@pytest.mark.asyncio
async def test_singleton_is_stable_and_has_async_reset_helper() -> None:
    settings = Settings(
        receipt_signing_provider="local",
        receipt_signing_private_key=PRIVATE_B64,
        receipt_signing_key_id="local-receipt-v1",
    )
    await reset_receipt_signer_cache_for_tests()
    first = await load_receipt_signer(settings)
    second = await load_receipt_signer(settings)
    assert first is second
    await reset_receipt_signer_cache_for_tests()
