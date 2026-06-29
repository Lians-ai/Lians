"""
Relationship graph service — the bitemporal knowledge-graph layer.

Stores directed ``src --rel_type--> dst`` edges with the same temporal, audit, and
information-barrier guarantees as memories, and answers the relational compliance
questions atomic facts can't:

    neighbors(entity)      — who/what is connected to this entity (N hops)
    path(src, dst)         — is there a connection, and through what? (COI /
                             related-party / referral reachability)

All reads accept ``as_of`` for point-in-time traversal — "who was connected on the
day of the trade?" — the same temporal guarantee Lians gives for facts, now for
relationships. Traversal runs in-process over the namespace's edges (no graph DB);
for very large graphs this can move to recursive SQL later without an API change.
"""
from __future__ import annotations

import hashlib
from collections import deque
from datetime import datetime, timezone
from typing import Any, Optional
from uuid import UUID

from sqlalchemy import select, and_, or_
from sqlalchemy.ext.asyncio import AsyncSession

from .models import Relationship
from .audit_chain import chain_log
from .entity_normalizer import cached_normalize


# ── Canonicalization ────────────────────────────────────────────────────────────


def canon_entity(value: str, *, normalize: bool = False) -> str:
    """
    Canonical form of an entity label used for dedup and traversal.

    Always collapses surrounding/internal whitespace. When ``normalize`` is set,
    routes through the domain entity normalizer so 'Apple Inc.', 'AAPL', and ISIN
    'US0378331005' resolve to one graph node (finance). Off by default so person /
    party / matter names are preserved verbatim.
    """
    collapsed = " ".join(str(value).split())
    if normalize:
        return cached_normalize("entity", collapsed)
    return collapsed


def _rel_hash(src: str, rel_type: str, dst: str, event_time: datetime) -> str:
    raw = f"{src}|{rel_type}|{dst}|{event_time.isoformat()}"
    return hashlib.sha256(raw.encode()).hexdigest()


def _aware(dt: Optional[datetime]) -> Optional[datetime]:
    if dt is None:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def _is_valid_at(edge: Relationship, as_of: Optional[datetime]) -> bool:
    """True if the edge was live at ``as_of`` (or currently live when as_of is None)."""
    if as_of is None:
        return edge.valid_to is None
    vf = _aware(edge.valid_from)
    vt = _aware(edge.valid_to)
    aso = _aware(as_of)
    return vf <= aso and (vt is None or vt > aso)


# ── Write ───────────────────────────────────────────────────────────────────────


async def relate(
    db: AsyncSession,
    namespace: str,
    *,
    agent_id: str,
    src_entity: str,
    rel_type: str,
    dst_entity: str,
    event_time: datetime,
    exclusive: bool = False,
    subject_id: Optional[str] = None,
    source: Optional[str] = None,
    metadata: Optional[dict[str, Any]] = None,
    normalize: bool = False,
) -> Relationship:
    """
    Assert a relationship edge.

    Idempotent: re-asserting an identical live triplet returns the existing edge.
    When ``exclusive`` is set, asserting ``src --rel_type--> X`` invalidates any
    other live ``src --rel_type--> Y`` (Y != X) — the deterministic equivalent of
    Graphiti's contradiction-driven invalidation, e.g. a person's current employer.
    """
    from .memory_service import _get_barrier_group

    src = canon_entity(src_entity, normalize=normalize)
    dst = canon_entity(dst_entity, normalize=normalize)
    rel = rel_type.strip()

    # Idempotent: identical live edge already exists.
    existing = (await db.execute(
        select(Relationship).where(and_(
            Relationship.namespace == namespace,
            Relationship.agent_id == agent_id,
            Relationship.src_entity == src,
            Relationship.rel_type == rel,
            Relationship.dst_entity == dst,
            Relationship.valid_to.is_(None),
        ))
    )).scalars().first()
    if existing is not None:
        return existing

    barrier_group = await _get_barrier_group(db, namespace, agent_id)
    now = datetime.now(timezone.utc)

    edge = Relationship(
        namespace=namespace,
        agent_id=agent_id,
        src_entity=src,
        rel_type=rel,
        dst_entity=dst,
        event_time=event_time,
        ingestion_time=now,
        valid_from=event_time,
        valid_to=None,
        barrier_group=barrier_group,
        subject_id=subject_id,
        source=source,
        metadata_=metadata or {},
        content_hash=_rel_hash(src, rel, dst, event_time),
    )
    db.add(edge)
    await db.flush()

    if exclusive:
        superseded = (await db.execute(
            select(Relationship).where(and_(
                Relationship.namespace == namespace,
                Relationship.agent_id == agent_id,
                Relationship.src_entity == src,
                Relationship.rel_type == rel,
                Relationship.dst_entity != dst,
                Relationship.valid_to.is_(None),
            ))
        )).scalars().all()
        for old in superseded:
            old.valid_to = event_time
            old.invalidated_by = edge.id
            await _log_invalidation(db, namespace, old, reason="exclusive_supersede")

    await chain_log(
        db, namespace=namespace, agent_id=agent_id,
        op="relate", memory_id=edge.id, content_hash=edge.content_hash,
        payload={"src": src, "rel_type": rel, "dst": dst,
                 "event_time": event_time.isoformat(), "exclusive": exclusive},
    )
    await db.commit()
    await db.refresh(edge)
    return edge


