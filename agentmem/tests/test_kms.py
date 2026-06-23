"""
Tests for the KMS master-key integration.

All cloud providers (AWS, Azure, Vault) are exercised with mocks â€” no external
network calls are made.  The env provider is tested against real in-process logic.
"""
from __future__ import annotations

import base64
import os
import pytest
import pytest_asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import src.lian.kms as kms
from src.lian.kms import (
    get_master_key,
    load_master_key,
    _reset_cache,
    _env_key,
)
from src.lian.config import get_settings


# Helpers
_SAMPLE_KEY = os.urandom(32)
_SAMPLE_KEY_B64 = base64.b64encode(_SAMPLE_KEY).decode()


@pytest.fixture(autouse=True)
def reset_kms():
    """Each test starts with a clean KMS cache and settings."""
    _reset_cache()
    yield
    _reset_cache()


# â”€â”€ env provider â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

class TestEnvProvider:

    def test_env_key_with_real_key(self, monkeypatch):
        monkeypatch.setenv("MASTER_ENCRYPTION_KEY", _SAMPLE_KEY_B64)
        get_settings.cache_clear()
        settings = get_settings()
        assert _env_key(settings) == _SAMPLE_KEY

    def test_env_key_empty_returns_zero_key(self, monkeypatch):
        monkeypatch.setenv("MASTER_ENCRYPTION_KEY", "")
        get_settings.cache_clear()
        settings = get_settings()
        result = _env_key(settings)
        assert result == b"\x00" * 32

    async def test_load_master_key_env(self, monkeypatch):
        monkeypatch.setenv("MASTER_ENCRYPTION_KEY", _SAMPLE_KEY_B64)
        monkeypatch.setenv("KMS_PROVIDER", "env")
        get_settings.cache_clear()
        _reset_cache()

        await load_master_key()
        assert get_master_key() == _SAMPLE_KEY

    def test_get_master_key_env_no_prior_load(self, monkeypatch):
        """env provider works without an explicit load_master_key() call."""
        monkeypatch.setenv("MASTER_ENCRYPTION_KEY", _SAMPLE_KEY_B64)
        monkeypatch.setenv("KMS_PROVIDER", "env")
        get_settings.cache_clear()
        _reset_cache()

        result = get_master_key()
        assert result == _SAMPLE_KEY

    async def test_load_is_idempotent(self, monkeypatch):
        monkeypatch.setenv("MASTER_ENCRYPTION_KEY", _SAMPLE_KEY_B64)
        monkeypatch.setenv("KMS_PROVIDER", "env")
        get_settings.cache_clear()
        _reset_cache()

        await load_master_key()
        first = get_master_key()
        await load_master_key()  # second call â€” should be a no-op
        assert get_master_key() is first

    def test_get_master_key_non_env_without_load_raises(self, monkeypatch):
        monkeypatch.setenv("KMS_PROVIDER", "aws")
        get_settings.cache_clear()
        _reset_cache()

        with pytest.raises(RuntimeError, match="load_master_key"):
            get_master_key()

    async def test_unknown_provider_raises(self, monkeypatch):
        monkeypatch.setenv("KMS_PROVIDER", "unknown-provider")
        get_settings.cache_clear()
        _reset_cache()

        with pytest.raises(ValueError, match="Unknown KMS provider"):
            await load_master_key()


# â”€â”€ AWS KMS provider â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

