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

from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar

# (namespace, subject_id) -> plaintext 32-byte DEK.
# The namespace is part of the key: subject_id is unique only within a tenant,
# so a bare-subject_id cache would serve one tenant's DEK to another.
_dek_cache: dict[tuple[str, str], bytes] = {}
_cache_disabled: ContextVar[bool] = ContextVar("lians_dek_cache_disabled", default=False)


@contextmanager
def dek_cache_disabled() -> Iterator[None]:
    """Bypass plaintext-DEK caching for the current task.

    Hosted multi-tenant calls use this boundary so plaintext keys live only in
    the active call stack. ContextVar scoping keeps concurrent local callers on
    their normal cache-enabled path.
    """
    token = _cache_disabled.set(True)
    try:
        yield
    finally:
        _cache_disabled.reset(token)


def get_cached_dek(namespace: str, subject_id: str) -> bytes | None:
    """Return the cached plaintext DEK, or None on cache miss."""
    if _cache_disabled.get():
        return None
    return _dek_cache.get((namespace, subject_id))


def cache_dek(namespace: str, subject_id: str, key: bytes) -> None:
    """Store the plaintext DEK after unwrapping."""
    if _cache_disabled.get():
        return
    _dek_cache[(namespace, subject_id)] = key


def evict_dek(namespace: str, subject_id: str) -> None:
    """Remove a destroyed subject's DEK from cache.

    Called immediately after ``destroy_subject_key()`` so subsequent decrypt
    attempts fail with InvalidTag rather than returning garbage.
    """
    _dek_cache.pop((namespace, subject_id), None)