async def unrelate(
    db: AsyncSession,
    namespace: str,
    *,
    agent_id: str,
    src_entity: str,
    rel_type: str,
    dst_entity: str,
    event_time: Optional[datetime] = None,
    normalize: bool = False,
) -> int:
    """
    Invalidate a live edge (set ``valid_to``) — Graphiti's ``invalid_at``.

    The edge is preserved for point-in-time traversal and audit; it simply drops
    out of present-time queries. Returns the number of edges invalidated (0 or 1).
    """
    src = canon_entity(src_entity, normalize=normalize)
    dst = canon_entity(dst_entity, normalize=normalize)
    rel = rel_type.strip()
    when = event_time or datetime.now(timezone.utc)

    edge = (await db.execute(
        select(Relationship).where(and_(
            Relationship.namespace == namespace,
            Relationship.agent_id == agent_id,
            Relationship.src_entity == src,
            Relationship.rel_type == rel,
            Relationship.dst_entity == dst,
            Relationship.valid_to.is_(None),
        ))
    )).scalars().first()
    if edge is None:
        return 0

    edge.valid_to = when
    await _log_invalidation(db, namespace, edge, reason="unrelate")
    await db.commit()
    return 1


async def _log_invalidation(db: AsyncSession, namespace: str, edge: Relationship, *, reason: str) -> None:
    await chain_log(
        db, namespace=namespace, agent_id=edge.agent_id,
        op="unrelate", memory_id=edge.id, content_hash=edge.content_hash,
        payload={"src": edge.src_entity, "rel_type": edge.rel_type,
                 "dst": edge.dst_entity, "reason": reason},
    )
    from .webhook_service import dispatch_event, RELATIONSHIP_INVALIDATED
    await dispatch_event(db, namespace, RELATIONSHIP_INVALIDATED, {
        "agent_id": edge.agent_id,
        "edge_id": str(edge.id),
        "src": edge.src_entity,
        "rel_type": edge.rel_type,
        "dst": edge.dst_entity,
        "reason": reason,
    })


# ── Read / traversal ────────────────────────────────────────────────────────────


async def _live_edges(
    db: AsyncSession,
    namespace: str,
    agent_id: str,
    as_of: Optional[datetime],
    rel_types: Optional[list[str]] = None,
) -> list[Relationship]:
    conds = [Relationship.namespace == namespace, Relationship.agent_id == agent_id]
    if as_of is None:
        conds.append(Relationship.valid_to.is_(None))
    if rel_types:
        conds.append(Relationship.rel_type.in_(rel_types))
    rows = (await db.execute(select(Relationship).where(and_(*conds)))).scalars().all()
    if as_of is None:
        return list(rows)
    # Point-in-time validity is compared in Python to dodge SQLite naive-datetime
    # pitfalls; the namespace+agent edge set is bounded.
    return [e for e in rows if _is_valid_at(e, as_of)]


def _adjacency(edges: list[Relationship], direction: str) -> dict[str, list[Relationship]]:
    """Map each entity to the edges leaving it under the chosen direction semantics."""
    adj: dict[str, list[Relationship]] = {}
    for e in edges:
        if direction in ("out", "any"):
            adj.setdefault(e.src_entity, []).append(e)
        if direction in ("in", "any"):
            adj.setdefault(e.dst_entity, []).append(e)
    return adj


def _other_end(edge: Relationship, current: str) -> str:
    return edge.dst_entity if edge.src_entity == current else edge.src_entity


async def neighbors(
    db: AsyncSession,
    namespace: str,
    agent_id: str,
    entity: str,
    *,
    depth: int = 1,
    as_of: Optional[datetime] = None,
    rel_types: Optional[list[str]] = None,
    direction: str = "any",
    normalize: bool = False,
) -> dict[str, Any]:
    """
    Return entities reachable from ``entity`` within ``depth`` hops.

    ``direction``: ``out`` follows src→dst, ``in`` follows dst→src, ``any`` (default)
    treats edges as undirected — the right default for COI / related-party reach.
    Each neighbor is returned with its shortest hop distance; the edges traversed
    at the first hop are included for context.
    """
    start = canon_entity(entity, normalize=normalize)
    edges = await _live_edges(db, namespace, agent_id, as_of, rel_types)
    adj = _adjacency(edges, direction)

    dist: dict[str, int] = {start: 0}
    q: deque[str] = deque([start])
    while q:
        node = q.popleft()
        if dist[node] >= depth:
            continue
        for edge in adj.get(node, []):
            nxt = _other_end(edge, node)
            if nxt not in dist:
                dist[nxt] = dist[node] + 1
                q.append(nxt)

    neighbor_list = [
        {"entity": e, "depth": d}
        for e, d in sorted(dist.items(), key=lambda kv: (kv[1], kv[0]))
        if e != start
    ]
    direct = [_edge_dict(e) for e in adj.get(start, [])]
    return {
        "entity": start,
        "depth": depth,
        "as_of": as_of.isoformat() if as_of else None,
        "neighbors": neighbor_list,
        "direct_edges": direct,
    }


