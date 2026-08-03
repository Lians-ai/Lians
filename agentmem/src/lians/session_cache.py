"""Generation-fenced, process-local cache for an agent's live working set.

The cache is only usable when the caller supplies the generation read from the
shared Redis coherence key while holding the agent's PostgreSQL shared advisory
lock.  A local entry from another generation is a miss, which prevents one API
replica from serving facts superseded by a write on another replica.

TTL and capacity are read from ``Settings`` at operation time so configuration
and test overrides are honored.  Scoring packs inherit the generation and
lifetime of their working-set entry.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any


@dataclass
class _WorkingSetEntry:
    fetched_at: float
    generation: str
    facts: list[Any]


@dataclass
class _ScoringPackEntry:
    generation: str
    pack: object


_cache: dict[tuple[str, str], _WorkingSetEntry] = {}
_packs: dict[tuple[str, str], _ScoringPackEntry] = {}


def _limits() -> tuple[float, int]:
    from .config import get_settings

    settings = get_settings()
    return float(settings.session_cache_ttl_seconds), settings.session_cache_max_entries


def _discard(key: tuple[str, str]) -> None:
    _cache.pop(key, None)
    _packs.pop(key, None)


def _fresh_entry(key: tuple[str, str], generation: str) -> _WorkingSetEntry | None:
    entry = _cache.get(key)
    if entry is None:
        return None
    ttl, _ = _limits()
    if entry.generation != generation or time.monotonic() - entry.fetched_at >= ttl:
        _discard(key)
        return None
    return entry


def get_working_set(
    namespace: str,
    agent_id: str,
    generation: str | None = None,
) -> list[Any] | None:
    """Return a fresh working set for ``generation``, otherwise a cache miss.

    Omitting the shared generation disables the cache.  This makes legacy or
    non-PostgreSQL callers safe by default instead of trusting process-local
    invalidation in a potentially multi-replica deployment.
    """
    if generation is None:
        return None
    entry = _fresh_entry((namespace, agent_id), generation)
    return entry.facts if entry is not None else None


def set_working_set(
    namespace: str,
    agent_id: str,
    facts: list[Any],
    generation: str | None = None,
) -> None:
    """Cache a working set only when it has a shared coherence generation."""
    if generation is None:
        return

    key = (namespace, agent_id)
    _, max_entries = _limits()
    while key not in _cache and len(_cache) >= max_entries:
        oldest = min(_cache, key=lambda candidate: _cache[candidate].fetched_at)
        _discard(oldest)

    _cache[key] = _WorkingSetEntry(
        fetched_at=time.monotonic(),
        generation=generation,
        facts=list(facts),
    )
    # A replacement working set invalidates even a same-generation scoring
    # pack; the list may have been refreshed for reasons other than a write.
    _packs.pop(key, None)


def invalidate_working_set(namespace: str, agent_id: str) -> None:
    """Drop this process's working set and derived scoring pack."""
    _discard((namespace, agent_id))


def get_scoring_pack(namespace: str, agent_id: str) -> object | None:
    key = (namespace, agent_id)
    working = _cache.get(key)
    if working is None or _fresh_entry(key, working.generation) is None:
        return None
    entry = _packs.get(key)
    if entry is None or entry.generation != working.generation:
        _packs.pop(key, None)
        return None
    return entry.pack


def set_scoring_pack(namespace: str, agent_id: str, pack: object) -> None:
    key = (namespace, agent_id)
    working = _cache.get(key)
    if working is None or _fresh_entry(key, working.generation) is None:
        return

    _, max_entries = _limits()
    while key not in _packs and len(_packs) >= max_entries:
        _packs.pop(next(iter(_packs)), None)
    _packs[key] = _ScoringPackEntry(generation=working.generation, pack=pack)


def clear_all() -> None:
    """Drop every cached working set and scoring pack."""
    _cache.clear()
    _packs.clear()


def working_set_size() -> int:
    return len(_cache)