class TestAwsProvider:

    def _make_settings(self, monkeypatch):
        monkeypatch.setenv("KMS_PROVIDER", "aws")
        monkeypatch.setenv("KMS_AWS_KEY_ID", "arn:aws:kms:us-east-1:123456789:key/test-key")
        monkeypatch.setenv("KMS_AWS_REGION", "us-east-1")
        monkeypatch.setenv("KMS_AWS_ENCRYPTED_KEY", base64.b64encode(b"mock-ciphertext").decode())
        get_settings.cache_clear()
        _reset_cache()

    async def test_load_master_key_aws(self, monkeypatch):
        self._make_settings(monkeypatch)

        mock_client = MagicMock()
        mock_client.decrypt.return_value = {"Plaintext": _SAMPLE_KEY}
        mock_boto3 = MagicMock()
        mock_boto3.client.return_value = mock_client

        with patch.dict("sys.modules", {"boto3": mock_boto3}):
            await load_master_key()

        mock_boto3.client.assert_called_once_with("kms", region_name="us-east-1")
        mock_client.decrypt.assert_called_once()
        call_kwargs = mock_client.decrypt.call_args[1]
        assert call_kwargs["CiphertextBlob"] == b"mock-ciphertext"
        assert call_kwargs["KeyId"] == "arn:aws:kms:us-east-1:123456789:key/test-key"

        assert get_master_key() == _SAMPLE_KEY

    async def test_aws_key_id_is_optional(self, monkeypatch):
        monkeypatch.setenv("KMS_PROVIDER", "aws")
        monkeypatch.setenv("KMS_AWS_KEY_ID", "")   # omitted
        monkeypatch.setenv("KMS_AWS_REGION", "eu-west-1")
        monkeypatch.setenv("KMS_AWS_ENCRYPTED_KEY", base64.b64encode(b"ct").decode())
        get_settings.cache_clear()
        _reset_cache()

        mock_client = MagicMock()
        mock_client.decrypt.return_value = {"Plaintext": _SAMPLE_KEY}
        mock_boto3 = MagicMock()
        mock_boto3.client.return_value = mock_client

        with patch.dict("sys.modules", {"boto3": mock_boto3}):
            await load_master_key()

        call_kwargs = mock_client.decrypt.call_args[1]
        assert "KeyId" not in call_kwargs  # not passed when empty

    async def test_aws_missing_encrypted_key_raises(self, monkeypatch):
        monkeypatch.setenv("KMS_PROVIDER", "aws")
        monkeypatch.setenv("KMS_AWS_ENCRYPTED_KEY", "")
        get_settings.cache_clear()
        _reset_cache()

        # ValueError fires before boto3 is imported â€” no boto3 mock needed
        with pytest.raises(ValueError, match="KMS_AWS_ENCRYPTED_KEY"):
            await load_master_key()

    async def test_aws_wrong_key_length_raises(self, monkeypatch):
        self._make_settings(monkeypatch)

        mock_client = MagicMock()
        mock_client.decrypt.return_value = {"Plaintext": b"too-short"}
        mock_boto3 = MagicMock()
        mock_boto3.client.return_value = mock_client

        with patch.dict("sys.modules", {"boto3": mock_boto3}):
            with pytest.raises(ValueError, match="32 bytes"):
                await load_master_key()

    async def test_aws_missing_boto3_raises_import_error(self, monkeypatch):
        self._make_settings(monkeypatch)

        with patch.dict("sys.modules", {"boto3": None}):
            with pytest.raises(ImportError, match="boto3"):
                await load_master_key()


# â”€â”€ Azure Key Vault provider â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

class TestAzureProvider:

    def _make_settings(self, monkeypatch):
        monkeypatch.setenv("KMS_PROVIDER", "azure")
        monkeypatch.setenv("KMS_AZURE_VAULT_URL", "https://test.vault.azure.net/")
        monkeypatch.setenv("KMS_AZURE_SECRET_NAME", "agentmem-master-key")
        get_settings.cache_clear()
        _reset_cache()

    async def test_load_master_key_azure(self, monkeypatch):
        self._make_settings(monkeypatch)

        mock_secret = MagicMock()
        mock_secret.value = _SAMPLE_KEY_B64

        mock_client = AsyncMock()
        mock_client.get_secret = AsyncMock(return_value=mock_secret)
        mock_client.close = AsyncMock()

        mock_credential = AsyncMock()
        mock_credential.close = AsyncMock()

        # Build a fake azure package hierarchy in sys.modules
        mock_azure_kv_aio = MagicMock()
        mock_azure_kv_aio.SecretClient.return_value = mock_client
        mock_azure_id_aio = MagicMock()
        mock_azure_id_aio.DefaultAzureCredential.return_value = mock_credential

        with patch.dict("sys.modules", {
            "azure": MagicMock(),
            "azure.keyvault": MagicMock(),
            "azure.keyvault.secrets": MagicMock(),
            "azure.keyvault.secrets.aio": mock_azure_kv_aio,
            "azure.identity": MagicMock(),
            "azure.identity.aio": mock_azure_id_aio,
        }):
            await load_master_key()

        mock_client.get_secret.assert_called_once_with("agentmem-master-key")
        mock_client.close.assert_called_once()
        mock_credential.close.assert_called_once()
        assert get_master_key() == _SAMPLE_KEY

    async def test_azure_missing_vault_url_raises(self, monkeypatch):
        monkeypatch.setenv("KMS_PROVIDER", "azure")
        monkeypatch.setenv("KMS_AZURE_VAULT_URL", "")
        get_settings.cache_clear()
        _reset_cache()

        # ValueError fires before Azure SDK is imported â€” no azure mock needed
        with pytest.raises(ValueError, match="KMS_AZURE_VAULT_URL"):
            await load_master_key()

    async def test_azure_missing_sdk_raises_import_error(self, monkeypatch):
        self._make_settings(monkeypatch)

        with patch.dict("sys.modules", {
            "azure": MagicMock(),
            "azure.keyvault": MagicMock(),
            "azure.keyvault.secrets": MagicMock(),
            "azure.keyvault.secrets.aio": None,
            "azure.identity": MagicMock(),
            "azure.identity.aio": None,
        }):
            with pytest.raises(ImportError, match="azure-keyvault-secrets"):
                await load_master_key()


