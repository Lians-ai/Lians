"""Fail-closed Decision Receipt signing providers.

The portable Decision Receipt v0.1 contract always signs the 32-byte SHA-256
receipt digest as the Ed25519 message.  A local provider preserves the original
raw-key behavior.  The Vault Transit provider keeps private material outside
the API process and binds every operation to an operator-pinned key version and
raw public key.
"""

from __future__ import annotations

import asyncio
import base64
import binascii
import hashlib
import json
import math
import os
import re
import secrets
import stat
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol
from urllib.parse import quote, urlsplit, urlunsplit

import httpx
from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import (
    Ed25519PrivateKey,
    Ed25519PublicKey,
)

_SAFE_IDENTIFIER = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$", re.ASCII)
_SAFE_VAULT_SEGMENT = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$", re.ASCII)
_VAULT_SIGNATURE = re.compile(
    r"^vault:v([1-9][0-9]*):([A-Za-z0-9+/]+={0,2})$", re.ASCII
)
_PLACEHOLDER_KEY_IDS = {
    "lians-receipt-key",
    "change-me",
    "configure-me",
    "default",
    "development-key",
}
_MAX_VAULT_RESPONSE_BYTES = 1_048_576


class ReceiptSignerConfigurationError(ValueError):
    """The selected receipt signer cannot be used safely."""


class ReceiptSigningUnavailable(RuntimeError):
    """The configured signer could not produce a locally verified signature."""


@dataclass(frozen=True)
class ReceiptSignature:
    """Provider-neutral signature material for Decision Receipt v0.1."""

    key_id: str
    public_key: str
    value: str
    algorithm: str = "ed25519"


@dataclass(frozen=True)
class ReceiptSignerConfiguration:
    """Normalized signer configuration; secret fields are hidden from repr."""

    provider: str
    key_id: str
    local_private_key: bytes | None = field(default=None, repr=False)
    vault_address: str = ""
    vault_token: str = field(default="", repr=False)
    vault_token_file: str = field(default="", repr=False)
    vault_namespace: str = ""
    vault_mount_point: str = ""
    vault_key_name: str = ""
    vault_key_version: int = 0
    vault_public_key: bytes | None = field(default=None, repr=False)
    vault_timeout_seconds: float = 5.0

    @property
    def enabled(self) -> bool:
        if self.provider == "local":
            return self.local_private_key is not None
        return True


class ReceiptSigner(Protocol):
    """Async signing boundary shared by local and remote providers."""

    provider: str
    key_id: str
    public_key: str
    key_version: int | None

    async def sign_digest(self, digest: bytes) -> ReceiptSignature: ...

    async def aclose(self) -> None: ...


def _is_production(settings: Any) -> bool:
    return str(settings.deployment_environment).strip().lower() in {"prod", "production"}


def _secret_value(value: Any) -> str:
    getter = getattr(value, "get_secret_value", None)
    return str(getter() if callable(getter) else value).strip()


def _validate_vault_token(value: str) -> str:
    token = value.strip()
    if (
        not token
        or len(token) > 8_192
        or any(ord(char) < 33 or ord(char) > 126 for char in token)
    ):
        raise ReceiptSignerConfigurationError("Vault receipt token has an invalid shape")
    return token


def _validate_vault_token_file_path(value: str) -> str:
    candidate = value
    if (
        not candidate
        or len(candidate) > 4_096
        or any(ord(char) < 32 for char in candidate)
        or candidate != candidate.strip()
    ):
        raise ReceiptSignerConfigurationError(
            "RECEIPT_VAULT_TOKEN_FILE must be a bounded absolute path"
        )
    path = Path(candidate)
    if not path.is_absolute() or ".." in path.parts:
        raise ReceiptSignerConfigurationError(
            "RECEIPT_VAULT_TOKEN_FILE must be a bounded absolute path"
        )
    return str(path)