async def path(
    db: AsyncSession,
    namespace: str,
    agent_id: str,
    src_entity: str,
    dst_entity: str,
    *,
    max_depth: int = 4,
    as_of: Optional[datetime] = None,
    rel_types: Optional[list[str]] = None,
    normalize: bool = False,
) -> dict[str, Any]:
    """
    Shortest connection between two entities — the conflict-of-interest /
    related-party query. Returns the chain of edges linking ``src`` to ``dst``
    (empty when unconnected within ``max_depth``). Treats edges as undirected.
    """
    src = canon_entity(src_entity, normalize=normalize)
    dst = canon_entity(dst_entity, normalize=normalize)
    edges = await _live_edges(db, namespace, agent_id, as_of, rel_types)
    adj = _adjacency(edges, "any")

    # BFS tracking the edge used to reach each node, to reconstruct the trail.
    prev: dict[str, tuple[str, Relationship]] = {}
    seen = {src}
    q: deque[tuple[str, int]] = deque([(src, 0)])
    found = src == dst
    while q and not found:
        node, d = q.popleft()
        if d >= max_depth:
            continue
        for edge in adj.get(node, []):
            nxt = _other_end(edge, node)
            if nxt not in seen:
                seen.add(nxt)
                prev[nxt] = (node, edge)
                if nxt == dst:
                    found = True
                    break
                q.append((nxt, d + 1))

    trail: list[dict] = []
    if found and src != dst:
        cur = dst
        while cur != src:
            node, edge = prev[cur]
            trail.append(_edge_dict(edge))
            cur = node
        trail.reverse()

    return {
        "src": src,
        "dst": dst,
        "connected": found,
        "hops": len(trail),
        "as_of": as_of.isoformat() if as_of else None,
        "path": trail,
    }


async def extract_and_relate(
    db: AsyncSession,
    namespace: str,
    *,
    agent_id: str,
    text: str,
    event_time: datetime,
    normalize: bool = False,
    exclusive: bool = False,
    use_llm: bool = False,
) -> dict[str, Any]:
    """
    Extract ``(src, rel_type, dst)`` triplets from ``text`` and assert each as an
    edge. Rule-based by default (deterministic, auditable); LLM extraction is
    opt-in via ``use_llm`` and falls back to rules if unavailable. Returns the
    extracted triplets and the created edges.
    """
    from .graph_extract import extract_relationships

    triplets = await extract_relationships(text, use_llm=use_llm)
    edges: list[dict[str, Any]] = []
    for src, rel, dst in triplets:
        edge = await relate(
            db, namespace,
            agent_id=agent_id, src_entity=src, rel_type=rel, dst_entity=dst,
            event_time=event_time, exclusive=exclusive, normalize=normalize,
            source="extracted",
        )
        edges.append(_edge_dict(edge))
    return {
        "extracted": [{"src": s, "rel_type": r, "dst": d} for (s, r, d) in triplets],
        "edges": edges,
    }


async def entity_distances(
    db: AsyncSession,
    namespace: str,
    agent_id: str,
    anchor: str,
    candidates: set[str],
    *,
    max_depth: int = 3,
    as_of: Optional[datetime] = None,
    normalize: bool = False,
) -> dict[str, int]:
    """
    Graph hop-distance from ``anchor`` to each candidate entity (BFS, undirected).

    Unreachable candidates are omitted. Used by graph-proximity reranking to boost
    facts about entities near the query's anchor entity.
    """
    start = canon_entity(anchor, normalize=normalize)
    wanted = {canon_entity(c, normalize=normalize) for c in candidates}
    edges = await _live_edges(db, namespace, agent_id, as_of)
    adj = _adjacency(edges, "any")

    dist: dict[str, int] = {start: 0}
    q: deque[str] = deque([start])
    out: dict[str, int] = {}
    while q:
        node = q.popleft()
        if node in wanted:
            out[node] = dist[node]
        if dist[node] >= max_depth:
            continue
        for edge in adj.get(node, []):
            nxt = _other_end(edge, node)
            if nxt not in dist:
                dist[nxt] = dist[node] + 1
                q.append(nxt)
    return out


def _edge_dict(e: Relationship) -> dict[str, Any]:
    return {
        "id": str(e.id),
        "src": e.src_entity,
        "rel_type": e.rel_type,
        "dst": e.dst_entity,
        "event_time": e.event_time.isoformat() if e.event_time else None,
        "valid_to": e.valid_to.isoformat() if e.valid_to else None,
        "source": e.source,
        "metadata": dict(e.metadata_ or {}),
    }
