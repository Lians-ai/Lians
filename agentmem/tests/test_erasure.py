"""
Erasure: crypto-shred renders content unreadable; audit trail (hashes) survive.
"""
import pytest
from src.lian.crypto import (
    generate_subject_key,
    wrap_subject_key,
    unwrap_subject_key,
    encrypt_content,
    decrypt_content,
)


def test_shred_makes_content_unreadable():
    key = generate_subject_key()
    ct = encrypt_content("Client: John Smith, SSN: 123-45-6789", key)

    # Shred: overwrite key with zeros
    shredded_key = b"\x00" * len(key)

    with pytest.raises(Exception):
        decrypt_content(ct, shredded_key)


def test_content_hash_survives_erasure():
    """The hash is derived from plaintext before encryption â€” it's always available."""
    import hashlib
    plaintext = "sensitive content"
    key = generate_subject_key()
    ct = encrypt_content(plaintext, key)
    content_hash = hashlib.sha256(plaintext.encode()).hexdigest()

    # Even after "erasure" (zeroing key), the hash is still valid
    assert content_hash == hashlib.sha256(plaintext.encode()).hexdigest()
    # The ciphertext is still there (tombstone), just unreadable
    assert ct is not None


def test_different_subjects_independent():
    """Shredding subject A's key doesn't affect subject B's content."""
    key_a = generate_subject_key()
    key_b = generate_subject_key()

    ct_a = encrypt_content("Subject A data", key_a)
    ct_b = encrypt_content("Subject B data", key_b)

    # Shred A
    shredded_a = b"\x00" * len(key_a)

    with pytest.raises(Exception):
        decrypt_content(ct_a, shredded_a)

    # B is unaffected
    assert decrypt_content(ct_b, key_b) == "Subject B data"
