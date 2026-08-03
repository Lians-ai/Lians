"""Validated dual-key master-key loading for Lians envelope encryption.

The selected provider supplies one *current* AES-256 key and, during a
rotation window, at most one explicitly configured *previous* key.  New writes
always use the current key.  Readers may use the previous key only for a
self-identifying previous-key envelope or as the second and final candidate
for legacy v1 values.

Key identifiers are non-secret, stable operator labels.  They are embedded in
v2 envelopes and therefore must be safe, short ASCII values.  Key material is
never logged or persisted by this module.
"""
from __future__ import annotations

import asyncio
import base64
import binascii
import logging
import re
from dataclasses import dataclass

logger = logging.getLogger("lians.kms")

SAFE_MASTER_KEY_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")
DEVELOPMENT_KEY_ID = "development-key"


class MasterKeyConfigurationError(ValueError):
    """The configured keyring is ambiguous, incomplete, or unsafe."""


@dataclass(frozen=True, slots=True)
class MasterKeyVersion:
    key_id: str
    material: bytes


@dataclass(frozen=True, slots=True)
class MasterKeyring:
    provider: str
    current: MasterKeyVersion
    previous: MasterKeyVersion | None = None

    @property
    def candidates(self) -> tuple[MasterKeyVersion, ...]:
        """Bounded candidates for legacy values: current, then previous."""
        return (self.current,) if self.previous is None else (self.current, self.previous)

    def by_id(self, key_id: str) -> MasterKeyVersion | None:
        if self.current.key_id == key_id:
            return self.current
        if self.previous is not None and self.previous.key_id == key_id:
            return self.previous
        return None


# Populated once during startup and never changed in-process.  A rotation is a
# configuration rollout followed by a process restart, not a live cache swap.
_master_keyring_cache: MasterKeyring | None = None
# Compatibility for callers/tests that introspect the former single-key path.
_master_key_cache: bytes | None = None


def validate_master_key_id(value: str, *, field: str = "MASTER_KEY_ID") -> str:
    key_id = value.strip()
    if not SAFE_MASTER_KEY_ID_RE.fullmatch(key_id):
        raise MasterKeyConfigurationError(
            f"{field} must be 1-64 ASCII letters, numbers, '.', '_', or '-', "
            "starting with a letter or number"
        )
    return key_id


def _is_production(settings) -> bool:
    return settings.deployment_environment.strip().lower() in {"prod", "production"}


def _configured_ids(settings) -> tuple[str, str | None]:
    raw_current = settings.master_key_id.strip()
    if not raw_current:
        if _is_production(settings):
            raise MasterKeyConfigurationError(
                "MASTER_KEY_ID is required in production and must identify the current key version"
            )
        raw_current = DEVELOPMENT_KEY_ID
    current_id = validate_master_key_id(raw_current)
    if _is_production(settings) and current_id.lower() in {
        DEVELOPMENT_KEY_ID,
        "configure-me",
        "change-me",
        "default",
    }:
        raise MasterKeyConfigurationError(
            "MASTER_KEY_ID must be an operator-assigned production key version, not a placeholder"
        )

    raw_previous = settings.master_key_previous_id.strip()
    previous_id = (
        validate_master_key_id(raw_previous, field="MASTER_KEY_PREVIOUS_ID")
        if raw_previous
        else None
    )
    if previous_id == current_id:
        raise MasterKeyConfigurationError(
            "MASTER_KEY_PREVIOUS_ID must differ from MASTER_KEY_ID"
        )
    return current_id, previous_id


def _previous_material_presence(settings) -> dict[str, bool]:
    return {
        "env": bool(settings.master_encryption_key_previous.strip()),
        "aws": bool(
            settings.kms_aws_previous_encrypted_key.strip()
            or settings.kms_aws_previous_key_id.strip()
            or settings.kms_aws_previous_region.strip()
        ),
        "azure": bool(
            settings.kms_azure_previous_vault_url.strip()
            or settings.kms_azure_previous_secret_name.strip()
        ),
        "vault": bool(
            settings.kms_vault_previous_addr.strip()
            or settings.kms_vault_previous_path.strip()
            or settings.kms_vault_previous_mount_point.strip()
        ),
    }


