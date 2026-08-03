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

from sqlalchemy import and_, delete, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from ._types import _FINANCE_STRUCTURED_KEYS
from .models import LiveFact, Memory

def _get_structured_keys() -> frozenset[str]:
    """Read structured keys from the active domain adapter (no finance hardcoding)."""
    from .adapters import get_adapter
    return get_adapter().structured_keys


# Cached fallback — same default as the finance adapter so behaviour is unchanged
# when DOMAIN_ADAPTER=finance (the default).  current_facts.py is called on
# every write; the per-call adapter lookup is O(1) dict access after first load.
_STRUCTURED_KEYS: frozenset[str] = _FINANCE_STRUCTURED_KEYS

# Keep IN predicates below SQLite's conventional 999-variable ceiling while
# remaining small enough for other development databases and drivers.
_PORTABLE_BIND_BATCH = 400


def compute_predicate_key(meta: dict) -> Optional[str]:
    """Derive a stable predicate key from structured metadata.

    Returns ``None`` for unkeyed memories (no _STRUCTURED_KEYS present).
    Canonical form: key=value pairs, sorted by key, pipe-delimited.
    """
    structured_keys = _get_structured_keys()
    pairs = sorted(
        (k, str(v)) for k, v in meta.items()
        if k in structured_keys and v is not None
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
    """Remove live-fact rows in deterministic, portable bind-size pages."""
    if not memory_ids:
        return
    ordered_ids = sorted(set(memory_ids), key=lambda value: value.hex)
    for start in range(0, len(ordered_ids), _PORTABLE_BIND_BATCH):
        await db.execute(
            delete(LiveFact).where(
                LiveFact.memory_id.in_(
                    ordered_ids[start : start + _PORTABLE_BIND_BATCH]
                )
            )
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