def _read_vault_token_file(path: str) -> str:
    """Read one rotated token without following its content into an error."""
    try:
        with open(path, "rb", buffering=0) as token_file:
            file_stat = os.fstat(token_file.fileno())
            if not stat.S_ISREG(file_stat.st_mode):
                raise ReceiptSigningUnavailable(
                    "Vault receipt token file is not a regular file"
                )
            if stat.S_IMODE(file_stat.st_mode) & 0o222:
                raise ReceiptSigningUnavailable(
                    "Vault receipt token file must be mounted read-only"
                )
            encoded = token_file.read(8_193)
    except ReceiptSigningUnavailable:
        raise
    except OSError:
        raise ReceiptSigningUnavailable("Vault receipt token file is unavailable") from None
    if len(encoded) > 8_192:
        raise ReceiptSigningUnavailable("Vault receipt token file exceeded the safety limit")
    try:
        decoded = encoded.decode("ascii")
        return _validate_vault_token(decoded)
    except (UnicodeDecodeError, ReceiptSignerConfigurationError):
        raise ReceiptSigningUnavailable("Vault receipt token file is invalid") from None


def _validate_vault_namespace(value: str) -> str:
    namespace = value
    if not namespace:
        return ""
    if (
        len(namespace) > 255
        or namespace.startswith("/")
        or namespace.endswith("/")
        or any(ord(char) < 33 or ord(char) > 126 for char in namespace)
        or any(not _SAFE_VAULT_SEGMENT.fullmatch(segment) for segment in namespace.split("/"))
    ):
        raise ReceiptSignerConfigurationError(
            "RECEIPT_VAULT_NAMESPACE must be a printable path of safe Vault segments"
        )
    return namespace


def _decode_raw_ed25519_key(value: str, *, field_name: str) -> bytes:
    candidate = value.strip()
    if len(candidate) == 64:
        try:
            raw = bytes.fromhex(candidate)
        except ValueError:
            raw = b""
        if len(raw) == 32:
            return raw
    try:
        raw = base64.b64decode(candidate, validate=True)
    except (binascii.Error, ValueError) as exc:
        raise ReceiptSignerConfigurationError(
            f"{field_name} must be a raw 32-byte Ed25519 key encoded as base64 or hexadecimal"
        ) from exc
    if len(raw) != 32:
        raise ReceiptSignerConfigurationError(
            f"{field_name} must decode to exactly 32 bytes"
        )
    return raw


def _decode_vault_public_key(value: Any) -> bytes:
    if not isinstance(value, str) or not value.strip():
        raise ReceiptSigningUnavailable(
            "Vault Transit metadata omitted the pinned key version public key"
        )
    candidate = value.strip()
    if candidate.startswith("-----BEGIN PUBLIC KEY-----"):
        try:
            loaded = serialization.load_pem_public_key(candidate.encode("ascii"))
        except (TypeError, ValueError) as exc:
            raise ReceiptSigningUnavailable(
                "Vault Transit returned an invalid Ed25519 public key"
            ) from exc
        if not isinstance(loaded, Ed25519PublicKey):
            raise ReceiptSigningUnavailable(
                "Vault Transit key metadata is not an Ed25519 public key"
            )
        return loaded.public_bytes(
            encoding=serialization.Encoding.Raw,
            format=serialization.PublicFormat.Raw,
        )
    try:
        raw = base64.b64decode(candidate, validate=True)
    except (binascii.Error, ValueError) as exc:
        raise ReceiptSigningUnavailable(
            "Vault Transit returned an invalid Ed25519 public key"
        ) from exc
    if len(raw) != 32:
        raise ReceiptSigningUnavailable(
            "Vault Transit Ed25519 public key must decode to exactly 32 bytes"
        )
    return raw


def _normalize_vault_address(value: str, *, production: bool) -> str:
    candidate = value
    if (
        not candidate
        or candidate != candidate.strip()
        or len(candidate) > 2_048
        or any(ord(char) < 32 for char in candidate)
    ):
        raise ReceiptSignerConfigurationError(
            "RECEIPT_VAULT_ADDR must be an absolute HTTP(S) Vault origin"
        )
    try:
        parsed = urlsplit(candidate)
        _ = parsed.port
    except ValueError as exc:
        raise ReceiptSignerConfigurationError(
            "RECEIPT_VAULT_ADDR must contain a valid host and optional port"
        ) from exc
    scheme = parsed.scheme.lower()
    if (
        scheme not in {"http", "https"}
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
        or parsed.query
        or parsed.fragment
        or parsed.path not in {"", "/"}
    ):
        raise ReceiptSignerConfigurationError(
            "RECEIPT_VAULT_ADDR must be an HTTP(S) origin without credentials, path, query, or fragment"
        )
    if production and scheme != "https":
        raise ReceiptSignerConfigurationError(
            "RECEIPT_VAULT_ADDR must use HTTPS in production"
        )
    return urlunsplit((scheme, parsed.netloc, "", "", "")).rstrip("/")


