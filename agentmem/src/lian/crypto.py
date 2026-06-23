"""
Per-subject key management and content encryption.

Crypto-shred = destroy subject key → content becomes permanently unreadable.
Audit hashes survive independently.
"""
from __future__ import annotations
import base64
import os
from datetime import datetime, timezone
from typing import Optional

from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.hkdf import HKDF
from cryptography.hazmat.primitives import hashes

from .kms import get_master_key


def _wrap_key(content_key: bytes) -> bytes:
    """Encrypt a per-subject key with the master key (AES-GCM)."""
    master = get_master_key()
    aesgcm = AESGCM(master)
    nonce = os.urandom(12)
    ct = aesgcm.encrypt(nonce, content_key, None)
    return nonce + ct


def _unwrap_key(wrapped: bytes) -> bytes:
    """Decrypt a per-subject key using the master key."""
    master = get_master_key()
    aesgcm = AESGCM(master)
    nonce, ct = wrapped[:12], wrapped[12:]
    return aesgcm.decrypt(nonce, ct, None)


def generate_subject_key() -> bytes:
    """Generate a random 32-byte content key."""
    return os.urandom(32)


def wrap_subject_key(content_key: bytes) -> bytes:
    return _wrap_key(content_key)


def unwrap_subject_key(wrapped: bytes) -> bytes:
    return _unwrap_key(wrapped)


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