def validate_keyring_configuration(settings) -> tuple[str, str | None]:
    """Validate identifiers and the selected provider's previous-key slot.

    This performs no network access and is safe to call from startup validation
    and readiness reporting.
    """
    provider = settings.kms_provider.strip().lower()
    if provider not in {"env", "aws", "azure", "vault"}:
        raise MasterKeyConfigurationError(
            f"Unknown KMS provider {provider!r}. Valid values: env, aws, azure, vault"
        )
    current_id, previous_id = _configured_ids(settings)
    presence = _previous_material_presence(settings)

    stray = sorted(name for name, configured in presence.items() if configured and name != provider)
    if stray:
        raise MasterKeyConfigurationError(
            "Previous-key material is configured for a provider other than KMS_PROVIDER: "
            + ", ".join(stray)
        )
    selected_present = presence[provider]
    if previous_id is None and selected_present:
        raise MasterKeyConfigurationError(
            "Previous-key material is set without MASTER_KEY_PREVIOUS_ID"
        )
    if previous_id is not None and not selected_present:
        raise MasterKeyConfigurationError(
            f"MASTER_KEY_PREVIOUS_ID requires previous-key material for KMS_PROVIDER={provider}"
        )

    if previous_id is not None:
        if provider == "aws" and not settings.kms_aws_previous_encrypted_key.strip():
            raise MasterKeyConfigurationError(
                "KMS_AWS_PREVIOUS_ENCRYPTED_KEY is required for the previous key"
            )
        if provider == "azure" and not settings.kms_azure_previous_secret_name.strip():
            raise MasterKeyConfigurationError(
                "KMS_AZURE_PREVIOUS_SECRET_NAME is required for the previous key"
            )
        if provider == "vault" and not settings.kms_vault_previous_path.strip():
            raise MasterKeyConfigurationError(
                "KMS_VAULT_PREVIOUS_PATH is required for the previous key"
            )
    return current_id, previous_id


def get_master_keyring() -> MasterKeyring:
    """Return the loaded keyring without ever fetching a cloud key synchronously."""
    if _master_keyring_cache is not None:
        return _master_keyring_cache

    from .config import get_settings

    settings = get_settings()
    if settings.kms_provider.strip().lower() != "env":
        raise RuntimeError(
            f"KMS provider {settings.kms_provider!r} requires load_master_key() to be "
            "awaited at startup before get_master_keyring() is called."
        )
    return _load_env_keyring(settings)


def get_master_key() -> bytes:
    """Compatibility API returning the current 32-byte master key."""
    if _master_key_cache is not None:
        return _master_key_cache
    return get_master_keyring().current.material


async def load_master_key() -> None:
    """Fetch, validate, and cache the bounded current/previous keyring."""
    global _master_keyring_cache, _master_key_cache
    if _master_keyring_cache is not None:
        return

    from .config import get_settings

    settings = get_settings()
    current_id, previous_id = validate_keyring_configuration(settings)
    current_material = await _fetch(settings)
    _validate_key(current_material)
    previous_material = None
    if previous_id is not None:
        previous_material = await _fetch_previous(settings)
        _validate_key(previous_material)
        if previous_material == current_material:
            raise MasterKeyConfigurationError(
                "Current and previous master-key material must be different"
            )

    ring = MasterKeyring(
        provider=settings.kms_provider.strip().lower(),
        current=MasterKeyVersion(current_id, current_material),
        previous=(
            MasterKeyVersion(previous_id, previous_material)
            if previous_id is not None and previous_material is not None
            else None
        ),
    )
    _master_keyring_cache = ring
    _master_key_cache = ring.current.material
    logger.info(
        "Master keyring loaded",
        extra={
            "provider": ring.provider,
            "current_key_id": ring.current.key_id,
            "previous_key_configured": ring.previous is not None,
        },
    )