def _validate_safe_identifier(value: str, *, field_name: str) -> str:
    candidate = value.strip()
    if not _SAFE_IDENTIFIER.fullmatch(candidate):
        raise ReceiptSignerConfigurationError(
            f"{field_name} must be 1-64 ASCII letters, numbers, '.', '_', or '-', "
            "starting with a letter or number"
        )
    return candidate


def _validate_vault_segment(value: str, *, field_name: str) -> str:
    candidate = value
    if not _SAFE_VAULT_SEGMENT.fullmatch(candidate):
        raise ReceiptSignerConfigurationError(
            f"{field_name} must be one safe 1-128 character Vault path segment"
        )
    return candidate


def validate_receipt_signer_configuration(settings: Any) -> ReceiptSignerConfiguration:
    """Validate the complete signer posture without performing network I/O."""
    production = _is_production(settings)
    provider = str(settings.receipt_signing_provider).strip().lower()
    if provider not in {"local", "vault-transit"}:
        raise ReceiptSignerConfigurationError(
            "RECEIPT_SIGNING_PROVIDER must be 'local' or 'vault-transit'"
        )
    key_id = _validate_safe_identifier(
        str(settings.receipt_signing_key_id), field_name="RECEIPT_SIGNING_KEY_ID"
    )
    if production and key_id.lower() in _PLACEHOLDER_KEY_IDS:
        raise ReceiptSignerConfigurationError(
            "RECEIPT_SIGNING_KEY_ID must identify this deployment's published trust key, "
            "not a placeholder"
        )

    raw_private = str(settings.receipt_signing_private_key).strip()
    raw_vault_token = _secret_value(settings.receipt_vault_token)
    raw_vault_token_file = str(settings.receipt_vault_token_file)
    raw_vault_namespace = str(settings.receipt_vault_namespace)
    vault_material_configured = any(
        (
            str(settings.receipt_vault_addr),
            raw_vault_token,
            raw_vault_token_file,
            raw_vault_namespace,
            str(settings.receipt_vault_key_name),
            settings.receipt_vault_key_version,
            str(settings.receipt_vault_public_key).strip(),
        )
    )

    if provider == "local":
        if vault_material_configured:
            raise ReceiptSignerConfigurationError(
                "Vault receipt-signing material is set while RECEIPT_SIGNING_PROVIDER=local"
            )
        private_raw = (
            _decode_raw_ed25519_key(
                raw_private, field_name="RECEIPT_SIGNING_PRIVATE_KEY"
            )
            if raw_private
            else None
        )
        if production and private_raw is None:
            raise ReceiptSignerConfigurationError(
                "RECEIPT_SIGNING_PRIVATE_KEY is required for the local provider in production"
            )
        if private_raw is not None:
            try:
                Ed25519PrivateKey.from_private_bytes(private_raw)
            except ValueError as exc:
                raise ReceiptSignerConfigurationError(
                    "RECEIPT_SIGNING_PRIVATE_KEY is not a valid raw Ed25519 private key"
                ) from exc
        return ReceiptSignerConfiguration(
            provider=provider,
            key_id=key_id,
            local_private_key=private_raw,
        )

    if raw_private:
        raise ReceiptSignerConfigurationError(
            "RECEIPT_SIGNING_PRIVATE_KEY must be empty when using Vault Transit"
        )
    if bool(raw_vault_token) == bool(raw_vault_token_file):
        raise ReceiptSignerConfigurationError(
            "Configure exactly one of RECEIPT_VAULT_TOKEN or RECEIPT_VAULT_TOKEN_FILE"
        )
    validated_token = _validate_vault_token(raw_vault_token) if raw_vault_token else ""
    validated_token_file = (
        _validate_vault_token_file_path(raw_vault_token_file)
        if raw_vault_token_file
        else ""
    )

    version = settings.receipt_vault_key_version
    if type(version) is not int or version <= 0 or version > 2_147_483_647:
        raise ReceiptSignerConfigurationError(
            "RECEIPT_VAULT_KEY_VERSION must be a pinned positive integer"
        )
    try:
        timeout = float(settings.receipt_vault_timeout_seconds)
    except (TypeError, ValueError) as exc:
        raise ReceiptSignerConfigurationError(
            "RECEIPT_VAULT_TIMEOUT_SECONDS must be between 0.25 and 10"
        ) from exc
    if not math.isfinite(timeout) or not 0.25 <= timeout <= 10.0:
        raise ReceiptSignerConfigurationError(
            "RECEIPT_VAULT_TIMEOUT_SECONDS must be between 0.25 and 10"
        )
    public_raw = _decode_raw_ed25519_key(
        str(settings.receipt_vault_public_key),
        field_name="RECEIPT_VAULT_PUBLIC_KEY",
    )
    try:
        Ed25519PublicKey.from_public_bytes(public_raw)
    except ValueError as exc:
        raise ReceiptSignerConfigurationError(
            "RECEIPT_VAULT_PUBLIC_KEY is not a valid raw Ed25519 public key"
        ) from exc

    return ReceiptSignerConfiguration(
        provider=provider,
        key_id=key_id,
        vault_address=_normalize_vault_address(
            str(settings.receipt_vault_addr), production=production
        ),
        vault_token=validated_token,
        vault_token_file=validated_token_file,
        vault_namespace=_validate_vault_namespace(raw_vault_namespace),
        vault_mount_point=_validate_vault_segment(
            str(settings.receipt_vault_mount_point),
            field_name="RECEIPT_VAULT_MOUNT_POINT",
        ),
        vault_key_name=_validate_vault_segment(
            str(settings.receipt_vault_key_name), field_name="RECEIPT_VAULT_KEY_NAME"
        ),
        vault_key_version=version,
        vault_public_key=public_raw,
        vault_timeout_seconds=timeout,
    )


