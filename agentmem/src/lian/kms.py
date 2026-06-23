"""
KMS (Key Management Service) integration for the master encryption key.

Supports four providers, selected via the KMS_PROVIDER environment variable:

  env    — master key loaded from MASTER_ENCRYPTION_KEY env var (default, dev-friendly)
  aws    — AWS KMS envelope encryption: an AES-256 data key is stored encrypted in
           KMS_AWS_ENCRYPTED_KEY (base64 CiphertextBlob); decrypted at startup.
  azure  — Azure Key Vault Secrets: the 32-byte key is stored as a base64 secret
           at KMS_AZURE_VAULT_URL / KMS_AZURE_SECRET_NAME.
  vault  — HashiCorp Vault KV v2: key is stored at KMS_VAULT_PATH under key
           "master_key" (base64) in mount KMS_VAULT_MOUNT_POINT.

Usage (FastAPI / any async app)
--------------------------------
    from agentmem.kms import load_master_key

    @app.on_event("startup")
    async def startup():
        await load_master_key()

Usage (synchronous / local)
----------------------------
    # For kms_provider="env" only — runs load synchronously at object creation.
    # For cloud providers, wrap in asyncio.run() before creating the client.

Key rotation
------------
Re-start the process (or call load_master_key() again after _reset_cache()) to
pick up a new key.  Existing wrapped subject keys were encrypted with the old
master key; re-wrap them using rotate_master_key() after setting the new key.
"""
from __future__ import annotations

import asyncio
import base64
import logging
from typing import Optional

logger = logging.getLogger("agentmem.kms")

# Module-level cache — populated by load_master_key(), never written after that.
_master_key_cache: Optional[bytes] = None


# ── Public API ────────────────────────────────────────────────────────────────

def get_master_key() -> bytes:
    """Return the cached 32-byte master encryption key.

    For kms_provider='env': falls back to reading the env var synchronously
    if load_master_key() has not yet been called — safe for tests and scripts.

    For all other providers: raises RuntimeError unless load_master_key() was
    called first (typically in the app lifespan / __init__ of the local client).
    """
    if _master_key_cache is not None:
        return _master_key_cache

    from .config import get_settings
    settings = get_settings()
    if settings.kms_provider == "env":
        return _env_key(settings)
    raise RuntimeError(
        f"KMS provider {settings.kms_provider!r} requires load_master_key() to be "
        "awaited at startup before get_master_key() is called."
    )


async def load_master_key() -> None:
    """Fetch and cache the master encryption key from the configured KMS provider.

    Idempotent — subsequent calls are no-ops once the key is cached.
    Call once in the application lifespan / client __init__.
    """
    global _master_key_cache
    if _master_key_cache is not None:
        return  # already loaded

    from .config import get_settings
    settings = get_settings()
    key = await _fetch(settings)
    _validate_key(key)
    _master_key_cache = key
    logger.info("Master key loaded", extra={"provider": settings.kms_provider})


def _reset_cache() -> None:
    """Clear the cached master key — for testing only."""
    global _master_key_cache
    _master_key_cache = None


# ── Internal dispatch ─────────────────────────────────────────────────────────

async def _fetch(settings) -> bytes:
    provider = settings.kms_provider
    if provider == "env":
        return _env_key(settings)
    elif provider == "aws":
        return await _from_aws(settings)
    elif provider == "azure":
        return await _from_azure(settings)
    elif provider == "vault":
        return await _from_vault(settings)
    else:
        raise ValueError(
            f"Unknown KMS provider {provider!r}. "
            "Valid values: env, aws, azure, vault"
        )


def _validate_key(key: bytes) -> None:
    if len(key) != 32:
        raise ValueError(
            f"Master encryption key must be exactly 32 bytes, got {len(key)}. "
            "Ensure the KMS provider returns a 256-bit AES key."
        )


# ── Provider: env ─────────────────────────────────────────────────────────────

def _env_key(settings) -> bytes:
    raw = settings.master_encryption_key
    if not raw:
        # Allow an explicit opt-out for unit tests and local dev (never set in prod).
        import os
        if os.getenv("AGENTMEM_ALLOW_UNENCRYPTED", "").lower() in ("1", "true", "yes"):
            logger.warning(
                "MASTER_ENCRYPTION_KEY is not set and AGENTMEM_ALLOW_UNENCRYPTED=true. "
                "All content will be stored with a zero encryption key. "
                "NEVER use this setting with real data."
            )
            return b"\x00" * 32
        raise RuntimeError(
            "MASTER_ENCRYPTION_KEY is not set. "
            "AgentMem cannot start without an encryption key because storing financial "
            "data with a predictable key violates the GDPR crypto-shred guarantee.\n\n"
            "Generate a key:\n"
            "  python -c \"import secrets,base64; "
            "print(base64.b64encode(secrets.token_bytes(32)).decode())\"\n\n"
            "Then set it as MASTER_ENCRYPTION_KEY in your .env or secrets manager.\n\n"
            "For local development or unit tests only, set "
            "AGENTMEM_ALLOW_UNENCRYPTED=true to bypass this check."
        )
    return base64.b64decode(raw)


