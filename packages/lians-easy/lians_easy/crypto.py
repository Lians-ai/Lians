"""Local encryption and receipt signing for Lians Bridge.

The database never contains the master key. Windows protects it with DPAPI;
other platforms keep it in a separate owner-readable file until their native
keychain adapters land. AES-GCM provides authenticated encryption for memory
content, and a derived Ed25519 key signs portable context receipts.
"""

from __future__ import annotations

import base64
import ctypes
import json
import os
import sys
import tempfile
from ctypes import wintypes
from pathlib import Path
from typing import Any

from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.hkdf import HKDF


class _DataBlob(ctypes.Structure):
    _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_byte))]


def _windows_protect(data: bytes) -> bytes:
    buffer = (ctypes.c_byte * len(data)).from_buffer_copy(data)
    source = _DataBlob(len(data), ctypes.cast(buffer, ctypes.POINTER(ctypes.c_byte)))
    target = _DataBlob()
    crypt32 = ctypes.windll.crypt32  # type: ignore[attr-defined]
    kernel32 = ctypes.windll.kernel32  # type: ignore[attr-defined]
    if not crypt32.CryptProtectData(
        ctypes.byref(source),
        "Lians Bridge",
        None,
        None,
        None,
        0x01,
        ctypes.byref(target),
    ):
        raise OSError("Windows could not protect the Lians encryption key")
    try:
        return ctypes.string_at(target.pbData, target.cbData)
    finally:
        kernel32.LocalFree(target.pbData)


def _windows_unprotect(data: bytes) -> bytes:
    buffer = (ctypes.c_byte * len(data)).from_buffer_copy(data)
    source = _DataBlob(len(data), ctypes.cast(buffer, ctypes.POINTER(ctypes.c_byte)))
    target = _DataBlob()
    crypt32 = ctypes.windll.crypt32  # type: ignore[attr-defined]
    kernel32 = ctypes.windll.kernel32  # type: ignore[attr-defined]
    if not crypt32.CryptUnprotectData(
        ctypes.byref(source), None, None, None, None, 0x01, ctypes.byref(target)
    ):
        raise OSError("Windows could not unlock the Lians encryption key")
    try:
        return ctypes.string_at(target.pbData, target.cbData)
    finally:
        kernel32.LocalFree(target.pbData)


class LocalCipher:
    """Encrypt memory content and sign receipts with one locally protected root key."""

    def __init__(self, key_path: str | Path, *, key: bytes | None = None) -> None:
        self.key_path = Path(key_path).expanduser()
        self._key = key or self._load_or_create_key()
        if len(self._key) != 32:
            raise ValueError("Lians root key must contain 32 bytes")

    @property
    def protection(self) -> str:
        return "windows-dpapi" if sys.platform == "win32" else "owner-file"

    @property
    def fingerprint(self) -> str:
        digest = hashes.Hash(hashes.SHA256())
        digest.update(self._key)
        return digest.finalize().hex()[:16]

    def _load_or_create_key(self) -> bytes:
        self.key_path.parent.mkdir(parents=True, exist_ok=True)
        if self.key_path.exists():
            return self._read_key()

        key = os.urandom(32)
        protection = "windows-dpapi" if sys.platform == "win32" else "owner-file"
        wrapped = _windows_protect(key) if sys.platform == "win32" else key
        descriptor, temporary_name = tempfile.mkstemp(
            prefix=f".{self.key_path.name}.",
            suffix=".tmp",
            dir=self.key_path.parent,
            text=True,
        )
        temporary = Path(temporary_name)
        try:
            document = (
                json.dumps(
                    {
                        "version": 1,
                        "protection": protection,
                        "key": base64.b64encode(wrapped).decode("ascii"),
                    },
                    sort_keys=True,
                )
                + "\n"
            )
            with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as handle:
                handle.write(document)
                handle.flush()
                os.fsync(handle.fileno())
            if sys.platform != "win32":
                temporary.chmod(0o600)
            try:
                # A hard link publishes a fully written file without overwriting
                # a key another client created at the same moment.
                os.link(temporary, self.key_path)
            except FileExistsError:
                return self._read_key()
            return key
        finally:
            temporary.unlink(missing_ok=True)

    def _read_key(self) -> bytes:
        try:
            document = json.loads(self.key_path.read_text(encoding="utf-8"))
            if document.get("version") != 1:
                raise ValueError("Unsupported Lians key version")
            protection = document.get("protection")
            if protection not in {"windows-dpapi", "owner-file"}:
                raise ValueError("Unsupported Lians key protection")
            wrapped = base64.b64decode(document["key"], validate=True)
        except (AttributeError, KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
            raise ValueError("Lians encryption key file is invalid") from exc
        if protection == "windows-dpapi":
            if sys.platform != "win32":
                raise OSError("This Lians key is protected for a Windows account")
            return _windows_unprotect(wrapped)
        return wrapped

    def seal(self, value: str, *, associated_data: bytes) -> tuple[bytes, bytes]:
        nonce = os.urandom(12)
        return AESGCM(self._key).encrypt(nonce, value.encode("utf-8"), associated_data), nonce

    def open(self, ciphertext: bytes, nonce: bytes, *, associated_data: bytes) -> str:
        return AESGCM(self._key).decrypt(nonce, ciphertext, associated_data).decode("utf-8")

    def seal_bytes(self, value: bytes, *, associated_data: bytes) -> tuple[bytes, bytes]:
        """Protect local binary state without exposing the device root key."""

        nonce = os.urandom(12)
        return AESGCM(self._key).encrypt(nonce, value, associated_data), nonce

    def open_bytes(self, ciphertext: bytes, nonce: bytes, *, associated_data: bytes) -> bytes:
        """Open binary state previously protected for this OS account."""

        return AESGCM(self._key).decrypt(nonce, ciphertext, associated_data)

    def derive_key(self, *, info: bytes, length: int = 32) -> bytes:
        """Derive a domain-separated device key from the protected local root."""

        if not info or len(info) > 256 or not 16 <= length <= 64:
            raise ValueError("Lians key derivation parameters are invalid")
        return HKDF(
            algorithm=hashes.SHA256(),
            length=length,
            salt=None,
            info=info,
        ).derive(self._key)

    def sign(self, value: bytes) -> dict[str, Any]:
        seed = self.derive_key(info=b"lians-context-receipt-v0.1")
        private_key = Ed25519PrivateKey.from_private_bytes(seed)
        public_key = private_key.public_key().public_bytes(
            encoding=serialization.Encoding.Raw,
            format=serialization.PublicFormat.Raw,
        )
        return {
            "algorithm": "Ed25519",
            "public_key": base64.b64encode(public_key).decode("ascii"),
            "value": base64.b64encode(private_key.sign(value)).decode("ascii"),
        }
