"""
Per-subject key management and content encryption.

Crypto-shred = destroy subject key → content becomes permanently unreadable.
Audit hashes survive independently.
"""
from __future__ import annotations

import os

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.hkdf import HKDF

from .kms import get_master_keyring

_WRAPPED_KEY_V2_MAGIC = b"lians-dek:v2\x00"
_NONCE_BYTES = 12
_TAG_BYTES = 16


class UnknownMasterKeyVersion(ValueError):
    """A self-identifying envelope names a key outside the bounded keyring."""


def _v2_wrapping_key(master_key: bytes) -> bytes:
    return HKDF(
        algorithm=hashes.SHA256(),
        length=32,
        salt=None,
        info=b"lians/subject-dek-wrap/v2",
    ).derive(master_key)


def _v2_header(key_id: str) -> bytes:
    encoded = key_id.encode("ascii")
    if not 1 <= len(encoded) <= 64:
        raise ValueError("Master key identifier length is outside the v2 envelope contract")
    return _WRAPPED_KEY_V2_MAGIC + bytes((len(encoded),)) + encoded


def wrapped_subject_key_version(wrapped: bytes) -> tuple[int, str | None]:
    """Return ``(2, key_id)`` for v2 or ``(1, None)`` for a legacy wrapper."""
    if not wrapped.startswith(_WRAPPED_KEY_V2_MAGIC):
        return 1, None
    offset = len(_WRAPPED_KEY_V2_MAGIC)
    if len(wrapped) <= offset:
        raise ValueError("Truncated v2 subject-key envelope")
    key_id_length = wrapped[offset]
    header_length = offset + 1 + key_id_length
    if not 1 <= key_id_length <= 64 or len(wrapped) < header_length + _NONCE_BYTES + _TAG_BYTES:
        raise ValueError("Malformed v2 subject-key envelope")
    try:
        key_id = wrapped[offset + 1:header_length].decode("ascii")
    except UnicodeDecodeError as exc:
        raise ValueError("Subject-key envelope contains a non-ASCII key identifier") from exc
    # Apply the same lexical contract as KMS configuration without importing a
    # second expression or accepting an identifier that can never be configured.
    from .kms import validate_master_key_id

    validate_master_key_id(key_id, field="wrapped subject-key key id")
    return 2, key_id


def _wrap_key(content_key: bytes) -> bytes:
    """Encrypt a per-subject key in a self-describing v2 binary envelope."""
    if len(content_key) != 32:
        raise ValueError("Subject content key must be exactly 32 bytes")
    current = get_master_keyring().current
    header = _v2_header(current.key_id)
    nonce = os.urandom(12)
    ciphertext = AESGCM(_v2_wrapping_key(current.material)).encrypt(
        nonce, content_key, header
    )
    return header + nonce + ciphertext


def _unwrap_key(wrapped: bytes) -> bytes:
    """Decrypt v2 wrappers and bounded-current/previous legacy wrappers."""
    version, key_id = wrapped_subject_key_version(wrapped)
    ring = get_master_keyring()
    if version == 2:
        key_version = ring.by_id(key_id or "")
        if key_version is None:
            raise UnknownMasterKeyVersion(
                f"Subject-key envelope references unavailable key id {key_id!r}"
            )
        header = _v2_header(key_version.key_id)
        nonce_offset = len(header)
        nonce = wrapped[nonce_offset:nonce_offset + _NONCE_BYTES]
        ciphertext = wrapped[nonce_offset + _NONCE_BYTES:]
        plaintext = AESGCM(_v2_wrapping_key(key_version.material)).decrypt(
            nonce, ciphertext, header
        )
    else:
        if len(wrapped) < _NONCE_BYTES + _TAG_BYTES:
            raise ValueError("Legacy subject-key wrapper is truncated")
        nonce, ciphertext = wrapped[:_NONCE_BYTES], wrapped[_NONCE_BYTES:]
        last_error: InvalidTag | None = None
        for candidate in ring.candidates:
            try:
                plaintext = AESGCM(candidate.material).decrypt(nonce, ciphertext, None)
                break
            except InvalidTag as exc:
                last_error = exc
        else:
            if last_error is None:
                raise RuntimeError(
                    "Legacy subject-key unwrap had no configured key candidates"
                )
            raise last_error
    if len(plaintext) != 32:
        raise ValueError("Unwrapped subject content key is not 32 bytes")
    return plaintext


def generate_subject_key() -> bytes:
    """Generate a random 32-byte content key."""
    return os.urandom(32)


def wrap_subject_key(content_key: bytes) -> bytes:
    return _wrap_key(content_key)


def unwrap_subject_key(wrapped: bytes) -> bytes:
    return _unwrap_key(wrapped)


def rewrap_subject_key(wrapped: bytes) -> bytes:
    """Return a current-key v2 wrapper after authenticating the existing value."""
    plaintext = _unwrap_key(wrapped)
    rewritten = _wrap_key(plaintext)
    if _unwrap_key(rewritten) != plaintext:
        raise RuntimeError("Subject-key rewrap verification failed")
    return rewritten


def encrypt_content(plaintext: str, content_key: bytes) -> bytes:
    """AES-256-GCM encrypt; nonce prepended to ciphertext."""
    aesgcm = AESGCM(content_key)
    nonce = os.urandom(12)
    ct = aesgcm.encrypt(nonce, plaintext.encode(), None)
    return nonce + ct


def decrypt_content(ciphertext: bytes, content_key: bytes) -> str:
    """Decrypt AES-256-GCM; raises InvalidTag if key is wrong/destroyed."""
    aesgcm = AESGCM(content_key)
    nonce, ct = ciphertext[:12], ciphertext[12:]
    return aesgcm.decrypt(nonce, ct, None).decode()