# ── Provider: AWS KMS ─────────────────────────────────────────────────────────

async def _from_aws(settings) -> bytes:
    """Decrypt an AES-256 data key stored as a KMS CiphertextBlob.

    One-time setup:
        aws kms generate-data-key \\
            --key-id <CMK_ARN_OR_ALIAS> \\
            --key-spec AES_256 \\
            --query CiphertextBlob \\
            --output text | base64 -d | base64 > encrypted_dek.b64
        # Set KMS_AWS_ENCRYPTED_KEY to the contents of encrypted_dek.b64
        # The PlaintextBlob from generate-data-key was used to encrypt historical
        # subject keys; it must be discarded after initial setup.
    """
    if not settings.kms_aws_encrypted_key:
        raise ValueError(
            "KMS_AWS_ENCRYPTED_KEY must be set when KMS_PROVIDER=aws. "
            "See the docstring in agentmem/kms.py for setup instructions."
        )

    try:
        import boto3
    except ImportError as exc:
        raise ImportError(
            "boto3 is required for KMS_PROVIDER=aws. "
            "Install with: pip install boto3  (or pip install agentmem[aws])"
        ) from exc

    encrypted_dek = base64.b64decode(settings.kms_aws_encrypted_key)
    region = settings.kms_aws_region or None

    decrypt_kwargs: dict = {"CiphertextBlob": encrypted_dek}
    if settings.kms_aws_key_id:
        decrypt_kwargs["KeyId"] = settings.kms_aws_key_id

    def _call() -> bytes:
        client = boto3.client("kms", region_name=region)
        response = client.decrypt(**decrypt_kwargs)
        return bytes(response["Plaintext"])

    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(None, _call)


# ── Provider: Azure Key Vault ─────────────────────────────────────────────────

async def _from_azure(settings) -> bytes:
    """Retrieve the master key stored as a base64 secret in Azure Key Vault.

    One-time setup:
        az keyvault secret set \\
            --vault-name <VAULT_NAME> \\
            --name <KMS_AZURE_SECRET_NAME> \\
            --value $(python -c "import os,base64; print(base64.b64encode(os.urandom(32)).decode())")
        # Set KMS_AZURE_VAULT_URL=https://<VAULT_NAME>.vault.azure.net/
    """
    if not settings.kms_azure_vault_url:
        raise ValueError(
            "KMS_AZURE_VAULT_URL must be set when KMS_PROVIDER=azure. "
            "Example: https://myvault.vault.azure.net/"
        )

    try:
        from azure.keyvault.secrets.aio import SecretClient  # type: ignore[import]
        from azure.identity.aio import DefaultAzureCredential  # type: ignore[import]
    except ImportError as exc:
        raise ImportError(
            "azure-keyvault-secrets and azure-identity are required for KMS_PROVIDER=azure. "
            "Install with: pip install azure-keyvault-secrets azure-identity  "
            "(or pip install agentmem[azure])"
        ) from exc

    credential = DefaultAzureCredential()
    client = SecretClient(
        vault_url=settings.kms_azure_vault_url,
        credential=credential,
    )
    try:
        secret = await client.get_secret(settings.kms_azure_secret_name)
        return base64.b64decode(secret.value)
    finally:
        await client.close()
        await credential.close()


# ── Provider: HashiCorp Vault ─────────────────────────────────────────────────

async def _from_vault(settings) -> bytes:
    """Read the master key from HashiCorp Vault KV v2.

    One-time setup:
        vault kv put <KMS_VAULT_MOUNT_POINT>/<KMS_VAULT_PATH> \\
            master_key=$(python -c "import os,base64; print(base64.b64encode(os.urandom(32)).decode())")
    """
    if not settings.kms_vault_token:
        raise ValueError(
            "KMS_VAULT_TOKEN must be set when KMS_PROVIDER=vault. "
            "Alternatively, configure AppRole or Kubernetes auth before calling load_master_key()."
        )

    try:
        import hvac  # type: ignore[import]
    except ImportError as exc:
        raise ImportError(
            "hvac is required for KMS_PROVIDER=vault. "
            "Install with: pip install hvac  (or pip install agentmem[vault])"
        ) from exc

    def _read() -> str:
        client = hvac.Client(
            url=settings.kms_vault_addr,
            token=settings.kms_vault_token,
        )
        response = client.secrets.kv.v2.read_secret_version(
            path=settings.kms_vault_path,
            mount_point=settings.kms_vault_mount_point,
        )
        return response["data"]["data"]["master_key"]

    loop = asyncio.get_event_loop()
    key_b64: str = await loop.run_in_executor(None, _read)
    return base64.b64decode(key_b64)