# â”€â”€ HashiCorp Vault provider â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

class TestVaultProvider:

    def _make_settings(self, monkeypatch):
        monkeypatch.setenv("KMS_PROVIDER", "vault")
        monkeypatch.setenv("KMS_VAULT_ADDR", "http://127.0.0.1:8200")
        monkeypatch.setenv("KMS_VAULT_TOKEN", "root-token")
        monkeypatch.setenv("KMS_VAULT_PATH", "agentmem/master-key")
        monkeypatch.setenv("KMS_VAULT_MOUNT_POINT", "secret")
        get_settings.cache_clear()
        _reset_cache()

    async def test_load_master_key_vault(self, monkeypatch):
        self._make_settings(monkeypatch)

        vault_response = {
            "data": {
                "data": {"master_key": _SAMPLE_KEY_B64}
            }
        }

        mock_kv = MagicMock()
        mock_kv.v2.read_secret_version.return_value = vault_response

        mock_hvac_client = MagicMock()
        mock_hvac_client.secrets.kv = mock_kv

        mock_hvac = MagicMock()
        mock_hvac.Client.return_value = mock_hvac_client

        with patch.dict("sys.modules", {"hvac": mock_hvac}):
            await load_master_key()

        mock_hvac.Client.assert_called_once_with(
            url="http://127.0.0.1:8200",
            token="root-token",
        )
        mock_kv.v2.read_secret_version.assert_called_once_with(
            path="agentmem/master-key",
            mount_point="secret",
        )
        assert get_master_key() == _SAMPLE_KEY

    async def test_vault_missing_token_raises(self, monkeypatch):
        monkeypatch.setenv("KMS_PROVIDER", "vault")
        monkeypatch.setenv("KMS_VAULT_TOKEN", "")
        get_settings.cache_clear()
        _reset_cache()

        # ValueError fires before hvac is imported â€” no hvac mock needed
        with pytest.raises(ValueError, match="KMS_VAULT_TOKEN"):
            await load_master_key()

    async def test_vault_missing_hvac_raises_import_error(self, monkeypatch):
        self._make_settings(monkeypatch)

        with patch.dict("sys.modules", {"hvac": None}):
            with pytest.raises(ImportError, match="hvac"):
                await load_master_key()


# â”€â”€ Integration: KMS key used in crypto wrap/unwrap â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

class TestKmsIntegration:

    async def test_aws_key_used_in_crypto_wrap_unwrap(self, monkeypatch):
        """End-to-end: key fetched from (mocked) AWS KMS â†’ wraps/unwraps a subject key."""
        from src.lian.crypto import wrap_subject_key, unwrap_subject_key, generate_subject_key

        monkeypatch.setenv("KMS_PROVIDER", "aws")
        monkeypatch.setenv("KMS_AWS_ENCRYPTED_KEY", base64.b64encode(b"ct").decode())
        monkeypatch.setenv("KMS_AWS_REGION", "us-east-1")
        get_settings.cache_clear()
        _reset_cache()

        mock_client = MagicMock()
        mock_client.decrypt.return_value = {"Plaintext": _SAMPLE_KEY}
        mock_boto3 = MagicMock()
        mock_boto3.client.return_value = mock_client

        with patch.dict("sys.modules", {"boto3": mock_boto3}):
            await load_master_key()

        subject_key = generate_subject_key()
        wrapped = wrap_subject_key(subject_key)
        unwrapped = unwrap_subject_key(wrapped)
        assert unwrapped == subject_key

    async def test_key_change_invalidates_wrapped_keys(self, monkeypatch):
        """Wrapping with key A and unwrapping with key B raises an error (crypto shred)."""
        from cryptography.exceptions import InvalidTag
        from src.lian.crypto import wrap_subject_key, unwrap_subject_key, generate_subject_key

        key_a = os.urandom(32)
        key_b = os.urandom(32)

        # Wrap with key A
        monkeypatch.setenv("KMS_PROVIDER", "env")
        monkeypatch.setenv("MASTER_ENCRYPTION_KEY", base64.b64encode(key_a).decode())
        get_settings.cache_clear()
        _reset_cache()
        await load_master_key()

        subject_key = generate_subject_key()
        wrapped = wrap_subject_key(subject_key)

        # Switch master key to B â€” simulates a key rotation without re-wrapping
        _reset_cache()
        monkeypatch.setenv("MASTER_ENCRYPTION_KEY", base64.b64encode(key_b).decode())
        get_settings.cache_clear()
        _reset_cache()
        await load_master_key()

        with pytest.raises(InvalidTag):
            unwrap_subject_key(wrapped)