def _load_env_keyring(settings) -> MasterKeyring:
    current_id, previous_id = validate_keyring_configuration(settings)
    current = _env_key(settings)
    _validate_key(current)
    previous = _env_key(settings, previous=True) if previous_id is not None else None
    if previous is not None:
        _validate_key(previous)
        if previous == current:
            raise MasterKeyConfigurationError(
                "Current and previous master-key material must be different"
            )
    return MasterKeyring(
        provider="env",
        current=MasterKeyVersion(current_id, current),
        previous=(MasterKeyVersion(previous_id, previous) if previous_id and previous else None),
    )


def _reset_cache() -> None:
    """Clear cached key material. Intended only for isolated tests."""
    global _master_keyring_cache, _master_key_cache
    _master_keyring_cache = None
    _master_key_cache = None


async def _fetch(settings) -> bytes:
    provider = settings.kms_provider.strip().lower()
    if provider == "env":
        return _env_key(settings)
    if provider == "aws":
        return await _from_aws(settings)
    if provider == "azure":
        return await _from_azure(settings)
    if provider == "vault":
        return await _from_vault(settings)
    raise MasterKeyConfigurationError(
        f"Unknown KMS provider {provider!r}. Valid values: env, aws, azure, vault"
    )


async def _fetch_previous(settings) -> bytes:
    provider = settings.kms_provider.strip().lower()
    if provider == "env":
        return _env_key(settings, previous=True)
    if provider == "aws":
        return await _from_aws(settings, previous=True)
    if provider == "azure":
        return await _from_azure(settings, previous=True)
    if provider == "vault":
        return await _from_vault(settings, previous=True)
    raise MasterKeyConfigurationError(f"Unknown KMS provider {provider!r}")


def _validate_key(key: bytes) -> None:
    if len(key) != 32:
        raise MasterKeyConfigurationError(
            f"Master encryption key must be exactly 32 bytes, got {len(key)}. "
            "Ensure the KMS provider returns a 256-bit AES key."
        )


def _decode_base64_key(raw: str, *, field: str) -> bytes:
    try:
        return base64.b64decode(raw, validate=True)
    except (binascii.Error, ValueError) as exc:
        raise MasterKeyConfigurationError(f"{field} must be valid base64") from exc


def _env_key(settings, *, previous: bool = False) -> bytes:
    raw = (
        settings.master_encryption_key_previous
        if previous
        else settings.master_encryption_key
    )
    field = "MASTER_ENCRYPTION_KEY_PREVIOUS" if previous else "MASTER_ENCRYPTION_KEY"
    if not raw:
        if not previous:
            import os

            if os.getenv("AGENTMEM_ALLOW_UNENCRYPTED", "").lower() in {"1", "true", "yes"}:
                logger.warning(
                    "MASTER_ENCRYPTION_KEY is absent with the local test bypass enabled; "
                    "never use this posture with real data"
                )
                return b"\x00" * 32
        raise MasterKeyConfigurationError(
            f"{field} is not set. Lians cannot load the configured master-key slot."
        )
    return _decode_base64_key(raw, field=field)


async def _from_aws(settings, *, previous: bool = False) -> bytes:
    encrypted_value = (
        settings.kms_aws_previous_encrypted_key
        if previous
        else settings.kms_aws_encrypted_key
    )
    encrypted_field = (
        "KMS_AWS_PREVIOUS_ENCRYPTED_KEY" if previous else "KMS_AWS_ENCRYPTED_KEY"
    )
    if not encrypted_value:
        raise MasterKeyConfigurationError(
            f"{encrypted_field} must be set when KMS_PROVIDER=aws"
        )
    try:
        import boto3
    except ImportError as exc:
        raise ImportError(
            "boto3 is required for KMS_PROVIDER=aws; install lians[aws]"
        ) from exc

    encrypted_dek = _decode_base64_key(encrypted_value, field=encrypted_field)
    region = (
        settings.kms_aws_previous_region.strip()
        if previous and settings.kms_aws_previous_region.strip()
        else settings.kms_aws_region.strip()
    ) or None
    key_id = (
        settings.kms_aws_previous_key_id
        if previous
        else settings.kms_aws_key_id
    ).strip()
    decrypt_kwargs: dict = {"CiphertextBlob": encrypted_dek}
    if key_id:
        decrypt_kwargs["KeyId"] = key_id

    def _call() -> bytes:
        client = boto3.client("kms", region_name=region)
        response = client.decrypt(**decrypt_kwargs)
        return bytes(response["Plaintext"])

    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(None, _call)


