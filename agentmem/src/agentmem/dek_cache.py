"""
Per-subject Data Encryption Key (DEK) cache — Change 6 of the performance roadmap.

The subject key stored in ``subject_keys`` is wrapped with the master key.
Unwrapping requires an AES-GCM decrypt every time.  This module caches the
plaintext DEK in-process after the first unwrap, eliminating repeated DB
lookups and KMS round-trips from the hot recall path.

Invalidation:
  - ``evict_dek(subject_id)`` is called on crypto-shred so that a destroyed
    key is never served from cache.
  - The cache survives process restarts only within a single worker instance;
    a new worker starts with an empty cache and warms on first access.

Thread safety: asyncio is cooperative, so dict reads/writes are atomic.
"""
from __future__ import annotations

from typing import Optional

# subject_id -> plaintext 32-byte DEK
_dek_cache: dict[str, bytes] = {}


def get_cached_dek(subject_id: str) -> Optional[bytes]:
    """Return the cached plaintext DEK, or None on cache miss."""
    return _dek_cache.get(subject_id)


def cache_dek(subject_id: str, key: bytes) -> None:
    """Store the plaintext DEK after unwrapping."""
    _dek_cache[subject_id] = key


def evict_dek(subject_id: str) -> None:
    """Remove a destroyed subject's DEK from cache.

    Called immediately after ``destroy_subject_key()`` so subsequent decrypt
    attempts fail with InvalidTag rather than returning garbage.
    """
    _dek_cache.pop(subject_id, None)
