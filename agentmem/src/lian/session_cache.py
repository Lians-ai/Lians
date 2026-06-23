"""
In-process working-set cache for live facts per agent — Change 7 of the
performance roadmap.

After a session is bound (first recall per agent per process), the agent's
entire live working set is prefetched from ``live_facts`` and held here.
Subsequent recalls for the same agent are served from memory — no Postgres
or vector-index round-trip — until an explicit invalidation.

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

_cache: dict[tuple[str, str], tuple[datetime, list]] = {}  # (ns, agent) -> (fetched_at, facts)
_MAX_ENTRIES = 512
_TTL_SECONDS = 300  # 5 min max staleness — write invalidation handles most cases


def get_working_set(namespace: str, agent_id: str) -> Optional[list]:
    """Return cached live facts or None on miss / expiry."""
    entry = _cache.get((namespace, agent_id))
    if entry is None:
        return None
    fetched_at, facts = entry
    if (datetime.now(timezone.utc) - fetched_at).total_seconds() > _TTL_SECONDS:
        _cache.pop((namespace, agent_id), None)
        return None
    return facts


def set_working_set(namespace: str, agent_id: str, facts: list) -> None:
    """Cache the live working set for the agent."""
    if len(_cache) >= _MAX_ENTRIES:
        oldest = min(_cache, key=lambda k: _cache[k][0])
        _cache.pop(oldest, None)
    _cache[(namespace, agent_id)] = (datetime.now(timezone.utc), list(facts))


def invalidate_working_set(namespace: str, agent_id: str) -> None:
    """Drop cached facts — called on any write or erasure for this agent."""
    _cache.pop((namespace, agent_id), None)


def working_set_size() -> int:
    return len(_cache)