async def _from_azure(settings, *, previous: bool = False) -> bytes:
    vault_url = (
        settings.kms_azure_previous_vault_url.strip()
        if previous and settings.kms_azure_previous_vault_url.strip()
        else settings.kms_azure_vault_url.strip()
    )
    secret_name = (
        settings.kms_azure_previous_secret_name
        if previous
        else settings.kms_azure_secret_name
    ).strip()
    if not vault_url:
        field = "KMS_AZURE_PREVIOUS_VAULT_URL" if previous else "KMS_AZURE_VAULT_URL"
        raise MasterKeyConfigurationError(f"{field} must be set when KMS_PROVIDER=azure")
    if not secret_name:
        field = "KMS_AZURE_PREVIOUS_SECRET_NAME" if previous else "KMS_AZURE_SECRET_NAME"
        raise MasterKeyConfigurationError(f"{field} must be set when KMS_PROVIDER=azure")
    try:
        from azure.identity.aio import DefaultAzureCredential  # type: ignore[import]
        from azure.keyvault.secrets.aio import SecretClient  # type: ignore[import]
    except ImportError as exc:
        raise ImportError(
            "azure-keyvault-secrets and azure-identity are required for "
            "KMS_PROVIDER=azure; install lians[azure]"
        ) from exc

    credential = DefaultAzureCredential()
    client = SecretClient(vault_url=vault_url, credential=credential)
    try:
        secret = await client.get_secret(secret_name)
        if not isinstance(secret.value, str):
            raise MasterKeyConfigurationError("Azure Key Vault secret has no string value")
        return _decode_base64_key(secret.value, field="Azure Key Vault master-key secret")
    finally:
        await client.close()
        await credential.close()


async def _from_vault(settings, *, previous: bool = False) -> bytes:
    if not settings.kms_vault_token:
        raise MasterKeyConfigurationError(
            "KMS_VAULT_TOKEN must be set when KMS_PROVIDER=vault"
        )
    try:
        import hvac  # type: ignore[import]
    except ImportError as exc:
        raise ImportError(
            "hvac is required for KMS_PROVIDER=vault; install lians[vault]"
        ) from exc

    address = (
        settings.kms_vault_previous_addr.strip()
        if previous and settings.kms_vault_previous_addr.strip()
        else settings.kms_vault_addr.strip()
    )
    path = (
        settings.kms_vault_previous_path
        if previous
        else settings.kms_vault_path
    ).strip()
    mount = (
        settings.kms_vault_previous_mount_point.strip()
        if previous and settings.kms_vault_previous_mount_point.strip()
        else settings.kms_vault_mount_point.strip()
    )
    if not path:
        field = "KMS_VAULT_PREVIOUS_PATH" if previous else "KMS_VAULT_PATH"
        raise MasterKeyConfigurationError(f"{field} must be set when KMS_PROVIDER=vault")

    def _read() -> str:
        client = hvac.Client(url=address, token=settings.kms_vault_token)
        response = client.secrets.kv.v2.read_secret_version(
            path=path,
            mount_point=mount,
        )
        value = response["data"]["data"]["master_key"]
        if not isinstance(value, str):
            raise MasterKeyConfigurationError("Vault master_key value is not a string")
        return value

    loop = asyncio.get_running_loop()
    key_b64 = await loop.run_in_executor(None, _read)
    return _decode_base64_key(key_b64, field="Vault master_key")