def receipt_signing_enabled(settings: Any) -> bool:
    """Return whether valid configuration will emit signed receipts."""
    return validate_receipt_signer_configuration(settings).enabled


def receipt_signer_identity(signer: ReceiptSigner | None) -> dict[str, Any]:
    """Return readiness-safe identity without endpoints, paths, or credentials."""
    if signer is None:
        return {
            "configured": False,
            "provider": None,
            "algorithm": None,
            "key_id": None,
            "key_version": None,
            "public_key_sha256": None,
        }
    public_raw = base64.b64decode(signer.public_key, validate=True)
    return {
        "configured": True,
        "provider": signer.provider,
        "algorithm": "ed25519",
        "key_id": signer.key_id,
        "key_version": signer.key_version,
        "public_key_sha256": hashlib.sha256(public_raw).hexdigest(),
    }


def _validate_digest(digest: bytes) -> bytes:
    if not isinstance(digest, bytes) or len(digest) != 32:
        raise ValueError("Decision Receipt signer requires an exact 32-byte SHA-256 digest")
    return digest


class LocalEd25519ReceiptSigner:
    """Compatibility signer using a raw Ed25519 private key in this process."""

    provider = "local"

    def __init__(self, config: ReceiptSignerConfiguration) -> None:
        if config.local_private_key is None:
            raise ReceiptSignerConfigurationError("Local receipt signer has no private key")
        self.key_id = config.key_id
        self.key_version = None
        self._private_key = Ed25519PrivateKey.from_private_bytes(config.local_private_key)
        self._public_key = self._private_key.public_key()
        public_raw = self._public_key.public_bytes(
            encoding=serialization.Encoding.Raw,
            format=serialization.PublicFormat.Raw,
        )
        self.public_key = base64.b64encode(public_raw).decode("ascii")

    async def sign_digest(self, digest: bytes) -> ReceiptSignature:
        message = _validate_digest(digest)
        raw_signature = self._private_key.sign(message)
        self._public_key.verify(raw_signature, message)
        return ReceiptSignature(
            key_id=self.key_id,
            public_key=self.public_key,
            value=base64.b64encode(raw_signature).decode("ascii"),
        )

    async def aclose(self) -> None:
        return None


