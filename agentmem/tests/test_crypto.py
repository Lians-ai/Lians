"""crypto.py: encrypt/decrypt round-trip + crypto-shred makes content unreadable."""
import pytest
from cryptography.exceptions import InvalidTag

from src.lian.crypto import (
    generate_subject_key,
    wrap_subject_key,
    unwrap_subject_key,
    encrypt_content,
    decrypt_content,
)


def test_roundtrip():
    key = generate_subject_key()
    plaintext = "NVDA Q3 guidance raised to $36B"
    ct = encrypt_content(plaintext, key)
    assert decrypt_content(ct, key) == plaintext


def test_different_ciphertexts_same_plaintext():
    """Nonce randomness: same plaintext produces different ciphertexts."""
    key = generate_subject_key()
    ct1 = encrypt_content("same text", key)
    ct2 = encrypt_content("same text", key)
    assert ct1 != ct2


def test_wrap_unwrap():
    key = generate_subject_key()
    wrapped = wrap_subject_key(key)
    assert unwrap_subject_key(wrapped) == key


def test_crypto_shred():
    """Destroying the key makes content unreadable (wrong key raises InvalidTag)."""
    key = generate_subject_key()
    ct = encrypt_content("sensitive PII content", key)

    # Simulate key destruction by zeroing it
    destroyed_key = b"\x00" * len(key)
    with pytest.raises(Exception):  # InvalidTag or similar
        decrypt_content(ct, destroyed_key)


def test_tampered_ciphertext():
    key = generate_subject_key()
    ct = encrypt_content("real content", key)
    tampered = ct[:-1] + bytes([ct[-1] ^ 0xFF])
    with pytest.raises(Exception):
        decrypt_content(tampered, key)
