"""
In-process working-set cache for live facts per agent - Change 7 of the
performance roadmap.

After a session is bound (first recall per agent per process), the agent's
entire live working set is prefetched from ``live_facts`` and held here.
Subsequent recalls for the same agent are served from memory - no Postgres
or vector-index round-trip - until an explicit invalidation.

Invalidation triggers (call ``invalidate_working_set``):
  - Any ``add_memory`` or ``batch_add_memories`` for the agent.
  - Any supersession that touches the agent's memories.
  - Any crypto-shred of a subject whose data belongs to the agent.

Bounds:
  - At most ``_MAX_ENTRIES`` (agent, namespace) slots.  Overflow evicts the
    oldest entry by fetch timestamp (simple LRU approximation).
  - Entries older than ``_TTL_SECONDS`` are treated as stale on read and
    re-fetched transparently.

Thread safety: asyncio is cooperative, so dict mutations are safe without locks.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional

_cache: dict[tuple[str, str], tuple[datetime, list, str]] = {}
# Derived scoring artifacts (embedding matrix, BM25 stats, decrypted contents)
# built by ranking._scoring_pack - same lifecycle as the working set.
_packs: dict[tuple[str, str], object] = {}
_MAX_ENTRIES = 512
_MAX_PACK_ENTRIES = 32
_MAX_PACK_TEXT_CHARS = 1_048_576
_TTL_SECONDS = 300  # 5 min max staleness - write invalidation handles most cases


def get_working_set(
    namespace: str,
    agent_id: str,
    generation: Optional[str] = None,
) -> Optional[list]:
    """Return cached live facts or None on miss / expiry."""
    key = (namespace, agent_id)
    entry = _cache.get(key)
    if entry is None:
        # A scoring pack is only valid as a derivative of an admitted working
        # set. Never let an orphan pack survive a rejected generation check.
        _packs.pop(key, None)
        return None
    fetched_at, facts, cached_generation = entry
    if (
        generation is None
        or cached_generation != generation
        or (datetime.now(timezone.utc) - fetched_at).total_seconds() > _TTL_SECONDS
    ):
        _cache.pop(key, None)
        _packs.pop(key, None)
        return None
    return facts


def set_working_set(
    namespace: str,
    agent_id: str,
    facts: list,
    generation: Optional[str] = None,
) -> None:
    """Cache the live working set for the agent."""
    if generation is None:
        return
    if len(_cache) >= _MAX_ENTRIES:
        oldest = min(_cache, key=lambda k: _cache[k][0])
        _cache.pop(oldest, None)
        _packs.pop(oldest, None)
    key = (namespace, agent_id)
    # Replacing a working set invalidates every derived plaintext/token pack,
    # even when a weak row fingerprint happens to collide.
    _packs.pop(key, None)
    _cache[key] = (
        datetime.now(timezone.utc), list(facts), generation,
    )


def invalidate_working_set(namespace: str, agent_id: str) -> None:
    """Drop cached facts - called on any write or erasure for this agent."""
    _cache.pop((namespace, agent_id), None)
    _packs.pop((namespace, agent_id), None)


def get_scoring_pack(namespace: str, agent_id: str):
    return _packs.get((namespace, agent_id))


def set_scoring_pack(namespace: str, agent_id: str, pack) -> None:
    key = (namespace, agent_id)
    contents = getattr(pack, "contents", ())
    retained_chars = sum(
        len(content) for content in contents if isinstance(content, str)
    )
    if retained_chars > _MAX_PACK_TEXT_CHARS:
        _packs.pop(key, None)
        return
    if key not in _packs and len(_packs) >= _MAX_PACK_ENTRIES:
        _packs.pop(next(iter(_packs)), None)
    _packs[key] = pack


def clear_all() -> None:
    """Drop every cached working set and scoring pack.

    Needed when a process hosts more than one storage engine (tests,
    notebooks with several LocalLiansClients): the caches are keyed by
    (namespace, agent), so a second client reusing an agent name would
    otherwise be served rows that belong to the first client's database.
    Server deployments have one engine per process and never call this.
    """
    _cache.clear()
    _packs.clear()


def working_set_size() -> int:
    return len(_cache)