class VaultTransitEd25519ReceiptSigner:
    """Vault Transit signer pinned to one Ed25519 key version and public key."""

    provider = "vault-transit"

    def __init__(
        self,
        config: ReceiptSignerConfiguration,
        *,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        if config.vault_public_key is None:
            raise ReceiptSignerConfigurationError("Vault receipt signer has no public-key pin")
        self.key_id = config.key_id
        self._address = config.vault_address
        self._token = config.vault_token
        self._token_file = config.vault_token_file
        self._namespace = config.vault_namespace
        self._mount = config.vault_mount_point
        self._key_name = config.vault_key_name
        self._key_version = config.vault_key_version
        self.key_version = self._key_version
        self._public_raw = config.vault_public_key
        self._public = Ed25519PublicKey.from_public_bytes(self._public_raw)
        self.public_key = base64.b64encode(self._public_raw).decode("ascii")
        timeout = config.vault_timeout_seconds
        self._timeout = httpx.Timeout(
            timeout,
            connect=min(timeout, 3.0),
            read=timeout,
            write=timeout,
            pool=min(timeout, 2.0),
        )
        self._owns_client = client is None
        self._client = client or httpx.AsyncClient(
            timeout=self._timeout,
            follow_redirects=False,
            limits=httpx.Limits(max_connections=20, max_keepalive_connections=10),
            # A process-wide HTTPS_PROXY must never receive X-Vault-Token.
            # Operators route Vault explicitly through RECEIPT_VAULT_ADDR.
            trust_env=False,
        )

    @classmethod
    async def load(
        cls,
        config: ReceiptSignerConfiguration,
        *,
        client: httpx.AsyncClient | None = None,
    ) -> VaultTransitEd25519ReceiptSigner:
        signer = cls(config, client=client)
        try:
            await signer._validate_key_metadata()
        except Exception:
            await signer.aclose()
            raise
        return signer

    def _endpoint(self, operation: str) -> str:
        mount = quote(self._mount, safe="")
        key_name = quote(self._key_name, safe="")
        return f"{self._address}/v1/{mount}/{operation}/{key_name}"

    async def _request_data(
        self,
        method: str,
        endpoint: str,
        *,
        body: Mapping[str, Any] | None = None,
        operation: str,
    ) -> Mapping[str, Any]:
        token = _read_vault_token_file(self._token_file) if self._token_file else self._token
        headers = {
            "Accept": "application/json",
            "Accept-Encoding": "identity",
            "X-Vault-Request": "true",
            "X-Vault-Token": token,
        }
        if self._namespace:
            headers["X-Vault-Namespace"] = self._namespace
        chunks: list[bytes] = []
        response_size = 0
        try:
            async with self._client.stream(
                method,
                endpoint,
                json=dict(body) if body is not None else None,
                headers=headers,
                timeout=self._timeout,
                follow_redirects=False,
            ) as response:
                if response.status_code != 200:
                    raise ReceiptSigningUnavailable(
                        f"Vault Transit {operation} failed with HTTP {response.status_code}"
                    )
                declared_size = response.headers.get("Content-Length")
                if declared_size is not None:
                    try:
                        parsed_size = int(declared_size)
                    except ValueError:
                        raise ReceiptSigningUnavailable(
                            f"Vault Transit {operation} returned invalid body framing"
                        ) from None
                    if parsed_size < 0 or parsed_size > _MAX_VAULT_RESPONSE_BYTES:
                        raise ReceiptSigningUnavailable(
                            f"Vault Transit {operation} response exceeded the safety limit"
                        )
                async for chunk in response.aiter_bytes(chunk_size=65_536):
                    response_size += len(chunk)
                    if response_size > _MAX_VAULT_RESPONSE_BYTES:
                        raise ReceiptSigningUnavailable(
                            f"Vault Transit {operation} response exceeded the safety limit"
                        )
                    chunks.append(chunk)
        except (httpx.TimeoutException, httpx.RequestError):
            raise ReceiptSigningUnavailable(
                f"Vault Transit {operation} request was unavailable"
            ) from None
        try:
            payload = json.loads(b"".join(chunks))
        except (UnicodeDecodeError, json.JSONDecodeError):
            raise ReceiptSigningUnavailable(
                f"Vault Transit {operation} returned invalid JSON"
            ) from None
        if not isinstance(payload, Mapping) or not isinstance(payload.get("data"), Mapping):
            raise ReceiptSigningUnavailable(
                f"Vault Transit {operation} response omitted its data object"
            )
        return payload["data"]

    async def _validate_key_metadata(self) -> None:
        data = await self._request_data(
            "GET",
            self._endpoint("keys"),
            operation="key metadata",
        )
        if data.get("type") != "ed25519":
            raise ReceiptSigningUnavailable("Vault Transit receipt key must be Ed25519")
        if data.get("supports_signing") is not True:
            raise ReceiptSigningUnavailable("Vault Transit receipt key does not support signing")
        if data.get("derived") is not False:
            raise ReceiptSigningUnavailable(
                "Vault Transit derived Ed25519 keys are not supported by the fixed receipt trust pin"
            )
        if data.get("name") is not None and data.get("name") != self._key_name:
            raise ReceiptSigningUnavailable("Vault Transit returned metadata for a different key")

        latest = data.get("latest_version")
        if type(latest) is not int or latest <= 0 or latest < self._key_version:
            raise ReceiptSigningUnavailable(
                "Vault Transit metadata does not contain the pinned key version"
            )
        minimum = data.get("min_encryption_version", 0)
        if type(minimum) is not int or minimum < 0 or (
            minimum > 0 and self._key_version < minimum
        ):
            raise ReceiptSigningUnavailable(
                "Vault Transit no longer permits signing with the pinned key version"
            )
        keys = data.get("keys")
        if not isinstance(keys, Mapping):
            raise ReceiptSigningUnavailable("Vault Transit metadata omitted key versions")
        version_entry = keys.get(str(self._key_version))
        if not isinstance(version_entry, Mapping) or version_entry.get("destroyed") is True:
            raise ReceiptSigningUnavailable(
                "Vault Transit metadata does not contain an active pinned key version"
            )
        metadata_public = _decode_vault_public_key(version_entry.get("public_key"))
        if not secrets.compare_digest(metadata_public, self._public_raw):
            raise ReceiptSigningUnavailable(
                "Vault Transit pinned key version does not match RECEIPT_VAULT_PUBLIC_KEY"
            )

    async def sign_digest(self, digest: bytes) -> ReceiptSignature:
        message = _validate_digest(digest)
        data = await self._request_data(
            "POST",
            self._endpoint("sign"),
            body={
                # The receipt SHA-256 bytes are deliberately the Ed25519
                # message. False avoids selecting Vault Enterprise Ed25519ph.
                "input": base64.b64encode(message).decode("ascii"),
                "key_version": self._key_version,
                "prehashed": False,
            },
            operation="signing",
        )
        encoded = data.get("signature")
        if not isinstance(encoded, str):
            raise ReceiptSigningUnavailable("Vault Transit signing response omitted its signature")
        match = _VAULT_SIGNATURE.fullmatch(encoded)
        if match is None or int(match.group(1)) != self._key_version:
            raise ReceiptSigningUnavailable(
                "Vault Transit signature did not use the exact pinned key version"
            )
        encoded_raw = match.group(2)
        try:
            raw_signature = base64.b64decode(encoded_raw, validate=True)
        except (binascii.Error, ValueError):
            raise ReceiptSigningUnavailable(
                "Vault Transit returned an invalid Ed25519 signature encoding"
            ) from None
        if (
            len(raw_signature) != 64
            or base64.b64encode(raw_signature).decode("ascii") != encoded_raw
        ):
            raise ReceiptSigningUnavailable(
                "Vault Transit returned an invalid Ed25519 signature encoding"
            )
        try:
            self._public.verify(raw_signature, message)
        except InvalidSignature:
            raise ReceiptSigningUnavailable(
                "Vault Transit signature failed local Ed25519 verification"
            ) from None
        return ReceiptSignature(
            key_id=self.key_id,
            public_key=self.public_key,
            value=encoded_raw,
        )

    async def aclose(self) -> None:
        if self._owns_client:
            await self._client.aclose()


async def create_receipt_signer(
    settings: Any,
    *,
    client: httpx.AsyncClient | None = None,
) -> ReceiptSigner | None:
    """Construct and, for Vault, remotely validate one signer instance."""
    config = validate_receipt_signer_configuration(settings)
    if not config.enabled:
        return None
    if config.provider == "local":
        return LocalEd25519ReceiptSigner(config)
    return await VaultTransitEd25519ReceiptSigner.load(config, client=client)


_signer_cache: ReceiptSigner | None = None
_signer_cache_config: ReceiptSignerConfiguration | None = None
_signer_cache_initialized = False
_signer_cache_lock = asyncio.Lock()


async def load_receipt_signer(settings: Any | None = None) -> ReceiptSigner | None:
    """Load the process singleton, validating Vault metadata before publication."""
    global _signer_cache, _signer_cache_config, _signer_cache_initialized

    if settings is None:
        from .config import get_settings

        settings = get_settings()
    config = validate_receipt_signer_configuration(settings)
    async with _signer_cache_lock:
        if _signer_cache_initialized and config == _signer_cache_config:
            return _signer_cache
        replacement = await create_receipt_signer(settings)
        previous = _signer_cache
        _signer_cache = replacement
        _signer_cache_config = config
        _signer_cache_initialized = True
        if previous is not None:
            await previous.aclose()
        return replacement


async def get_receipt_signer() -> ReceiptSigner | None:
    """Return the validated singleton, loading it lazily when startup has not."""
    return await load_receipt_signer()


async def close_receipt_signer() -> None:
    """Close and clear the process signer during application shutdown."""
    global _signer_cache, _signer_cache_config, _signer_cache_initialized

    async with _signer_cache_lock:
        previous = _signer_cache
        _signer_cache = None
        _signer_cache_config = None
        _signer_cache_initialized = False
        if previous is not None:
            await previous.aclose()


async def reset_receipt_signer_cache_for_tests() -> None:
    """Clear and close the signer singleton between isolated test settings."""
    await close_receipt_signer()


async def build_decision_receipt_with_signer(
    *,
    signer: ReceiptSigner | None,
    decision: Mapping[str, Any] | Any,
    knowledge_snapshot: Sequence[Mapping[str, Any] | Any],
    cited_evidence: Sequence[Mapping[str, Any] | Any],
    audit_chain: Mapping[str, Any],
    include_source_content: bool = False,
) -> dict[str, Any]:
    """Build a receipt and attach a signature only after provider verification."""
    from .decision_receipt import (
        attach_decision_receipt_signature,
        build_decision_receipt,
        build_decision_receipt_for_signing,
    )

    if signer is None:
        return build_decision_receipt(
            decision=decision,
            knowledge_snapshot=knowledge_snapshot,
            cited_evidence=cited_evidence,
            audit_chain=audit_chain,
            include_source_content=include_source_content,
        )
    receipt = build_decision_receipt_for_signing(
        decision=decision,
        knowledge_snapshot=knowledge_snapshot,
        cited_evidence=cited_evidence,
        audit_chain=audit_chain,
        signing_key_id=signer.key_id,
        include_source_content=include_source_content,
    )
    digest = bytes.fromhex(receipt["integrity"]["receipt_hash"])
    signature = await signer.sign_digest(digest)
    if (
        signature.algorithm != "ed25519"
        or signature.key_id != signer.key_id
        or signature.public_key != signer.public_key
    ):
        raise ReceiptSigningUnavailable("Receipt signer returned inconsistent trust metadata")
    return attach_decision_receipt_signature(
        receipt,
        signing_key_id=signature.key_id,
        signing_public_key=signature.public_key,
        signature_value=signature.value,
    )
