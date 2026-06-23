"""
Current-facts projection — Change 1 of the performance roadmap.

Maintains ``live_facts`` as a compact, always-current view of memories:
  • One row per keyed (namespace, agent_id, predicate_key) — the latest
    non-superseded fact for each entity+attribute combination.
  • One row per unkeyed memory while it remains live.

Recall queries ``live_facts`` instead of filtering ``memories WHERE
valid_to IS NULL``, shrinking the ANN search space 5–10× on a real
financial corpus and eliminating temporal predicates from the hot path.
"""
from __future__ import annotations

from typing import Optional
from uuid import UUID

from sqlalchemy import delete, select, and_, or_
from sqlalchemy.ext.asyncio import AsyncSession

from .models import LiveFact, Memory

def _get_structured_keys() -> frozenset[str]:
    """Read structured keys from the active domain adapter (no finance hardcoding)."""
    from .adapters import get_adapter
    return get_adapter().structured_keys


# Cached fallback — same default as the finance adapter so behaviour is unchanged
# when DOMAIN_ADAPTER=finance (the default).  current_facts.py is called on
# every write; the per-call adapter lookup is O(1) dict access after first load.
_STRUCTURED_KEYS: frozenset[str] = frozenset({"ticker", "metric", "entity", "instrument", "cusip", "isin", "field"})


def compute_predicate_key(meta: dict) -> Optional[str]:
    """Derive a stable predicate key from structured metadata.

    Returns ``None`` for unkeyed memories (no _STRUCTURED_KEYS present).
    Canonical form: key=value pairs, sorted by key, pipe-delimited.
    """
    pairs = sorted(
        (k, str(v)) for k, v in meta.items()
        if k in _STRUCTURED_KEYS and v is not None
    )
    if not pairs:
        return None
    return "|".join(f"{k}={v}" for k, v in pairs)


async def upsert_live_fact(
    db: AsyncSession,
    mem: Memory,
    predicate_key: Optional[str],
) -> None:
    """Insert a new live fact entry for *mem*.

    Removals of superseded entries are handled exclusively by
    ``remove_live_facts(superseded_ids)`` — which is called with the
    supersession engine's verdict before this function.  Inserting here
    without a pre-delete means same-predicate-key facts that were *not*
    superseded (e.g. same event_time or ADDS relation) correctly coexist
    in live_facts, preserving recall correctness.
    """
    db.add(LiveFact(
        namespace=mem.namespace,
        agent_id=mem.agent_id,
        memory_id=mem.id,
        predicate_key=predicate_key,
        subject_id=mem.subject_id,
        barrier_group=mem.barrier_group,
        event_time=mem.event_time,
        importance=mem.importance,
        metadata_=dict(mem.metadata_ or {}),
        content_encrypted=mem.content_encrypted,
        embedding=mem.embedding,
    ))


async def remove_live_facts(db: AsyncSession, memory_ids: list[UUID]) -> None:
    """Remove live-fact rows for superseded memories."""
    if not memory_ids:
        return
    await db.execute(
        delete(LiveFact).where(LiveFact.memory_id.in_(memory_ids))
    )


async def keyed_lookup(
    db: AsyncSession,
    namespace: str,
    agent_id: str,
    predicate_key: str,
    barrier_group: Optional[str],
) -> Optional[LiveFact]:
    """Exact-match lookup for a keyed fact — no embedding, no ANN.

    Returns the live fact if it exists and passes the barrier check, otherwise
    None (caller falls through to the vector-search branch).  Sub-millisecond
    on the index path (namespace, agent_id, predicate_key).
    """
    conditions = [
        LiveFact.namespace == namespace,
        LiveFact.agent_id == agent_id,
        LiveFact.predicate_key == predicate_key,
    ]
    if barrier_group is not None:
        conditions.append(
            or_(LiveFact.barrier_group == barrier_group, LiveFact.barrier_group.is_(None))
        )
    result = await db.execute(select(LiveFact).where(and_(*conditions)).limit(1))
    return result.scalar_one_or_none()


async def fetch_working_set(
    db: AsyncSession,
    namespace: str,
    agent_id: str,
    barrier_group: Optional[str],
) -> list[LiveFact]:
    """Load all live facts for an agent — used to warm the in-process cache."""
    conditions = [
        LiveFact.namespace == namespace,
        LiveFact.agent_id == agent_id,
    ]
    if barrier_group is not None:
        conditions.append(
            or_(LiveFact.barrier_group == barrier_group, LiveFact.barrier_group.is_(None))
        )
    result = await db.execute(select(LiveFact).where(and_(*conditions)))
    return list(result.scalars().all())
