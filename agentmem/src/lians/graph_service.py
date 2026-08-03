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
relationships. Traversal performs indexed, bounded frontier queries and discloses
whether a node/edge budget prevented a conclusive negative result.
"""
from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from typing import Any, Optional

from sqlalchemy import and_, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import load_only

from .audit_chain import chain_log
from .config import get_settings
from .entity_normalizer import cached_normalize
from .models import Relationship

_GRAPH_INVALIDATION_SERIALIZATION_RESERVE = 4 * 1024


class GraphMutationDecisionUnavailable(RuntimeError):
    """A graph mutation could not be decided completely inside its ceiling."""

    def __init__(
        self,
        code: str,
        message: str,
        *,
        status_code: int = 503,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.public_message = message
        self.status_code = status_code

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


def _exact_barrier(column, barrier_group: str | None):
    """Return an exact write-boundary predicate; null is not a wildcard."""
    return column.is_(None) if barrier_group is None else column == barrier_group


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
    barrier_override: Optional[str] = None,
    commit: bool = True,
) -> Relationship:
    """
    Assert a relationship edge.

    Idempotent: re-asserting an identical live triplet returns the existing edge.
    When ``exclusive`` is set, asserting ``src --rel_type--> X`` invalidates any
    other live ``src --rel_type--> Y`` (Y != X) — the deterministic equivalent of
    Graphiti's contradiction-driven invalidation, e.g. a person's current employer.
    """
    from .memory_service import _acquire_pg_advisory_lock, _get_barrier_group
    from .pii import assert_subject_not_erased
    from .subject_privacy import replace_subject_identifier

    src = canon_entity(src_entity, normalize=normalize)
    dst = canon_entity(dst_entity, normalize=normalize)
    raw_subject_id = subject_id
    persisted_subject_ref = (
        await assert_subject_not_erased(db, raw_subject_id, namespace)
        if raw_subject_id
        else None
    )
    if raw_subject_id and persisted_subject_ref:
        if src == raw_subject_id:
            src = persisted_subject_ref
        if dst == raw_subject_id:
            dst = persisted_subject_ref
        metadata = replace_subject_identifier(
            metadata or {}, raw_subject_id, persisted_subject_ref
        )
    rel = rel_type.strip()
    await _acquire_pg_advisory_lock(db, namespace, agent_id)
    barrier_group = await _get_barrier_group(
        db, namespace, agent_id, override=barrier_override
    )

    # Idempotent: identical live edge already exists.
    existing_conditions = [
            Relationship.namespace == namespace,
            Relationship.agent_id == agent_id,
            Relationship.src_entity == src,
            Relationship.rel_type == rel,
            Relationship.dst_entity == dst,
            Relationship.valid_to.is_(None),
    ]
    existing_conditions.append(
        _exact_barrier(Relationship.barrier_group, barrier_group)
    )
    existing_rows = list(
        (
            await db.execute(
                select(Relationship)
                .where(and_(*existing_conditions))
                .order_by(Relationship.id)
                .limit(2)
                .with_for_update()
            )
        ).scalars().all()
    )
    if len(existing_rows) > 1:
        raise GraphMutationDecisionUnavailable(
            "graph_live_edge_invariant_violation",
            "Multiple identical live relationship edges require reconciliation",
        )
    if existing_rows:
        return existing_rows[0]

    superseded: list[Relationship] = []
    if exclusive:
        exclusive_conditions = [
            Relationship.namespace == namespace,
            Relationship.agent_id == agent_id,
            Relationship.src_entity == src,
            Relationship.rel_type == rel,
            Relationship.dst_entity != dst,
            Relationship.valid_to.is_(None),
            _exact_barrier(Relationship.barrier_group, barrier_group),
        ]
        invalidation_limit = get_settings().graph_exclusive_invalidation_limit
        superseded = list(
            (
                await db.execute(
                    select(Relationship)
                    .options(
                        load_only(
                            Relationship.id,
                            Relationship.agent_id,
                            Relationship.src_entity,
                            Relationship.rel_type,
                            Relationship.dst_entity,
                            Relationship.barrier_group,
                            Relationship.content_hash,
                        )
                    )
                    .where(and_(*exclusive_conditions))
                    .order_by(Relationship.id)
                    .limit(invalidation_limit + 1)
                    .with_for_update()
                )
            ).scalars().all()
        )
        if len(superseded) > invalidation_limit:
            raise GraphMutationDecisionUnavailable(
                "graph_exclusive_invalidation_capacity_exceeded",
                "Exclusive relationship invalidation exceeds the atomic capacity",
            )

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
        subject_id=persisted_subject_ref,
        source=source,
        metadata_=metadata or {},
        content_hash=_rel_hash(src, rel, dst, event_time),
    )
    db.add(edge)
    await db.flush()

    if exclusive:
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
    if commit:
        await db.commit()
        await db.refresh(edge)
    else:
        await db.flush()
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
    barrier_override: Optional[str] = None,
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

    identity_conditions = [
            Relationship.namespace == namespace,
            Relationship.agent_id == agent_id,
            Relationship.src_entity == src,
            Relationship.rel_type == rel,
            Relationship.dst_entity == dst,
            Relationship.valid_to.is_(None),
    ]
    observed_conditions = list(identity_conditions)
    if barrier_override is not None:
        observed_conditions.append(Relationship.barrier_group == barrier_override)
    observed_rows = (
        await db.execute(
            select(Relationship.id, Relationship.subject_id).where(
                and_(*observed_conditions)
            )
            .order_by(Relationship.id)
            .limit(get_settings().graph_exclusive_invalidation_limit + 1)
        )
    ).all()
    if len(observed_rows) > get_settings().graph_exclusive_invalidation_limit:
        raise GraphMutationDecisionUnavailable(
            "graph_mutation_candidate_capacity_exceeded",
            "Relationship mutation candidates exceed the configured capacity",
        )
    if not observed_rows:
        return 0

    # Subject erasure takes the subject boundary before graph rows; match that
    # order before taking the agent mutex and the authoritative row lock. Lock
    # every observed candidate because an unbarriered caller's effective agent
    # assignment is resolved only after the agent mutex is held.
    from .pii import lock_subject_key_for_update

    for subject_ref in sorted(
        {str(subject_ref) for _, subject_ref in observed_rows if subject_ref}
    ):
        await lock_subject_key_for_update(db, subject_ref, namespace)
    from .memory_service import _acquire_pg_advisory_lock, _get_barrier_group

    await _acquire_pg_advisory_lock(db, namespace, agent_id)
    effective_barrier = await _get_barrier_group(
        db,
        namespace,
        agent_id,
        override=barrier_override,
    )
    authoritative_conditions = [
        *identity_conditions,
        _exact_barrier(Relationship.barrier_group, effective_barrier),
        Relationship.id.in_([row_id for row_id, _ in observed_rows]),
    ]
    authoritative_rows = list(
        (
            await db.execute(
                select(Relationship)
                .where(and_(*authoritative_conditions))
                .order_by(Relationship.id)
                .execution_options(populate_existing=True)
                .with_for_update()
                .limit(2)
            )
        ).scalars().all()
    )
    if len(authoritative_rows) > 1:
        raise GraphMutationDecisionUnavailable(
            "graph_live_edge_invariant_violation",
            "Multiple identical live relationship edges require reconciliation",
        )
    if not authoritative_rows:
        return 0
    edge = authoritative_rows[0]

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
    from .webhook_service import RELATIONSHIP_INVALIDATED, dispatch_event
    await dispatch_event(db, namespace, RELATIONSHIP_INVALIDATED, {
        "agent_id": edge.agent_id,
        "edge_id": str(edge.id),
        "src": edge.src_entity,
        "rel_type": edge.rel_type,
        "dst": edge.dst_entity,
        "reason": reason,
    }, barrier_group=edge.barrier_group)


# ── Read / traversal ────────────────────────────────────────────────────────────


_GRAPH_FRONTIER_BIND_BATCH = 400
_DEFAULT_GRAPH_MAX_NODES = 5_000
_DEFAULT_GRAPH_MAX_EDGES = 20_000


def _edge_scope_filters(
    namespace: str,
    agent_id: str,
    as_of: Optional[datetime],
    rel_types: Optional[list[str]],
    barrier_override: Optional[str],
) -> list[Any]:
    filters: list[Any] = [
        Relationship.namespace == namespace,
        Relationship.agent_id == agent_id,
    ]
    if as_of is None:
        filters.append(Relationship.valid_to.is_(None))
    else:
        filters.extend(
            (
                Relationship.valid_from <= as_of,
                or_(Relationship.valid_to.is_(None), Relationship.valid_to > as_of),
            )
        )
    if rel_types:
        filters.append(Relationship.rel_type.in_(rel_types))
    if barrier_override is not None:
        filters.append(
            or_(
                Relationship.barrier_group.is_(None),
                Relationship.barrier_group == barrier_override,
            )
        )
    return filters


async def _frontier_edges(
    db: AsyncSession,
    namespace: str,
    agent_id: str,
    frontier: set[str],
    direction: str,
    as_of: Optional[datetime],
    rel_types: Optional[list[str]] = None,
    barrier_override: Optional[str] = None,
    *,
    edge_budget: int,
    seen_edge_ids: set[Any],
) -> tuple[list[Relationship], bool]:
    """Fetch only indexed edges incident to one BFS frontier.

    The boolean is true when the edge budget prevented an exhaustive frontier
    read.  Callers must treat a negative result as unknown in that case.
    """
    if not frontier or edge_budget <= 0:
        return [], bool(frontier)
    scope = _edge_scope_filters(
        namespace,
        agent_id,
        as_of,
        rel_types,
        barrier_override,
    )
    ordered_frontier = sorted(frontier)
    rows: list[Relationship] = []
    truncated = False
    for start in range(0, len(ordered_frontier), _GRAPH_FRONTIER_BIND_BATCH):
        remaining = edge_budget - len(rows)
        if remaining <= 0:
            truncated = True
            break
        chunk = ordered_frontier[start : start + _GRAPH_FRONTIER_BIND_BATCH]
        if direction == "out":
            incident = Relationship.src_entity.in_(chunk)
        elif direction == "in":
            incident = Relationship.dst_entity.in_(chunk)
        else:
            incident = or_(
                Relationship.src_entity.in_(chunk),
                Relationship.dst_entity.in_(chunk),
            )
        fetched = list(
            (
                await db.execute(
                    select(Relationship)
                    .where(*scope, incident)
                    .order_by(Relationship.id)
                    .limit(remaining + 1)
                )
            ).scalars()
        )
        if len(fetched) > remaining:
            truncated = True
            fetched = fetched[:remaining]
        for edge in fetched:
            if edge.id in seen_edge_ids:
                continue
            seen_edge_ids.add(edge.id)
            rows.append(edge)
        if truncated:
            break
    return rows, truncated


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
    barrier_override: Optional[str] = None,
    max_nodes: int = _DEFAULT_GRAPH_MAX_NODES,
    max_edges: int = _DEFAULT_GRAPH_MAX_EDGES,
) -> dict[str, Any]:
    """
    Return entities reachable from ``entity`` within ``depth`` hops.

    ``direction``: ``out`` follows src→dst, ``in`` follows dst→src, ``any`` (default)
    treats edges as undirected — the right default for COI / related-party reach.
    Each neighbor is returned with its shortest hop distance; the edges traversed
    at the first hop are included for context.
    """
    start = canon_entity(entity, normalize=normalize)
    dist: dict[str, int] = {start: 0}
    frontier = {start}
    seen_edge_ids: set[Any] = set()
    direct_edges: list[Relationship] = []
    search_complete = True
    for current_depth in range(depth):
        edges, edge_truncated = await _frontier_edges(
            db,
            namespace,
            agent_id,
            frontier,
            direction,
            as_of,
            rel_types,
            barrier_override,
            edge_budget=max_edges - len(seen_edge_ids),
            seen_edge_ids=seen_edge_ids,
        )
        if current_depth == 0:
            direct_edges = list(edges)
        next_frontier: set[str] = set()
        for edge in edges:
            candidates: list[str] = []
            if direction in {"out", "any"} and edge.src_entity in frontier:
                candidates.append(edge.dst_entity)
            if direction in {"in", "any"} and edge.dst_entity in frontier:
                candidates.append(edge.src_entity)
            for candidate in candidates:
                if candidate in dist:
                    continue
                if len(dist) >= max_nodes:
                    search_complete = False
                    break
                dist[candidate] = current_depth + 1
                next_frontier.add(candidate)
            if not search_complete:
                break
        if edge_truncated:
            search_complete = False
        if not search_complete or not next_frontier:
            break
        frontier = next_frontier

    neighbor_list = [
        {"entity": e, "depth": d}
        for e, d in sorted(dist.items(), key=lambda kv: (kv[1], kv[0]))
        if e != start
    ]
    direct = [_edge_dict(edge) for edge in direct_edges]
    return {
        "entity": start,
        "depth": depth,
        "as_of": as_of.isoformat() if as_of else None,
        "neighbors": neighbor_list,
        "direct_edges": direct,
        "search_complete": search_complete,
        "truncated": not search_complete,
        "nodes_examined": len(dist),
        "edges_examined": len(seen_edge_ids),
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
    barrier_override: Optional[str] = None,
    max_nodes: int = _DEFAULT_GRAPH_MAX_NODES,
    max_edges: int = _DEFAULT_GRAPH_MAX_EDGES,
) -> dict[str, Any]:
    """
    Shortest connection between two entities — the conflict-of-interest /
    related-party query. Returns the chain of edges linking ``src`` to ``dst``
    (empty when unconnected within ``max_depth``). ``connected`` is ``None``
    when a node/edge budget prevents a conclusive negative. Treats edges as
    undirected.
    """
    src = canon_entity(src_entity, normalize=normalize)
    dst = canon_entity(dst_entity, normalize=normalize)
    # Indexed frontier BFS tracks the edge used to reach each node, allowing a
    # shortest trail without hydrating the entire tenant-agent graph.
    prev: dict[str, tuple[str, Relationship]] = {}
    seen = {src}
    seen_edge_ids: set[Any] = set()
    frontier = {src}
    found = src == dst
    search_complete = True
    for _current_depth in range(max_depth):
        if found or not frontier:
            break
        edges, edge_truncated = await _frontier_edges(
            db,
            namespace,
            agent_id,
            frontier,
            "any",
            as_of,
            rel_types,
            barrier_override,
            edge_budget=max_edges - len(seen_edge_ids),
            seen_edge_ids=seen_edge_ids,
        )
        next_frontier: set[str] = set()
        for edge in edges:
            endpoints: list[tuple[str, str]] = []
            if edge.src_entity in frontier:
                endpoints.append((edge.src_entity, edge.dst_entity))
            if edge.dst_entity in frontier:
                endpoints.append((edge.dst_entity, edge.src_entity))
            for parent, candidate in endpoints:
                if candidate in seen:
                    continue
                if len(seen) >= max_nodes:
                    search_complete = False
                    break
                seen.add(candidate)
                prev[candidate] = (parent, edge)
                if candidate == dst:
                    found = True
                    break
                next_frontier.add(candidate)
            if found or not search_complete:
                break
        if edge_truncated:
            search_complete = False
        if found or not search_complete:
            break
        frontier = next_frontier

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
        "connected": True if found else False if search_complete else None,
        "hops": len(trail),
        "as_of": as_of.isoformat() if as_of else None,
        "path": trail,
        "search_complete": search_complete,
        "truncated": not search_complete,
        "nodes_examined": len(seen),
        "edges_examined": len(seen_edge_ids),
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
    barrier_override: Optional[str] = None,
) -> dict[str, Any]:
    """
    Extract ``(src, rel_type, dst)`` triplets from ``text`` and assert each as an
    edge. Rule-based by default (deterministic, auditable); LLM extraction is
    opt-in via ``use_llm`` and falls back to rules if unavailable. Returns the
    extracted triplets and the created edges.
    """
    from .graph_extract import extract_relationships

    raw_triplets = await extract_relationships(text, use_llm=use_llm)
    settings = get_settings()
    if len(raw_triplets) > settings.graph_extract_candidate_limit:
        raise GraphMutationDecisionUnavailable(
            "graph_extraction_candidate_capacity_exceeded",
            "Extracted relationship candidates exceed the atomic row capacity",
            status_code=413,
        )

    triplets: list[tuple[str, str, str]] = []
    seen: set[tuple[str, str, str]] = set()
    serialized_bytes = 2
    for raw_triplet in raw_triplets:
        if (
            not isinstance(raw_triplet, (list, tuple))
            or len(raw_triplet) != 3
            or not all(isinstance(value, str) for value in raw_triplet)
        ):
            raise GraphMutationDecisionUnavailable(
                "graph_extraction_candidate_invalid",
                "The relationship extractor returned an invalid candidate",
            )
        raw_src, raw_rel, raw_dst = raw_triplet
        src = " ".join(raw_src.split())
        rel = raw_rel.strip()
        dst = " ".join(raw_dst.split())
        if (
            not src
            or len(src) > 1_000
            or not rel
            or len(rel) > 200
            or not dst
            or len(dst) > 1_000
        ):
            raise GraphMutationDecisionUnavailable(
                "graph_extraction_candidate_field_capacity_exceeded",
                "An extracted relationship field exceeds the supported capacity",
                status_code=413,
            )
        identity = (
            canon_entity(src, normalize=normalize),
            rel,
            canon_entity(dst, normalize=normalize),
        )
        if identity in seen:
            continue
        seen.add(identity)
        candidate_bytes = len(
            json.dumps(
                {"src": src, "rel_type": rel, "dst": dst},
                ensure_ascii=False,
                separators=(",", ":"),
            ).encode("utf-8")
        )
        # Reserve deterministic envelope/edge/audit serialization overhead in
        # addition to the visible triplet itself.
        serialized_bytes += candidate_bytes + 512 + int(bool(triplets))
        if serialized_bytes > settings.graph_extract_candidate_bytes_limit:
            raise GraphMutationDecisionUnavailable(
                "graph_extraction_candidate_bytes_exceeded",
                "Extracted relationship candidates exceed the atomic byte capacity",
                status_code=413,
            )
        triplets.append((src, rel, dst))

    if exclusive and triplets:
        from .memory_service import _acquire_pg_advisory_lock, _get_barrier_group

        destinations_by_pair: dict[tuple[str, str], str] = {}
        for src, rel, dst in triplets:
            pair = (canon_entity(src, normalize=normalize), rel)
            normalized_dst = canon_entity(dst, normalize=normalize)
            prior_destination = destinations_by_pair.setdefault(pair, normalized_dst)
            if prior_destination != normalized_dst:
                raise GraphMutationDecisionUnavailable(
                    "graph_extraction_exclusive_conflict",
                    "Exclusive extraction produced multiple destinations for one relation",
                    status_code=409,
                )

        # Serialize the complete preflight with every cooperating graph write.
        # Re-entrant advisory locking inside relate() preserves the same
        # transaction boundary without opening a race after this inventory.
        await _acquire_pg_advisory_lock(db, namespace, agent_id)
        barrier_group = await _get_barrier_group(
            db,
            namespace,
            agent_id,
            override=barrier_override,
        )
        invalidation_count = 0
        pairs = sorted(destinations_by_pair.items())
        for start in range(0, len(pairs), 100):
            chunk = pairs[start : start + 100]
            pair_conditions = [
                and_(
                    Relationship.src_entity == src,
                    Relationship.rel_type == rel,
                    Relationship.dst_entity != dst,
                )
                for (src, rel), dst in chunk
            ]
            invalidation_count += int(
                (
                    await db.execute(
                        select(func.count(Relationship.id)).where(
                            Relationship.namespace == namespace,
                            Relationship.agent_id == agent_id,
                            Relationship.valid_to.is_(None),
                            _exact_barrier(
                                Relationship.barrier_group,
                                barrier_group,
                            ),
                            or_(*pair_conditions),
                        )
                    )
                ).scalar_one()
            )
            if invalidation_count > settings.graph_exclusive_invalidation_limit:
                raise GraphMutationDecisionUnavailable(
                    "graph_extraction_exclusive_capacity_exceeded",
                    "Exclusive extraction invalidations exceed the atomic capacity",
                    status_code=413,
                )
        if (
            serialized_bytes
            + invalidation_count * _GRAPH_INVALIDATION_SERIALIZATION_RESERVE
            > settings.graph_extract_candidate_bytes_limit
        ):
            raise GraphMutationDecisionUnavailable(
                "graph_extraction_candidate_bytes_exceeded",
                "Extracted relationships and invalidations exceed the atomic byte capacity",
                status_code=413,
            )
    edges: list[dict[str, Any]] = []
    for src, rel, dst in triplets:
        edge = await relate(
            db, namespace,
            agent_id=agent_id, src_entity=src, rel_type=rel, dst_entity=dst,
            event_time=event_time, exclusive=exclusive, normalize=normalize,
            source="extracted",
            barrier_override=barrier_override,
            commit=False,
        )
        edges.append(_edge_dict(edge))
    await db.commit()
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
    barrier_override: Optional[str] = None,
    max_nodes: int = _DEFAULT_GRAPH_MAX_NODES,
    max_edges: int = _DEFAULT_GRAPH_MAX_EDGES,
) -> tuple[dict[str, int], bool]:
    """
    Graph hop-distance from ``anchor`` to each candidate entity (BFS, undirected).

    Unreachable candidates are omitted. The second value is false when a budget
    cap makes omitted candidates unknown rather than conclusively unreachable.
    """
    start = canon_entity(anchor, normalize=normalize)
    wanted = {canon_entity(c, normalize=normalize) for c in candidates}
    dist: dict[str, int] = {start: 0}
    out: dict[str, int] = {}
    frontier = {start}
    seen_edge_ids: set[Any] = set()
    search_complete = True
    for current_depth in range(max_depth + 1):
        for node in frontier:
            if node in wanted:
                out[node] = current_depth
        if wanted.issubset(out) or current_depth >= max_depth:
            break
        edges, truncated = await _frontier_edges(
            db,
            namespace,
            agent_id,
            frontier,
            "any",
            as_of,
            barrier_override=barrier_override,
            edge_budget=max_edges - len(seen_edge_ids),
            seen_edge_ids=seen_edge_ids,
        )
        next_frontier: set[str] = set()
        for edge in edges:
            candidates: list[str] = []
            if edge.src_entity in frontier:
                candidates.append(edge.dst_entity)
            if edge.dst_entity in frontier:
                candidates.append(edge.src_entity)
            for candidate in candidates:
                if candidate in dist:
                    continue
                if len(dist) >= max_nodes:
                    truncated = True
                    break
                dist[candidate] = current_depth + 1
                next_frontier.add(candidate)
            if truncated:
                break
        if truncated or not next_frontier:
            if truncated:
                search_complete = False
            break
        frontier = next_frontier
    return out, search_complete


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
