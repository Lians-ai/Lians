"""
Core memory service: add, recall, recall(as_of) — used by API routes.

Performance roadmap changes wired here:
  Change 1  — recall queries live_facts (compact read model), not memories.
  Change 2  — keyed-vs-semantic router: keyed queries skip embed + ANN entirely.
  Change 3  — supersession fast path (keyed deterministic); async LLM worker.
  Change 6  — DEK cache: subject keys unwrapped once, cached in-process.
  Change 7  — session cache: working set prefetched and served from memory.
  Change 10 — recall instrumented as sub-spans: embed/search/decrypt/assemble.
"""
from __future__ import annotations

import asyncio
import heapq
import hashlib
import json
import logging
import math
import time as _time
from datetime import datetime, timedelta, timezone
from typing import Any, Optional
from uuid import UUID

from sqlalchemy import Text, and_, bindparam, cast, func, literal, or_, select, text
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased, load_only

from .audit_chain import chain_log
from .cache import (
    get_agent_cache_generation,
    get_cached_recall,
    invalidate_agent,
    set_cached_recall,
)
from .cache_fence import acquire_namespace_cache_lock
from .config import get_settings
from .crypto import encrypt_content
from .current_facts import compute_predicate_key, keyed_lookup, remove_live_facts, upsert_live_fact
from .dek_cache import cache_dek, get_cached_dek
from .embeddings import get_embedding_provider
from .governance_service import estimate_ingest_bytes, reserve_namespace_usage
from .idempotency import IdempotencyReplayUnavailable
from .metrics import (
    observe_add,
    observe_recall,
    record_best_effort_failure,
    record_recall,
    record_write,
)
from .models import (
    AgentBarrierGroup,
    ConflictFlag,
    EventLog,
    LiveFact,
    Memory,
    NamespacePolicy,
)
from .mutation_safety import MutationVersionConflict, assert_expected_updated_at
from .pii import (
    assert_subject_not_erased,
    get_or_create_subject_key,
    lock_subject_key_for_update,
)
from .ranking import (
    hybrid_recall,
    lexical_reranker_primary_enabled,
    recall_candidate_contract,
    reranker_enabled,
)
from .schemas import (
    ConflictFlagOut,
    ConflictListResult,
    ConflictResolveRequest,
    ConflictResolveResult,
    ContextRequest,
    ContextResult,
    LineageEdge,
    LineageNode,
    MemoryAdd,
    MemoryBatchResult,
    MemoryLineageResult,
    MemoryOut,
    RecallRequest,
    RecallResult,
    RetentionPolicyIn,
    RetentionPolicyOut,
    RetentionPruneResult,
    SupersessionAction,
    SupersessionActionResult,
    SupersessionReviewItem,
    SupersessionReviewResult,
)
from .session_cache import invalidate_working_set
from .subject_key_loader import load_subject_keys
from .subject_privacy import (
    replace_subject_identifier,
    subject_reference,
)
from .supersession import SupersessionDecisionUnavailable, _utc, run_supersession
from .telemetry import tracer

logger = logging.getLogger("lians.memory_service")

_IMPORTANCE_RECENCY_HALF_LIFE_DAYS = 90.0
_SUPERSESSION_BIND_BATCH = 400
_STALE_CLAUSE_MARK_LIMIT = 5_000


def _barrier_visible(row: Any, barrier_group: Optional[str]) -> bool:
    """Application-layer equivalent of the PostgreSQL barrier RLS policy."""
    return (
        barrier_group is None
        or getattr(row, "barrier_group", None) is None
        or getattr(row, "barrier_group", None) == barrier_group
    )


def _barrier_filter(column: Any, barrier_group: Optional[str]) -> Optional[Any]:
    if barrier_group is None:
        return None
    return or_(column.is_(None), column == barrier_group)


def _write_lock_keys(namespace: str, agent_id: str) -> tuple[int, int]:
    h = hashlib.sha256(f"{namespace}\x00{agent_id}".encode()).digest()
    return (
        int.from_bytes(h[:4], "big", signed=True),
        int.from_bytes(h[4:8], "big", signed=True),
    )


_write_locks: dict[tuple[int, str, str], asyncio.Lock] = {}


async def _get_in_process_lock(namespace: str, agent_id: str) -> asyncio.Lock:
    loop = asyncio.get_running_loop()
    key = (id(loop), namespace, agent_id)
    if key not in _write_locks:
        _write_locks[key] = asyncio.Lock()
    return _write_locks[key]


def _uses_postgresql(db: AsyncSession) -> bool:
    engine = db.sync_session.get_bind()
    return engine.dialect.name == "postgresql"


async def _acquire_pg_advisory_lock(
    db: AsyncSession,
    namespace: str,
    agent_id: str,
    *,
    shared: bool = False,
) -> bool:
    if not _uses_postgresql(db):
        return False
    k1, k2 = _write_lock_keys(namespace, agent_id)
    function = "pg_advisory_xact_lock_shared" if shared else "pg_advisory_xact_lock"
    await db.execute(text(f"SELECT {function}(:k1, :k2)"), {"k1": k1, "k2": k2})
    return True


async def _fence_recall_caches_before_commit(
    db: AsyncSession,
    namespace: str,
    agent_id: str,
) -> None:
    """Invalidate local state and advance the shared generation before commit.

    PostgreSQL writers must already hold the agent's exclusive transaction
    advisory lock.  Redis failure propagates so the durable mutation rolls
    back.  Non-PostgreSQL deployments never use these caches because they lack
    the cross-process lock needed to make cache validation linearizable.
    """
    invalidate_working_set(namespace, agent_id)
    if get_settings().recall_cache_enabled and _uses_postgresql(db):
        await invalidate_agent(namespace, agent_id)


def _compute_importance(event_time: datetime, caller_salience: float) -> float:
    now = datetime.now(timezone.utc)
    if event_time.tzinfo is None:
        event_time = event_time.replace(tzinfo=timezone.utc)
    age_days = (now - event_time).total_seconds() / 86400
    recency = math.exp(-math.log(2) * age_days / _IMPORTANCE_RECENCY_HALF_LIFE_DAYS)
    return round(0.4 * recency + 0.6 * caller_salience, 4)


async def _get_barrier_group(
    db: AsyncSession, namespace: str, agent_id: str, override: Optional[str] = None
) -> Optional[str]:
    if override is not None:
        # The calling API key is barrier-scoped (SSO gateway picked it from the
        # caller's IdP group) — the key's barrier is authoritative, no lookup.
        group: Optional[str] = override
    else:
        stmt = select(AgentBarrierGroup).where(
            and_(AgentBarrierGroup.namespace == namespace, AgentBarrierGroup.agent_id == agent_id)
        )
        result = await db.execute(stmt)
        row = result.scalar_one_or_none()
        group = row.group_name if row else None

    # Engage the PostgreSQL RLS barrier policy by setting the session variable the
    # RESTRICTIVE barrier_isolation policy reads (migration 0013). An unbarriered
    # agent sets '' and sees every row in its namespace (compliance-officer view);
    # a group-scoped agent sees only NULL-barrier (shared) and same-group rows.
    # No-op on SQLite (no set_config) — those tests rely on app-layer filtering.
    # PostgreSQL errors propagate so a broken RLS context fails closed.
    if db.get_bind().dialect.name == "postgresql":
        await db.execute(
            text("SELECT set_config('agentmem.barrier_group', :bg, true)"),
            {"bg": group or ""},
        )
    else:
        pass

    return group


async def _resolve_subject_key(
    db: AsyncSession,
    subject_id: str,
    namespace: str,
    *,
    legacy_subject_id: str | None = None,
) -> bytes:
    """Return the DEK while holding the same subject fence as erasure.

    The cache is only a key-unwrapping optimization. It must never bypass the
    durable erasure tombstone or allow a write to race past crypto-shredding.
    """
    await assert_subject_not_erased(db, subject_id, namespace)
    cached = get_cached_dek(namespace, subject_id)
    if cached is not None:
        return cached
    key = await get_or_create_subject_key(
        db,
        subject_id,
        namespace,
        legacy_subject_id=legacy_subject_id,
    )
    cache_dek(namespace, subject_id, key)
    return key


async def _keys_for_rows(db: AsyncSession, namespace: str, rows: Any) -> dict[str, bytes]:
    """Load only the DEKs referenced by an already-bounded result window."""
    return await load_subject_keys(
        db,
        namespace,
        (getattr(row, "subject_id", None) for row in rows),
    )


def _content_hash(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


def _memory_to_out(mem: Memory, content: Optional[str]) -> MemoryOut:
    return MemoryOut(
        id=mem.id,
        namespace=mem.namespace,
        agent_id=mem.agent_id,
        content=content,
        subject_id=mem.subject_id,
        event_time=mem.event_time,
        ingestion_time=mem.ingestion_time,
        valid_from=mem.valid_from,
        valid_to=mem.valid_to,
        superseded_by=mem.superseded_by,
        supersession_confidence=mem.supersession_confidence,
        barrier_group=mem.barrier_group,
        importance=mem.importance,
        source=mem.source,
        content_hash=mem.content_hash,
        erased_at=mem.erased_at,
        metadata=dict(mem.metadata_ or {}),
    )


def _supersession_unavailable(code: str, message: str) -> SupersessionDecisionUnavailable:
    return SupersessionDecisionUnavailable(code, message)


async def _lock_supersession_rows(
    db: AsyncSession,
    *,
    namespace: str,
    agent_id: str,
    barrier_group: str | None,
    new_memory_id: UUID,
    new_event_time: datetime,
    superseded_ids: list[UUID],
    conflict_ids: list[UUID],
    superseded_by_id: UUID | None,
) -> tuple[list[Memory], list[Memory], Memory | None]:
    """Lock and revalidate a complete supersession verdict before mutation.

    Candidate discovery is bounded, but its ORM objects are only a decision
    snapshot. This second, deterministic read acquires row locks, refreshes the
    authoritative state, and verifies the corresponding current-fact rows.
    """

    superseded = list(superseded_ids)
    conflicts = list(conflict_ids)
    successor_ids = [superseded_by_id] if superseded_by_id is not None else []
    expected_ids = superseded + conflicts + successor_ids
    if len(expected_ids) != len(set(expected_ids)) or new_memory_id in expected_ids:
        raise _supersession_unavailable(
            "supersession_decision_invalid",
            "The supersession decision contains duplicate or self-referential candidates",
        )
    if len(expected_ids) > get_settings().supersession_candidate_limit:
        raise _supersession_unavailable(
            "supersession_decision_invalid",
            "The supersession decision exceeds the configured candidate capacity",
        )
    if not expected_ids:
        return [], [], None

    rows_by_id: dict[UUID, Memory] = {}
    ordered_ids = sorted(expected_ids, key=lambda value: value.hex)
    for start in range(0, len(ordered_ids), _SUPERSESSION_BIND_BATCH):
        page = ordered_ids[start : start + _SUPERSESSION_BIND_BATCH]
        rows = (
            (
                await db.execute(
                    select(Memory)
                    .options(
                        load_only(
                            Memory.id,
                            Memory.namespace,
                            Memory.agent_id,
                            Memory.metadata_,
                            Memory.event_time,
                            Memory.valid_to,
                            Memory.system_valid_to,
                            Memory.superseded_by,
                            Memory.barrier_group,
                            Memory.content_hash,
                            Memory.erased_at,
                            raiseload=True,
                        )
                    )
                    .where(Memory.namespace == namespace, Memory.id.in_(page))
                    .order_by(Memory.id.asc())
                    .execution_options(populate_existing=True)
                    .with_for_update()
                )
            )
            .scalars()
            .all()
        )
        rows_by_id.update((row.id, row) for row in rows)
    if set(rows_by_id) != set(expected_ids):
        raise _supersession_unavailable(
            "supersession_snapshot_changed",
            "A supersession candidate changed while the write was being prepared; retry",
        )

    for row in rows_by_id.values():
        if (
            row.namespace != namespace
            or row.agent_id != agent_id
            or row.barrier_group != barrier_group
            or row.valid_to is not None
            or row.system_valid_to is not None
            or row.superseded_by is not None
            or row.erased_at is not None
        ):
            raise _supersession_unavailable(
                "supersession_snapshot_changed",
                "A supersession candidate is no longer live in the same scope; retry",
            )

    new_time = _utc(new_event_time)
    if any(_utc(rows_by_id[row_id].event_time) > new_time for row_id in superseded):
        raise _supersession_unavailable(
            "supersession_decision_invalid",
            "A superseded candidate is newer than the incoming memory",
        )
    if any(_utc(rows_by_id[row_id].event_time) != new_time for row_id in conflicts):
        raise _supersession_unavailable(
            "supersession_decision_invalid",
            "A conflict candidate does not share the incoming event time",
        )
    successor = rows_by_id.get(superseded_by_id) if superseded_by_id is not None else None
    if successor is not None and _utc(successor.event_time) <= new_time:
        raise _supersession_unavailable(
            "supersession_decision_invalid",
            "A superseding candidate is not later than the incoming memory",
        )

    # Present-time candidates must have one exact derivative row. Locking that
    # projection now makes a concurrent erasure/supersession either precede
    # this verdict or wait until this transaction commits.
    live_ids: set[UUID] = set()
    for start in range(0, len(ordered_ids), _SUPERSESSION_BIND_BATCH):
        page = ordered_ids[start : start + _SUPERSESSION_BIND_BATCH]
        live_rows = (
            await db.execute(
                select(
                    LiveFact.memory_id,
                    LiveFact.namespace,
                    LiveFact.agent_id,
                    LiveFact.barrier_group,
                )
                .where(LiveFact.namespace == namespace, LiveFact.memory_id.in_(page))
                .order_by(LiveFact.memory_id.asc())
                .with_for_update()
            )
        ).all()
        for live in live_rows:
            if (
                live.namespace != namespace
                or live.agent_id != agent_id
                or live.barrier_group != barrier_group
            ):
                raise _supersession_unavailable(
                    "supersession_snapshot_changed",
                    "A supersession candidate's current-fact projection changed; retry",
                )
            live_ids.add(live.memory_id)
    if live_ids != set(expected_ids):
        raise _supersession_unavailable(
            "supersession_snapshot_changed",
            "A supersession candidate is missing from the current-fact projection; retry",
        )

    return (
        [rows_by_id[row_id] for row_id in superseded],
        [rows_by_id[row_id] for row_id in conflicts],
        successor,
    )


async def _mark_parents_stale(
    db: AsyncSession,
    namespace: str,
    agent_id: str,
    barrier_group: str | None,
    closed_memories: list[Memory],
    closure_time: datetime,
) -> None:
    """Batch-lock derived parents and update their denormalized projections."""

    closures_by_parent: dict[UUID, list[Memory]] = {}
    for closed in closed_memories:
        parent_ref = dict(closed.metadata_ or {}).get("_parent")
        if not parent_ref:
            continue
        try:
            parent_id = UUID(str(parent_ref))
        except (TypeError, ValueError) as exc:
            raise _supersession_unavailable(
                "supersession_parent_invariant_violation",
                "A derived memory has an invalid parent reference",
            ) from exc
        if parent_id == closed.id:
            raise _supersession_unavailable(
                "supersession_parent_invariant_violation",
                "A derived memory cannot be its own parent",
            )
        closures_by_parent.setdefault(parent_id, []).append(closed)
    if not closures_by_parent:
        return

    parent_ids = sorted(closures_by_parent, key=lambda value: value.hex)
    parent_byte_limit = get_settings().supersession_candidate_bytes_limit
    parent_inventory_bytes = 0
    for start in range(0, len(parent_ids), _SUPERSESSION_BIND_BATCH):
        page = parent_ids[start : start + _SUPERSESSION_BIND_BATCH]
        count, page_bytes = (
            await db.execute(
                select(
                    func.count(Memory.id),
                    func.coalesce(
                        func.sum(func.coalesce(func.length(cast(Memory.metadata_, Text)), 0) + 512),
                        0,
                    ),
                ).where(Memory.namespace == namespace, Memory.id.in_(page))
            )
        ).one()
        if int(count or 0) != len(page):
            raise _supersession_unavailable(
                "supersession_parent_snapshot_changed",
                "A derived memory parent is unavailable; retry",
            )
        parent_inventory_bytes += int(page_bytes or 0)
        if parent_inventory_bytes > parent_byte_limit:
            raise _supersession_unavailable(
                "supersession_parent_capacity_exceeded",
                "Derived parent metadata exceeds the configured supersession byte capacity",
            )
    parents: dict[UUID, Memory] = {}
    for start in range(0, len(parent_ids), _SUPERSESSION_BIND_BATCH):
        page = parent_ids[start : start + _SUPERSESSION_BIND_BATCH]
        rows = (
            (
                await db.execute(
                    select(Memory)
                    .options(
                        load_only(
                            Memory.id,
                            Memory.namespace,
                            Memory.agent_id,
                            Memory.metadata_,
                            Memory.valid_to,
                            Memory.system_valid_to,
                            Memory.superseded_by,
                            Memory.barrier_group,
                            Memory.content_hash,
                            Memory.erased_at,
                            raiseload=True,
                        )
                    )
                    .where(Memory.namespace == namespace, Memory.id.in_(page))
                    .order_by(Memory.id.asc())
                    .execution_options(populate_existing=True)
                    .with_for_update()
                )
            )
            .scalars()
            .all()
        )
        parents.update((row.id, row) for row in rows)
    if set(parents) != set(parent_ids) or any(
        parent.agent_id != agent_id or parent.barrier_group != barrier_group
        for parent in parents.values()
    ):
        raise _supersession_unavailable(
            "supersession_parent_snapshot_changed",
            "A derived memory parent changed scope or is unavailable; retry",
        )

    active_parents = {
        parent_id: parent
        for parent_id, parent in parents.items()
        if parent.erased_at is None
    }
    updated_metadata: dict[UUID, dict[str, Any]] = {}
    closure_iso = _utc(closure_time).isoformat()
    for parent_id, parent in active_parents.items():
        meta = dict(parent.metadata_ or {})
        raw_marks = meta.get("_stale_clauses")
        if raw_marks is None:
            marks: list[Any] = []
        elif isinstance(raw_marks, list):
            marks = list(raw_marks)
        else:
            raise _supersession_unavailable(
                "supersession_parent_invariant_violation",
                "A derived parent has an invalid stale-clause marker",
            )
        marks.extend(closure_iso for _ in closures_by_parent[parent_id])
        if len(marks) > _STALE_CLAUSE_MARK_LIMIT:
            raise _supersession_unavailable(
                "supersession_parent_capacity_exceeded",
                "A derived parent exceeds the stale-clause marker capacity",
            )
        meta["_stale_clauses"] = marks
        parent.metadata_ = meta
        updated_metadata[parent_id] = meta
    updated_metadata_bytes = sum(
        len(
            json.dumps(
                metadata,
                sort_keys=True,
                separators=(",", ":"),
                ensure_ascii=False,
                default=str,
            ).encode("utf-8")
        )
        + 512
        for metadata in updated_metadata.values()
    )
    if updated_metadata_bytes > parent_byte_limit:
        raise _supersession_unavailable(
            "supersession_parent_capacity_exceeded",
            "Derived parent updates exceed the configured supersession byte capacity",
        )

    # Load only projection identifiers, then use one typed executemany update
    # per portable bind page rather than hydrating encrypted content/embeddings.
    projection_rows: list[Any] = []
    active_ids = sorted(active_parents, key=lambda value: value.hex)
    for start in range(0, len(active_ids), _SUPERSESSION_BIND_BATCH):
        page = active_ids[start : start + _SUPERSESSION_BIND_BATCH]
        projection_rows.extend(
            (
                await db.execute(
                    select(
                        LiveFact.id,
                        LiveFact.memory_id,
                        LiveFact.agent_id,
                        LiveFact.barrier_group,
                    )
                    .where(LiveFact.namespace == namespace, LiveFact.memory_id.in_(page))
                    .order_by(LiveFact.memory_id.asc())
                    .with_for_update()
                )
            ).all()
        )
    if any(
        row.agent_id != agent_id or row.barrier_group != barrier_group
        for row in projection_rows
    ):
        raise _supersession_unavailable(
            "supersession_parent_snapshot_changed",
            "A derived parent's current-fact projection changed scope; retry",
        )
    current_parent_ids = {
        parent_id
        for parent_id, parent in active_parents.items()
        if (
            parent.valid_to is None
            and parent.system_valid_to is None
            and parent.superseded_by is None
        )
    }
    projected_parent_ids = {row.memory_id for row in projection_rows}
    if not current_parent_ids.issubset(projected_parent_ids):
        raise _supersession_unavailable(
            "supersession_parent_snapshot_changed",
            "A live derived parent is missing from the current-fact projection; retry",
        )
    closing_ids = {closed.id for closed in closed_memories}
    if projected_parent_ids - current_parent_ids - closing_ids:
        raise _supersession_unavailable(
            "supersession_parent_snapshot_changed",
            "A non-current derived parent has a stale current-fact projection; retry",
        )
    projection_update = (
        LiveFact.__table__.update()
        .where(LiveFact.__table__.c.id == bindparam("lians_live_fact_id"))
        .values(metadata=bindparam("lians_live_fact_metadata"))
    )
    update_parameters = [
        {
            "lians_live_fact_id": row.id,
            "lians_live_fact_metadata": updated_metadata[row.memory_id],
        }
        for row in projection_rows
        if row.memory_id in current_parent_ids
    ]
    for start in range(0, len(update_parameters), _SUPERSESSION_BIND_BATCH):
        await db.execute(
            projection_update,
            update_parameters[start : start + _SUPERSESSION_BIND_BATCH],
        )

    # The immutable namespace chain is serialized by chain_log, so preserve one
    # event per closed clause while all avoidable candidate/parent reads stay batched.
    for parent_id in parent_ids:
        parent = active_parents.get(parent_id)
        if parent is None:
            continue
        for closed in closures_by_parent[parent_id]:
            await chain_log(
                db,
                namespace=namespace,
                agent_id=agent_id,
                op="derived_stale_mark",
                memory_id=parent.id,
                content_hash=parent.content_hash,
                payload={
                    "closed_clause": str(closed.id),
                    "closure_time": closure_iso,
                },
            )


async def _ingest_derived_clause(
    db: AsyncSession,
    namespace: str,
    req: MemoryAdd,
    parent: Memory,
    clause: str,
    embedding: list[float],
    subject_key: Optional[bytes],
) -> None:
    """Store one extracted interjection clause as a derived memory.

    Same event_time/subject/barrier as the parent; structured keys are dropped
    so a clause can never trip keyed supersession against its own parent. Runs
    the full supersession funnel — this is where a cued revision clause closes
    its predecessor clause. Caller holds the agent write lock.
    """
    from .adapters import get_adapter
    sk = get_adapter().structured_keys
    meta = {
        k: v for k, v in (req.metadata or {}).items()
        if k not in sk and k not in ("_auto_meta", "_stale_clauses")
    }
    meta["_derived"] = "interjection"
    meta["_parent"] = str(parent.id)

    import uuid as _uuid
    new_id = _uuid.uuid4()
    # The clause inherits the parent turn's revision-cue status: the cue words
    # ("wait —", "actually", "now") usually stay in the surrounding chatter
    # while the extracted clause is the revision payload itself.
    from .supersession import _REVISION_CUE_RE
    parent_cued = bool(_REVISION_CUE_RE.search(req.content or ""))
    supersession = await run_supersession(
        db=db, namespace=namespace, agent_id=req.agent_id,
        new_content=clause, new_meta=meta, new_embedding=embedding,
        new_event_time=req.event_time, subject_key=subject_key,
        new_memory_id=new_id, cue_hint=parent_cued,
        barrier_group=parent.barrier_group,
    )
    superseded_rows, conflict_rows, successor = await _lock_supersession_rows(
        db,
        namespace=namespace,
        agent_id=req.agent_id,
        barrier_group=parent.barrier_group,
        new_memory_id=new_id,
        new_event_time=req.event_time,
        superseded_ids=list(supersession.superseded_ids),
        conflict_ids=list(supersession.conflict_ids),
        superseded_by_id=supersession.superseded_by_id,
    )

    now = datetime.now(timezone.utc)
    dmem = Memory(
        id=new_id,
        namespace=namespace,
        agent_id=req.agent_id,
        content_encrypted=encrypt_content(clause, subject_key) if subject_key else clause.encode(),
        subject_id=req.subject_id,
        embedding=embedding,
        metadata_=meta,
        event_time=req.event_time,
        ingestion_time=now,
        system_valid_from=now,
        valid_from=req.event_time,
        valid_to=None,
        importance=parent.importance,
        source=req.source,
        content_hash=_content_hash(clause),
        barrier_group=parent.barrier_group,
    )
    db.add(dmem)
    await db.flush()

    for old in superseded_rows:
        old.close_validity(valid_to=req.event_time, recorded_at=now)
        old.superseded_by = dmem.id
        old.supersession_confidence = supersession.confidence
    if superseded_rows:
        await db.flush()
    for old in superseded_rows:
        await chain_log(
            db, namespace=namespace, agent_id=req.agent_id,
            op="supersede", memory_id=old.id,
            content_hash=old.content_hash,
            payload={
                "superseded_by": str(dmem.id),
                "confidence": supersession.confidence,
                "relation": supersession.relation,
                "derived": True,
            },
        )
    await _mark_parents_stale(
        db,
        namespace,
        req.agent_id,
        parent.barrier_group,
        superseded_rows,
        req.event_time,
    )

    # Backdated arrival: a live later revision of this clause already exists.
    arrived_closed = False
    if successor is not None:
        dmem.close_validity(valid_to=successor.event_time, recorded_at=now)
        dmem.superseded_by = successor.id
        dmem.supersession_confidence = supersession.confidence
        arrived_closed = True
        await chain_log(
            db,
            namespace=namespace,
            agent_id=req.agent_id,
            op="supersede",
            memory_id=dmem.id,
            content_hash=dmem.content_hash,
            payload={
                "superseded_by": str(successor.id),
                "confidence": supersession.confidence,
                "relation": supersession.relation,
                "backdated_arrival": True,
                "derived": True,
            },
        )

    conflict_flags = [
        ConflictFlag(
            namespace=namespace,
            agent_id=req.agent_id,
            memory_a_id=conflict.id,
            memory_b_id=dmem.id,
            confidence=supersession.confidence,
            status="open",
        )
        for conflict in conflict_rows
    ]
    if conflict_flags:
        db.add_all(conflict_flags)
        await db.flush()
    for conflict in conflict_rows:
        await chain_log(
            db,
            namespace=namespace,
            agent_id=req.agent_id,
            op="conflict_detected",
            memory_id=dmem.id,
            content_hash=dmem.content_hash,
            payload={
                "memory_a_id": str(conflict.id),
                "memory_b_id": str(dmem.id),
                "confidence": supersession.confidence,
                "relation": supersession.relation,
                "derived": True,
            },
        )

    await remove_live_facts(db, [row.id for row in superseded_rows])
    if not arrived_closed:
        await upsert_live_fact(db, dmem, compute_predicate_key(meta))

    await chain_log(
        db, namespace=namespace, agent_id=req.agent_id,
        op="add", memory_id=dmem.id,
        content_hash=dmem.content_hash,
        payload={
            "source": req.source,
            "event_time": req.event_time.isoformat(),
            "derived_from": str(parent.id),
            "kind": "interjection",
            "supersession_relation": supersession.relation,
            "supersession_confidence": supersession.confidence,
        },
    )


async def add_memory(
    db: AsyncSession,
    namespace: str,
    req: MemoryAdd,
    *,
    barrier_override: Optional[str] = None,
    precomputed_embedding: Optional[list[float]] = None,
    commit: bool = True,
) -> MemoryOut:
    """``precomputed_embedding`` lets batch writers embed many contents in one
    model call (10-20x faster on local models) and pass each vector through;
    it must come from the same provider/model the store was built with."""
    _add_t0 = _time.perf_counter()
    with tracer.start_as_current_span("memory.add") as span:
        span.set_attribute("namespace", namespace)
        span.set_attribute("agent_id", req.agent_id)
        span.set_attribute("has_subject", bool(req.subject_id))

        # Raw customer identifiers are routing inputs only. Persist a keyed,
        # namespace-scoped reference in every memory/read-model/evidence field.
        raw_subject_id = req.subject_id
        if raw_subject_id:
            persisted_subject_ref = subject_reference(namespace, raw_subject_id)
            if persisted_subject_ref is None:
                raise ValueError("subject_id must not be empty")
            req.subject_id = persisted_subject_ref
            req.metadata = replace_subject_identifier(
                req.metadata or {}, raw_subject_id, persisted_subject_ref
            )
            if req.source == raw_subject_id:
                req.source = persisted_subject_ref

        # Reserve before embedding, extraction, encryption, or supersession.
        # The reservation shares this write's transaction, so any failure before
        # commit releases it automatically. Transactional idempotency callers
        # serialize and filter replays before this function is entered.
        await reserve_namespace_usage(
            db,
            namespace=namespace,
            memory_writes=1,
            estimated_ingest_bytes=estimate_ingest_bytes(req),
        )

        if precomputed_embedding is not None:
            embedding = precomputed_embedding
        else:
            provider = get_embedding_provider()
            embedding = await provider.embed_one(req.content)

        # Auto-metadata (auto-supersession parity): when the caller supplied no
        # structured keys, derive them from the content so the deterministic
        # keyed-supersession fast path can fire on a plain-text write. Opt-in
        # (auto_metadata_enabled); caller keys are never overridden; provenance
        # is tagged under metadata._auto_meta. Fail-open — never blocks the write.
        settings = get_settings()
        if settings.auto_metadata_enabled:
            try:
                from .adapters import get_adapter
                from .auto_metadata import enrich_metadata
                enriched_meta, auto_prov = await enrich_metadata(
                    req.content, req.metadata or {}, adapter=get_adapter(), settings=settings,
                )
                if auto_prov is not None:
                    req.metadata = enriched_meta
                    span.set_attribute("auto_metadata_keys", ",".join(auto_prov["keys"]))
            except Exception:
                record_best_effort_failure("auto_metadata")
                logger.warning(
                    "Auto-metadata enrichment failed; caller metadata retained"
                )

        # Interjection extraction (see interjection.py): durable-fact clauses
        # buried in a conversational turn become derived memories beside the
        # raw turn. Extraction + embedding happen before the write lock; the
        # derived rows are ingested inside it. Fail-open, like auto-metadata.
        derived_clauses: list[tuple[str, list[float]]] = []
        if settings.interjection_extraction_enabled and not (req.metadata or {}).get("_derived"):
            try:
                from .interjection import extract_interjections
                clauses = extract_interjections(req.content)
                if clauses:
                    vectors = await get_embedding_provider().embed(clauses)
                    derived_clauses = list(zip(clauses, vectors))
            except Exception:
                logger.warning("interjection extraction failed; storing raw turn only")

        # Change 6: DEK resolved through cache
        subject_key: Optional[bytes] = None
        if req.subject_id:
            subject_key = await _resolve_subject_key(
                db,
                req.subject_id,
                namespace,
                legacy_subject_id=raw_subject_id,
            )

        stored_bytes = (
            encrypt_content(req.content, subject_key) if subject_key else req.content.encode()
        )

        predicate_key = compute_predicate_key(req.metadata or {})

        in_process_lock = await _get_in_process_lock(namespace, req.agent_id)
        async with in_process_lock:
            await _acquire_pg_advisory_lock(db, namespace, req.agent_id)

            barrier_group = await _get_barrier_group(db, namespace, req.agent_id, override=barrier_override)

            # Change 3: pass a pre-generated UUID so the async LLM worker can
            # reference the new memory before flush assigns the DB id.
            import uuid as _uuid
            new_id = _uuid.uuid4()

            supersession = await run_supersession(
                db=db,
                namespace=namespace,
                agent_id=req.agent_id,
                new_content=req.content,
                new_meta=req.metadata or {},
                new_embedding=embedding,
                new_event_time=req.event_time,
                subject_key=subject_key,
                new_memory_id=new_id,
                barrier_group=barrier_group,
            )
            superseded_rows, conflict_rows, successor = await _lock_supersession_rows(
                db,
                namespace=namespace,
                agent_id=req.agent_id,
                barrier_group=barrier_group,
                new_memory_id=new_id,
                new_event_time=req.event_time,
                superseded_ids=list(supersession.superseded_ids),
                conflict_ids=list(supersession.conflict_ids),
                superseded_by_id=supersession.superseded_by_id,
            )

            now = datetime.now(timezone.utc)
            mem = Memory(
                id=new_id,
                namespace=namespace,
                agent_id=req.agent_id,
                content_encrypted=stored_bytes,
                subject_id=req.subject_id,
                embedding=embedding,
                metadata_=req.metadata,
                event_time=req.event_time,
                ingestion_time=now,
                system_valid_from=now,
                valid_from=req.event_time,
                valid_to=None,
                importance=_compute_importance(req.event_time, req.importance),
                source=req.source,
                content_hash=_content_hash(req.content),
                barrier_group=barrier_group,
            )
            db.add(mem)
            await db.flush()

            for old in superseded_rows:
                old.close_validity(valid_to=req.event_time, recorded_at=now)
                old.superseded_by = mem.id
                old.supersession_confidence = supersession.confidence
            if superseded_rows:
                await db.flush()
            for old in superseded_rows:
                await chain_log(
                    db, namespace=namespace, agent_id=req.agent_id,
                    op="supersede", memory_id=old.id,
                    content_hash=old.content_hash,
                    payload={
                        "superseded_by": str(mem.id),
                        "confidence": supersession.confidence,
                        "relation": supersession.relation,
                        "rationale": supersession.rationale,
                        "adjudication_stage": 3 if supersession.rationale else 2,
                    },
                )
            await _mark_parents_stale(
                db,
                namespace,
                req.agent_id,
                barrier_group,
                superseded_rows,
                req.event_time,
            )

            # Out-of-order ingestion: a live fact with a LATER event_time already
            # covers this key/topic, so the incoming memory arrives historical —
            # its validity window closes at the successor's event_time. It stays
            # queryable via as_of/snapshot for its own era but never pollutes the
            # current view.
            arrived_closed = False
            if successor is not None:
                mem.close_validity(valid_to=successor.event_time, recorded_at=now)
                mem.superseded_by = successor.id
                mem.supersession_confidence = supersession.confidence
                arrived_closed = True
                await chain_log(
                    db, namespace=namespace, agent_id=req.agent_id,
                    op="supersede", memory_id=mem.id,
                    content_hash=mem.content_hash,
                    payload={
                        "superseded_by": str(successor.id),
                        "confidence": supersession.confidence,
                        "relation": supersession.relation,
                        "backdated_arrival": True,
                    },
                )

            # Same-time contradiction: persist a ConflictFlag for human review.
            # Both memories stay live (neither superseded) until someone resolves it.
            conflict_flags = [
                ConflictFlag(
                    namespace=namespace,
                    agent_id=req.agent_id,
                    memory_a_id=conflict.id,       # pre-existing memory
                    memory_b_id=mem.id,            # newly ingested memory
                    confidence=supersession.confidence,
                    status="open",
                )
                for conflict in conflict_rows
            ]
            if conflict_flags:
                db.add_all(conflict_flags)
                await db.flush()
            for conflict in conflict_rows:
                await chain_log(
                    db, namespace=namespace, agent_id=req.agent_id,
                    op="conflict_detected", memory_id=mem.id,
                    content_hash=mem.content_hash,
                    payload={
                        "memory_a_id": str(conflict.id),
                        "memory_b_id": str(mem.id),
                        "confidence": supersession.confidence,
                        "relation": supersession.relation,
                    },
                )

            # Change 1: maintain live_facts projection. A memory that arrived
            # already superseded (backdated) is never live.
            await remove_live_facts(db, [row.id for row in superseded_rows])
            if not arrived_closed:
                await upsert_live_fact(db, mem, predicate_key)

            await chain_log(
                db, namespace=namespace, agent_id=req.agent_id,
                op="add", memory_id=mem.id,
                content_hash=mem.content_hash,
                payload={
                    "source": req.source,
                    "event_time": req.event_time.isoformat(),
                    "metadata": req.metadata,
                    "supersession_relation": supersession.relation,
                    "supersession_confidence": supersession.confidence,
                },
            )

            # Ingest extracted interjection clauses as derived memories. Runs
            # inside the same lock/transaction as the parent; each clause goes
            # through the full supersession funnel so a cued revision clause
            # closes its predecessor clause. Fail-open per clause.
            for clause_text, clause_vec in derived_clauses:
                try:
                    await _ingest_derived_clause(
                        db, namespace, req, mem, clause_text, clause_vec, subject_key,
                    )
                except SupersessionDecisionUnavailable:
                    raise
                except Exception:
                    logger.warning("derived-clause ingest failed; raw turn unaffected")

            # Fan out webhook events for the write outcome. dispatch_event is a
            # no-op when no endpoint subscribes, so this is safe on every write.
            from .webhook_service import MEMORY_CONFLICT, MEMORY_SUPERSEDED, dispatch_event
            if supersession.superseded_ids:
                await dispatch_event(db, namespace, MEMORY_SUPERSEDED, {
                    "agent_id": req.agent_id,
                    "new_memory_id": str(mem.id),
                    "superseded_ids": [str(i) for i in supersession.superseded_ids],
                    "relation": supersession.relation,
                    "confidence": supersession.confidence,
                }, barrier_group=barrier_group)
            if supersession.conflict_ids:
                await dispatch_event(db, namespace, MEMORY_CONFLICT, {
                    "agent_id": req.agent_id,
                    "new_memory_id": str(mem.id),
                    "conflict_ids": [str(i) for i in supersession.conflict_ids],
                    "confidence": supersession.confidence,
                }, barrier_group=barrier_group)

            # The usage fact and authoritative memory mutation share one
            # commit. A provider outage is handled later by the durable worker;
            # no billable write can disappear between two transactions.
            from .metering import enqueue_usage_event

            await enqueue_usage_event(
                db,
                namespace=namespace,
                event_name=get_settings().stripe_meter_write_event,
                quantity=1,
                source_identifier=f"w:{mem.id}",
                occurred_at=mem.ingestion_time,
            )

            # Fence every older Redis/process-local generation while the
            # exclusive advisory lock is still held.  If Redis is unavailable,
            # invalidate_agent raises and this mutation is not committed.
            await _fence_recall_caches_before_commit(db, namespace, req.agent_id)
            if commit:
                await db.commit()
            else:
                await db.flush()

        await db.refresh(mem)

        span.set_attribute("memory_id", str(mem.id))
        span.set_attribute("supersession_relation", supersession.relation)
        span.set_attribute("predicate_key", predicate_key or "")

        record_write(namespace, supersession.relation)
        observe_add(namespace, _time.perf_counter() - _add_t0)

        response = _memory_to_out(mem, req.content)
        # Echo the caller's transient identifier only on this direct response;
        # the persisted row and all later reads expose the keyed reference.
        if raw_subject_id:
            response.subject_id = raw_subject_id
        return response


async def replay_memory_result(
    db: AsyncSession,
    namespace: str,
    memory_id: UUID,
    *,
    raw_subject_id: str | None = None,
    barrier_override: Optional[str] = None,
) -> MemoryOut:
    """Rehydrate a completed memory write without persisting response content."""
    mem = await db.get(Memory, memory_id)
    if (
        mem is None
        or mem.namespace != namespace
        or not _barrier_visible(mem, barrier_override)
    ):
        raise IdempotencyReplayUnavailable(
            "The committed idempotency result is not visible to this caller"
        )
    subject_keys = await _keys_for_rows(db, namespace, [mem])
    from .ranking import _decrypt

    response = _memory_to_out(mem, _decrypt(mem, subject_keys))
    if raw_subject_id:
        response.subject_id = raw_subject_id
    return response


def _estimate_tokens(text: str) -> int:
    """Cheap token estimate (~4 chars/token) — good enough for budgeting."""
    return max(1, len(text) // 4)


# Neighbors farther apart than this are different episodes, not context.
CONTEXT_GAP_S = 3600.0


async def _attach_context(
    db: AsyncSession,
    namespace: str,
    agent_id: str,
    memories_out: list[MemoryOut],
    subject_keys: dict[str, bytes],
    *,
    as_of: Optional[datetime] = None,
    barrier_override: Optional[str] = None,
) -> None:
    """Populate ``context_before``/``context_after`` on recall hits.

    For each hit, the nearest same-agent memory strictly before/after it in
    event time (within CONTEXT_GAP_S) — the other half of a dialogue exchange
    or event burst. Two indexed LIMIT-1 queries per hit on
    ix_memories_ns_agent_event; erased rows never surface, and under as_of
    only rows already knowable at the pinned time qualify.
    """
    from .ranking import _decrypt

    gap = timedelta(seconds=CONTEXT_GAP_S)
    context_rows: list[tuple[MemoryOut, Memory | None, Memory | None]] = []
    for out in memories_out:
        base = [
            Memory.namespace == namespace,
            Memory.agent_id == agent_id,
            Memory.erased_at.is_(None),
            Memory.id != out.id,
        ]
        if as_of is not None:
            base.append(Memory.event_time <= as_of)
        barrier_condition = _barrier_filter(Memory.barrier_group, barrier_override)
        if barrier_condition is not None:
            base.append(barrier_condition)
        before_stmt = (
            select(Memory)
            .where(and_(*base, Memory.event_time < out.event_time,
                        Memory.event_time >= out.event_time - gap))
            .order_by(Memory.event_time.desc())
            .limit(1)
        )
        after_stmt = (
            select(Memory)
            .where(and_(*base, Memory.event_time > out.event_time,
                        Memory.event_time <= out.event_time + gap))
            .order_by(Memory.event_time.asc())
            .limit(1)
        )
        before = (await db.execute(before_stmt)).scalars().first()
        after = (await db.execute(after_stmt)).scalars().first()
        context_rows.append((out, before, after))
    neighbor_keys = await _keys_for_rows(
        db,
        namespace,
        [
            row
            for _, before, after in context_rows
            for row in (before, after)
            if row is not None
        ],
    )
    subject_keys = {**subject_keys, **neighbor_keys}
    for out, before, after in context_rows:
        if before is not None:
            out.context_before = _decrypt(before, subject_keys)
        if after is not None:
            out.context_after = _decrypt(after, subject_keys)


async def _visible_conflict_rows(
    db: AsyncSession,
    namespace: str,
    *,
    status: Optional[str],
    limit: int,
    barrier_override: Optional[str],
    agent_id: Optional[str] = None,
    oldest_first: bool = False,
    after_detected_at: Optional[datetime] = None,
    after_id: Optional[UUID] = None,
) -> tuple[int, list[tuple[ConflictFlag, Memory, Memory]]]:
    """Load a bounded conflict page and both memories in one tenant-safe query."""
    memory_a = aliased(Memory)
    memory_b = aliased(Memory)
    filters: list[Any] = [
        ConflictFlag.namespace == namespace,
        memory_a.namespace == namespace,
        memory_b.namespace == namespace,
    ]
    if status:
        filters.append(ConflictFlag.status == status)
    if agent_id:
        filters.append(ConflictFlag.agent_id == agent_id)
    if barrier_override is not None:
        filters.extend(
            (
                or_(memory_a.barrier_group.is_(None), memory_a.barrier_group == barrier_override),
                or_(memory_b.barrier_group.is_(None), memory_b.barrier_group == barrier_override),
            )
        )
    page_filters = list(filters)
    if after_detected_at is not None and after_id is not None:
        timestamp_comparison = (
            ConflictFlag.detected_at > after_detected_at
            if oldest_first
            else ConflictFlag.detected_at < after_detected_at
        )
        id_comparison = (
            ConflictFlag.id > after_id if oldest_first else ConflictFlag.id < after_id
        )
        page_filters.append(
            or_(
                timestamp_comparison,
                and_(
                    ConflictFlag.detected_at == after_detected_at,
                    id_comparison,
                ),
            )
        )
    joins = (
        (memory_a, memory_a.id == ConflictFlag.memory_a_id),
        (memory_b, memory_b.id == ConflictFlag.memory_b_id),
    )
    count_stmt = select(func.count(ConflictFlag.id)).select_from(ConflictFlag)
    page_stmt = select(ConflictFlag, memory_a, memory_b)
    for target, condition in joins:
        count_stmt = count_stmt.join(target, condition)
        page_stmt = page_stmt.join(target, condition)
    total = int((await db.execute(count_stmt.where(*filters))).scalar_one() or 0)
    ordering = (
        (ConflictFlag.detected_at.asc(), ConflictFlag.id.asc())
        if oldest_first
        else (ConflictFlag.detected_at.desc(), ConflictFlag.id.desc())
    )
    rows = list(
        (
            await db.execute(
                page_stmt.where(*page_filters).order_by(*ordering).limit(limit)
            )
        ).all()
    )
    return total, rows


async def _agent_open_conflicts(
    db: AsyncSession,
    namespace: str,
    agent_id: str,
    limit: int,
    barrier_override: Optional[str] = None,
) -> tuple[list[ConflictFlagOut], int]:
    """
    Open conflicts for one agent, oldest first (the longest-unresolved conflict
    is the most overdue), plus the total open count. Backs the active-resurfacing
    section of ``assemble_context``.
    """
    total, rows = await _visible_conflict_rows(
        db,
        namespace,
        status="open",
        limit=limit,
        barrier_override=barrier_override,
        agent_id=agent_id,
        oldest_first=True,
    )
    if total == 0:
        return [], 0
    memory_rows = [memory for _, mem_a, mem_b in rows for memory in (mem_a, mem_b)]
    subject_keys = await _keys_for_rows(db, namespace, memory_rows)
    from .ranking import _decrypt

    out: list[ConflictFlagOut] = []
    for flag, mem_a, mem_b in rows:
        out.append(ConflictFlagOut(
            id=flag.id,
            namespace=flag.namespace,
            agent_id=flag.agent_id,
            memory_a_id=flag.memory_a_id,
            memory_b_id=flag.memory_b_id,
            memory_a_content=_decrypt(mem_a, subject_keys) if mem_a else None,
            memory_b_content=_decrypt(mem_b, subject_keys) if mem_b else None,
            memory_a_source=mem_a.source if mem_a else None,
            memory_b_source=mem_b.source if mem_b else None,
            memory_a_event_time=mem_a.event_time if mem_a else flag.detected_at,
            memory_b_event_time=mem_b.event_time if mem_b else flag.detected_at,
            confidence=flag.confidence,
            detected_at=flag.detected_at,
            status=flag.status,
            resolved_at=flag.resolved_at,
            resolver_note=flag.resolver_note,
        ))
    return out, int(total)


async def assemble_context(
    db: AsyncSession,
    namespace: str,
    req: "ContextRequest",
    *,
    barrier_override: Optional[str] = None,
) -> "ContextResult":
    """
    Recall the relevant facts and assemble them into a token-budgeted, ready-to-
    inject context block — the one-call "memory context" surface (Zep parity),
    backed by Lians' bitemporal recall so the block never contains stale facts.

    Facts are included in relevance order until ``max_tokens`` is reached; each
    line carries event-time and source so the model can reason about recency and
    provenance. Erased (crypto-shredded) facts are skipped.

    Active resurfacing: open conflicts for this agent push to the top of the
    block (oldest first — they cannot silently age out) until a human
    adjudicates them, so the model treats contested facts as contested rather
    than confidently using whichever version recall happened to rank higher.
    """
    from .schemas import ContextResult
    filters: dict[str, Any] = dict(req.filters)
    if req.mmr:
        filters["_rerank"] = "mmr"
    recall_req = RecallRequest(
        agent_id=req.agent_id, query=req.query, k=req.k, as_of=req.as_of, filters=filters,
    )
    result = await recall_memories(db, namespace, recall_req, barrier_override=barrier_override)

    lines = [req.header]
    used = _estimate_tokens(req.header)
    truncated = False
    if used > req.max_tokens:
        # A caller-controlled header must not be able to violate the same
        # budget that constrains conflicts and recalled memories.
        lines = [req.header[: req.max_tokens * 4]]
        used = _estimate_tokens(lines[0])
        truncated = True

    def append_if_fits(line: str) -> bool:
        nonlocal used
        candidate = "\n".join((*lines, line))
        candidate_tokens = _estimate_tokens(candidate)
        if candidate_tokens > req.max_tokens:
            return False
        lines.append(line)
        used = candidate_tokens
        return True

    open_conflicts: list[ConflictFlagOut] = []
    open_conflicts_total = 0
    # Conflict flags represent the current adjudication backlog. Until conflict
    # state itself is reconstructed bitemporally, never mix it into an as-of
    # context where it could reveal facts detected after the requested cutoff.
    if req.surface_conflicts and req.max_conflicts > 0 and req.as_of is None:
        open_conflicts, open_conflicts_total = await _agent_open_conflicts(
            db,
            namespace,
            req.agent_id,
            req.max_conflicts,
            barrier_override,
        )
    if open_conflicts:
        surfaced_conflicts: list[ConflictFlagOut] = []
        banner = "⚠ UNRESOLVED MEMORY CONFLICTS — contested facts, pending adjudication:"
        if append_if_fits(banner):
            for c in open_conflicts:
                a_stamp = c.memory_a_event_time.isoformat()[:16].replace("T", " ")
                b_stamp = c.memory_b_event_time.isoformat()[:16].replace("T", " ")
                a_src = f" [{c.memory_a_source}]" if c.memory_a_source else ""
                b_src = f" [{c.memory_b_source}]" if c.memory_b_source else ""
                line = (
                    f"- ({a_stamp}){a_src} \"{c.memory_a_content}\" DISAGREES WITH "
                    f"({b_stamp}){b_src} \"{c.memory_b_content}\""
                )
                if not append_if_fits(line):
                    truncated = True
                    break
                surfaced_conflicts.append(c)
            omitted = open_conflicts_total - len(surfaced_conflicts)
            if omitted > 0:
                more = f"  (+{omitted} more open conflicts not shown)"
                if not append_if_fits(more):
                    truncated = True
        else:
            truncated = True
        # This field is documented as the conflicts actually surfaced in the
        # context block. The total still reports the full open backlog.
        open_conflicts = surfaced_conflicts
    included: list = []
    for m in result.memories:
        if not m.content:
            continue  # erased — content unrecoverable
        stamp = m.event_time.isoformat()[:16].replace("T", " ") if m.event_time else "undated"
        prov = f" [{m.source}]" if m.source else ""
        line = f"- ({stamp}){prov} {m.content}"
        if not append_if_fits(line):
            truncated = True
            break
        included.append(m)

    return ContextResult(
        context="\n".join(lines),
        memories=included,
        token_estimate=used,
        truncated=truncated,
        retrieval_degraded=result.retrieval_degraded,
        graph_search_complete=result.graph_search_complete,
        candidate_window_complete=result.candidate_window_complete,
        candidates_considered=result.candidates_considered,
        candidate_limit=result.candidate_limit,
        open_conflicts=open_conflicts,
        open_conflicts_total=open_conflicts_total,
    )


async def _complete_recall(
    db: AsyncSession,
    namespace: str,
    req: RecallRequest,
    result: RecallResult,
    *,
    router: str,
    cache_hit: bool,
    started_at: float,
) -> RecallResult:
    """Durably audit one successful recall before returning its result.

    Every router, including Redis and keyed fast paths, passes through this
    awaited boundary. The audit row and durable metering outbox row share one
    commit, so a successful response cannot lose its billable usage fact.
    """
    audit_payload: dict[str, Any] = {
        "query_hash": _content_hash(req.query),
        "k": req.k,
        "as_of": req.as_of.isoformat() if req.as_of else None,
        "filters": req.filters,
        "result_ids": [str(memory.id) for memory in result.memories],
        "router": router,
        "cache_hit": cache_hit,
        "candidate_window_complete": result.candidate_window_complete,
        "candidates_considered": result.candidates_considered,
        "candidate_limit": result.candidate_limit,
        "candidate_mode": result.candidate_mode,
        "graph_search_complete": result.graph_search_complete,
    }
    if result.retrieval_degraded:
        audit_payload["retrieval_degraded"] = True

    # Reserve the durable namespace recall budget in the same transaction as
    # the audit and billing facts. A rejected quota reservation returns no
    # result and leaves no partial counter, audit, or metering state.
    await reserve_namespace_usage(db, namespace=namespace, recalls=1)

    recall_log = await chain_log(
        db,
        namespace=namespace,
        agent_id=req.agent_id,
        op="recall",
        payload=audit_payload,
    )

    from .metering import enqueue_usage_event

    await enqueue_usage_event(
        db,
        namespace=namespace,
        event_name=get_settings().stripe_meter_recall_event,
        quantity=1,
        source_identifier=f"r:{recall_log.id}",
        occurred_at=recall_log.created_at,
    )
    await db.commit()

    record_recall(namespace, router=router, cache_hit=cache_hit)
    observe_recall(namespace, _time.perf_counter() - started_at)
    return result


async def recall_memories(
    db: AsyncSession,
    namespace: str,
    req: RecallRequest,
    *,
    barrier_override: Optional[str] = None,
) -> RecallResult:
    _recall_t0 = _time.perf_counter()
    with tracer.start_as_current_span("memory.recall") as span:
        span.set_attribute("namespace", namespace)
        span.set_attribute("agent_id", req.agent_id)
        span.set_attribute("k", req.k)
        span.set_attribute("has_as_of", bool(req.as_of))

        settings = get_settings()

        # Graph-proximity reranking (opt-in via filters). Pull the anchor params
        # out of `filters` BEFORE they reach the metadata matcher, and bypass the
        # recall cache when present (results depend on the live graph).
        near_entity: Optional[str] = None
        near_key = "ticker"
        rerank: Optional[str] = None
        mmr_lambda = 0.5
        if req.filters:
            near_entity = req.filters.pop("_near_entity", None)
            near_key = req.filters.pop("_near_key", "ticker")
            rerank = req.filters.pop("_rerank", None)
            try:
                mmr_lambda = float(req.filters.pop("_mmr_lambda", 0.5))
            except (TypeError, ValueError):
                mmr_lambda = 0.5

        # Cache coherence is supported only on PostgreSQL, where recall holds a
        # shared transaction advisory lock through its audit commit and writers
        # take the matching exclusive lock. Other databases always use durable
        # reads rather than an unsafe process-local approximation.
        cache_generation: str | None = None
        if (
            settings.recall_cache_enabled
            and not req.as_of
            and barrier_override is None
            and await acquire_namespace_cache_lock(db, namespace, shared=True)
            and await _acquire_pg_advisory_lock(
                db, namespace, req.agent_id, shared=True
            )
        ):
            cache_generation = await get_agent_cache_generation(namespace, req.agent_id)
        candidate_contract = recall_candidate_contract(req.k)

        # Hot cache (Redis)
        if (
            cache_generation is not None
            and not near_entity
            and not rerank
        ):
            cached = await get_cached_recall(
                namespace,
                req.agent_id,
                req.query,
                req.as_of,
                req.k,
                req.filters,
                generation=cache_generation,
                candidate_contract=candidate_contract,
            )
            if cached is not None:
                span.set_attribute("cache_hit", True)
                result = RecallResult.model_validate_json(cached)
                return await _complete_recall(
                    db,
                    namespace,
                    req,
                    result,
                    router="cache",
                    cache_hit=True,
                    started_at=_recall_t0,
                )
        span.set_attribute("cache_hit", False)

        # Change 2: keyed router — exact lookup if filters resolve to a known predicate
        if not req.as_of and req.filters:
            predicate_key = compute_predicate_key(req.filters)
            if predicate_key:
                with tracer.start_as_current_span("recall.keyed_lookup") as ks:
                    barrier_group = await _get_barrier_group(db, namespace, req.agent_id, override=barrier_override)
                    live_fact = await keyed_lookup(
                        db, namespace, req.agent_id, predicate_key, barrier_group
                    )
                    if live_fact is not None:
                        subject_keys = await _keys_for_rows(db, namespace, [live_fact])
                        from .ranking import _decrypt
                        content = _decrypt(live_fact, subject_keys)
                        # Build a synthetic Memory-like result for the schema
                        mem = await db.get(Memory, live_fact.memory_id)
                        if mem is not None:
                            ks.set_attribute("keyed_hit", True)
                            span.set_attribute("router", "keyed")
                            mem_out = _memory_to_out(mem, content)
                            mem_out.score = 1.0  # exact keyed match
                            result = RecallResult(
                                memories=[mem_out],
                                as_of=None,
                                total_candidates=1,
                                candidate_window_complete=True,
                                candidates_considered=1,
                                candidate_limit=1,
                                candidate_mode="keyed_exact",
                            )
                            return await _complete_recall(
                                db,
                                namespace,
                                req,
                                result,
                                router="keyed",
                                cache_hit=False,
                                started_at=_recall_t0,
                            )

        span.set_attribute("router", "semantic")

        # Change 10: sub-spans for each recall stage
        #
        # Degraded-retrieval mode: an unavailable embedding provider must not
        # take recall down with it. On embed failure the query proceeds
        # lexical-only (BM25 + recency + importance — semantic weight scores 0)
        # and the degradation is carried on the result AND into the audit
        # chain: a decision made under degraded recall is a fact an examiner
        # needs, not something to silently absorb. Keyed lookups above never
        # embed, so they never degrade.
        retrieval_degraded = False
        lexical_reranker_primary = lexical_reranker_primary_enabled()
        with tracer.start_as_current_span("recall.embed") as embed_span:
            if lexical_reranker_primary:
                query_embedding = []
                embed_span.set_attribute("retrieval_mode", "lexical_reranker_primary")
                if not reranker_enabled():
                    retrieval_degraded = True
                    embed_span.set_attribute("retrieval_degraded", True)
                    logger.warning(
                        "lexical-primary recall requested without a configured reranker"
                    )
            else:
                provider = get_embedding_provider()
                try:
                    # Custom providers written against the pre-asymmetric
                    # interface may only implement embed_one; treat that as the
                    # query embedding rather than a degradation event.
                    embed_fn = getattr(provider, "embed_query", None) or provider.embed_one
                    query_embedding = await embed_fn(req.query)
                except Exception as exc:
                    query_embedding = []
                    retrieval_degraded = True
                    embed_span.set_attribute("retrieval_degraded", True)
                    logger.warning(
                        "embedding provider failed (%s) — recall degrading to lexical-only",
                        type(exc).__name__,
                    )
        span.set_attribute("retrieval_degraded", retrieval_degraded)

        barrier_group = await _get_barrier_group(
            db,
            namespace,
            req.agent_id,
            override=barrier_override,
        )
        recall_diagnostics: dict[str, Any] = {}

        with tracer.start_as_current_span("recall.search"):
            results = await hybrid_recall(
                db=db,
                namespace=namespace,
                agent_id=req.agent_id,
                query=req.query,
                query_embedding=query_embedding,
                k=req.k,
                as_of=req.as_of,
                filters=req.filters,
                subject_keys=None,
                barrier_group=barrier_group,
                live_facts_override=None,
                diagnostics=recall_diagnostics,
            )

        if lexical_reranker_primary:
            recall_diagnostics["candidate_mode"] = "bounded_lexical_reranker_primary"
        if recall_diagnostics.get("reranker_complete") is False:
            retrieval_degraded = True
            span.set_attribute("retrieval_degraded", True)

        span.set_attribute("result_count", len(results))

        # MMR reranking (opt-in via filters {"_rerank": "mmr"}): reorder the
        # candidate set to balance relevance against diversity, so the top-k isn't
        # dominated by near-duplicate restatements of the same fact.
        if rerank == "mmr" and len(results) > 1:
            from .ranking import mmr_rerank
            results = mmr_rerank(results, lambda_=mmr_lambda)
            span.set_attribute("mmr_rerank", True)

        # Graph-proximity reranking: boost results whose entity sits near the
        # anchor entity in the relationship graph (Graphiti-style node-distance).
        graph_search_complete = True
        if near_entity and results:
            results, graph_search_complete = await _rerank_by_proximity(
                db, namespace, req.agent_id, near_entity, near_key, results, req.as_of,
                barrier_override=barrier_override,
            )
            span.set_attribute("graph_rerank", True)
            span.set_attribute("graph_search_complete", graph_search_complete)
            retrieval_degraded = retrieval_degraded or not graph_search_complete
            span.set_attribute("retrieval_degraded", retrieval_degraded)

        # hybrid_recall always returns Memory objects (Change 1 fetch-back ensures this)
        with tracer.start_as_current_span("recall.assemble"):
            memories_out: list[MemoryOut] = []
            for mem, _score, content in results:
                mem_out = _memory_to_out(mem, content)
                mem_out.score = _score
                memories_out.append(mem_out)

        if req.include_context and memories_out:
            with tracer.start_as_current_span("recall.context"):
                await _attach_context(
                    db,
                    namespace,
                    req.agent_id,
                    memories_out,
                    await _keys_for_rows(db, namespace, [item[0] for item in results]),
                    as_of=req.as_of,
                    barrier_override=barrier_override,
                )

        result = RecallResult(
            memories=memories_out,
            as_of=req.as_of,
            total_candidates=len(results),
            retrieval_degraded=retrieval_degraded,
            graph_search_complete=graph_search_complete,
            candidate_window_complete=bool(
                recall_diagnostics.get("candidate_window_complete", True)
            ),
            candidates_considered=int(
                recall_diagnostics.get("candidates_considered", len(results))
            ),
            candidate_limit=int(recall_diagnostics.get("candidate_limit", len(results))),
            candidate_mode=str(recall_diagnostics.get("candidate_mode", "exact")),
            token_estimate=sum(
                _estimate_tokens(m.content) for m in memories_out if m.content),
        )

        router = "semantic_degraded" if retrieval_degraded else "semantic"
        result = await _complete_recall(
            db,
            namespace,
            req,
            result,
            router=router,
            cache_hit=False,
            started_at=_recall_t0,
        )

        # Never cache a degraded result — it would keep serving lexical-only
        # recall after the embedding provider recovers.
        if (
            cache_generation is not None and not near_entity
            and barrier_override is None and not retrieval_degraded
            and result.candidate_mode in {"ann", "historical_ann"}
        ):
            await set_cached_recall(
                namespace, req.agent_id, req.query, req.as_of, req.k, req.filters,
                result.model_dump_json(),
                settings.recall_cache_ttl_seconds,
                generation=cache_generation,
                candidate_contract=candidate_contract,
            )

        return result


async def _rerank_by_proximity(
    db: AsyncSession,
    namespace: str,
    agent_id: str,
    anchor: str,
    near_key: str,
    results: list,
    as_of: Optional[datetime],
    barrier_override: Optional[str] = None,
) -> tuple[list, bool]:
    """
    Reorder recall results by graph proximity to ``anchor``.

    Each result's entity is read from metadata[``near_key``]; its hop-distance to
    the anchor in the relationship graph yields an additive proximity bonus
    (1/(1+distance)), so closely-connected facts rise without displacing strong
    semantic matches. Unreachable entities get no bonus. A budget-capped search
    also gives unknown distances no bonus but reports ``search_complete=False``.
    """
    from .graph_service import canon_entity, entity_distances

    candidates: set[str] = set()
    for mem, _score, _content in results:
        val = (mem.metadata_ or {}).get(near_key)
        if val:
            candidates.add(str(val))
    if not candidates:
        return results, True

    distances, search_complete = await entity_distances(
        db, namespace, agent_id, anchor, candidates, as_of=as_of,
        barrier_override=barrier_override,
    )

    def _key(item):
        mem, score, _content = item
        val = (mem.metadata_ or {}).get(near_key)
        dist = distances.get(canon_entity(str(val))) if val else None
        bonus = 1.0 / (1.0 + dist) if dist is not None else 0.0
        return score + bonus

    return sorted(results, key=_key, reverse=True), search_complete


async def batch_add_memories(
    db: AsyncSession,
    namespace: str,
    reqs: list[MemoryAdd],
    barrier_override: Optional[str] = None,
    *,
    commit: bool = True,
) -> MemoryBatchResult:
    """Atomically add memories; later items may supersede earlier items.

    PostgreSQL transaction advisory locks survive each nested ``add_memory``
    call until the batch commits. Subject locks precede agent locks, matching
    single writes and erasure; both sets use a canonical order.
    """
    # Match the single-write/erasure lock order: subject before agent. Both
    # collections are canonicalized so overlapping batches cannot deadlock.
    subject_refs = sorted(
        {
            persisted
            for req in reqs
            if req.subject_id is not None
            and (persisted := subject_reference(namespace, req.subject_id)) is not None
        }
    )
    for persisted_subject_ref in subject_refs:
        await lock_subject_key_for_update(db, persisted_subject_ref, namespace)
    for agent_id in sorted({req.agent_id for req in reqs}):
        await _acquire_pg_advisory_lock(db, namespace, agent_id)
    out: list[MemoryOut] = []
    for req in reqs:
        out.append(
            await add_memory(
                db,
                namespace,
                req,
                barrier_override=barrier_override,
                commit=False,
            )
        )
    if commit:
        await db.commit()
    return MemoryBatchResult(added=len(out), memories=out)


async def get_pending_supersessions(
    db: AsyncSession,
    namespace: str,
    confidence_threshold: Optional[float] = None,
    limit: int = 50,
    barrier_override: Optional[str] = None,
    before_chain_position: Optional[int] = None,
) -> SupersessionReviewResult:
    settings = get_settings()
    threshold = confidence_threshold if confidence_threshold is not None else settings.supersession_review_threshold

    resolution = aliased(EventLog)
    confidence = func.coalesce(EventLog.payload["confidence"].as_float(), 1.0)
    base_conditions: list[Any] = [
        EventLog.namespace == namespace,
        EventLog.op == "supersede",
        confidence < threshold,
        ~select(resolution.id)
        .where(
            resolution.namespace == EventLog.namespace,
            resolution.memory_id == EventLog.memory_id,
            resolution.op.in_(("supersession_confirmed", "supersession_rejected")),
            resolution.chain_position > EventLog.chain_position,
        )
        .exists(),
    ]
    page_conditions = list(base_conditions)
    if before_chain_position is not None:
        page_conditions.append(EventLog.chain_position < before_chain_position)

    def _statement(*, conditions: list[Any]):
        statement = select(EventLog)
        if barrier_override is not None:
            statement = statement.join(
                Memory,
                and_(
                    Memory.namespace == EventLog.namespace,
                    Memory.id == EventLog.memory_id,
                ),
            ).where(
                or_(
                    Memory.barrier_group.is_(None),
                    Memory.barrier_group == barrier_override,
                )
            )
        return statement.where(*conditions)

    # Exact total is independent of cursor; the EventLog namespace/chain index
    # and correlated resolution predicate keep work in the authenticated scope.
    count_statement = _statement(conditions=base_conditions).with_only_columns(
        func.count(EventLog.id),
        maintain_column_froms=True,
    )
    total = int((await db.execute(count_statement)).scalar_one())
    rows = list(
        (
            await db.execute(
                _statement(conditions=page_conditions)
                .order_by(EventLog.chain_position.desc())
                .limit(limit + 1)
            )
        ).scalars().all()
    )
    has_more = len(rows) > limit
    page = rows[:limit]

    items: list[SupersessionReviewItem] = []
    for row in page:
        payload = dict(row.payload or {})
        confidence = float(payload.get("confidence", 1.0))
        items.append(SupersessionReviewItem(
            event_id=row.id,
            memory_id=row.memory_id,
            superseded_by=payload.get("superseded_by"),
            confidence=confidence,
            relation=payload.get("relation", "SUPERSEDES"),
            rationale=payload.get("rationale"),
            adjudication_stage=payload.get("adjudication_stage", 2),
            created_at=row.created_at,
            content_hash=row.content_hash,
        ))

    return SupersessionReviewResult(
        items=items,
        total=total,
        returned=len(items),
        complete=before_chain_position is None and not has_more,
        has_more=has_more,
        next_chain_position=(page[-1].chain_position if has_more and page else None),
        confidence_threshold=threshold,
    )


async def apply_supersession_action(
    db: AsyncSession,
    namespace: str,
    memory_id: UUID,
    action: SupersessionAction,
    barrier_override: Optional[str] = None,
) -> SupersessionActionResult:
    mem = await db.get(Memory, memory_id)
    if (
        mem is None
        or mem.namespace != namespace
        or not _barrier_visible(mem, barrier_override)
    ):
        from fastapi import HTTPException
        raise HTTPException(status_code=404, detail="Memory not found")
    if action.action not in ("confirm", "reject"):
        from fastapi import HTTPException
        raise HTTPException(status_code=422, detail="action must be 'confirm' or 'reject'")

    await _acquire_pg_advisory_lock(db, namespace, mem.agent_id)
    mem = (
        await db.execute(
            select(Memory)
            .where(Memory.id == memory_id, Memory.namespace == namespace)
            .execution_options(populate_existing=True)
            .with_for_update()
        )
    ).scalar_one_or_none()
    if mem is None or not _barrier_visible(mem, barrier_override):
        from fastapi import HTTPException
        raise HTTPException(status_code=404, detail="Memory not found")
    if mem.superseded_by != action.expected_superseded_by:
        from fastapi import HTTPException
        raise HTTPException(status_code=409, detail="Supersession version conflict")
    supersede_event = (
        await db.execute(
            select(EventLog.id, EventLog.chain_position)
            .where(
                EventLog.namespace == namespace,
                EventLog.memory_id == memory_id,
                EventLog.op == "supersede",
            )
            .order_by(EventLog.chain_position.desc())
            .limit(1)
        )
    ).one_or_none()
    if supersede_event is None:
        from fastapi import HTTPException
        raise HTTPException(status_code=409, detail="Supersession evidence is missing")
    prior_resolution = (
        await db.execute(
            select(EventLog.id)
            .where(
                EventLog.namespace == namespace,
                EventLog.memory_id == memory_id,
                EventLog.op.in_(("supersession_confirmed", "supersession_rejected")),
                EventLog.chain_position > supersede_event.chain_position,
            )
            .limit(1)
        )
    ).scalar_one_or_none()
    if prior_resolution is not None:
        from fastapi import HTTPException
        raise HTTPException(status_code=409, detail="Supersession is already resolved")
    now = datetime.now(timezone.utc)

    if action.action == "reject":
        mem.reopen_validity()
        mem.superseded_by = None
        mem.supersession_confidence = None
        # Change 1: restore to live_facts when supersession is rejected
        predicate_key = compute_predicate_key(dict(mem.metadata_ or {}))
        await upsert_live_fact(db, mem, predicate_key)
        op = "supersession_rejected"
    else:
        op = "supersession_confirmed"

    await chain_log(
        db, namespace=namespace, agent_id=mem.agent_id,
        op=op, memory_id=mem.id,
        content_hash=mem.content_hash,
        payload={
            "reviewer_note": action.reviewer_note,
            "action": action.action,
            "actioned_at": now.isoformat(),
            "supersede_event_id": str(supersede_event.id),
            "superseded_by": str(action.expected_superseded_by),
        },
    )
    await _fence_recall_caches_before_commit(db, namespace, mem.agent_id)
    await db.commit()
    return SupersessionActionResult(memory_id=memory_id, action=action.action, applied_at=now)


async def get_retention_policy(db: AsyncSession, namespace: str) -> RetentionPolicyOut:
    pol = await db.get(NamespacePolicy, namespace)
    if pol is None:
        return RetentionPolicyOut(
            namespace=namespace,
            content_ttl_days=None,
            audit_retention_days=1825,
            legal_hold=False,
            updated_at=None,
        )
    return RetentionPolicyOut.model_validate(pol)


async def set_retention_policy(
    db: AsyncSession,
    namespace: str,
    data: RetentionPolicyIn,
    actor_id: str = "__admin__",
) -> RetentionPolicyOut:
    # Use the same namespace-policy boundary as governance reservations and
    # administration so creation is serialized even before a row exists.
    if db.get_bind().dialect.name == "postgresql":
        await db.execute(
            text("SELECT pg_advisory_xact_lock(hashtextextended(:key, 0))"),
            {"key": f"lians:namespace-governance:{namespace}"},
        )
    pol = (
        await db.execute(
            select(NamespacePolicy)
            .where(NamespacePolicy.namespace == namespace)
            .with_for_update()
        )
    ).scalar_one_or_none()
    if pol is None:
        if data.expected_updated_at is not None:
            raise MutationVersionConflict("Resource version conflict")
        pol = NamespacePolicy(namespace=namespace)
        db.add(pol)
    else:
        if data.expected_updated_at is None:
            raise MutationVersionConflict("Resource version conflict")
        assert_expected_updated_at(pol.updated_at, data.expected_updated_at)
    pol.content_ttl_days = data.content_ttl_days
    pol.audit_retention_days = data.audit_retention_days
    pol.legal_hold = data.legal_hold
    pol.updated_at = datetime.now(timezone.utc)
    await chain_log(
        db, namespace=namespace, agent_id=actor_id,
        op="admin.retention_set",
        payload={
            "content_ttl_days": data.content_ttl_days,
            "audit_retention_days": data.audit_retention_days,
            "legal_hold": data.legal_hold,
        },
    )
    await db.commit()
    await db.refresh(pol)
    return RetentionPolicyOut.model_validate(pol)


async def prune_expired_content(
    db: AsyncSession,
    namespace: str,
    *,
    batch_limit: int = 500,
) -> RetentionPruneResult:
    if db.get_bind().dialect.name == "postgresql":
        await db.execute(
            text("SELECT pg_advisory_xact_lock(hashtextextended(:key, 0))"),
            {"key": f"lians:namespace-governance:{namespace}"},
        )
    pol = (
        await db.execute(
            select(NamespacePolicy)
            .where(NamespacePolicy.namespace == namespace)
            .with_for_update()
        )
    ).scalar_one_or_none()
    if pol is None or pol.content_ttl_days is None:
        cutoff = datetime.min.replace(tzinfo=timezone.utc)
        return RetentionPruneResult(
            namespace=namespace,
            memories_pruned=0,
            cutoff_date=cutoff,
            remaining=0,
            complete=True,
            batch_limit=batch_limit,
        )

    if pol.legal_hold:
        from fastapi import HTTPException
        raise HTTPException(
            status_code=409,
            detail=f"Namespace '{namespace}' is under legal hold — pruning is blocked.",
        )

    now = datetime.now(timezone.utc)
    cutoff = now - timedelta(days=pol.content_ttl_days)

    prune_conditions = and_(
        Memory.namespace == namespace,
        Memory.ingestion_time < cutoff,
        Memory.content_encrypted.is_not(None),
        Memory.erased_at.is_(None),
    )
    candidates = list(
        (
            await db.execute(
                select(Memory.id, Memory.agent_id)
                .where(prune_conditions)
                .order_by(Memory.ingestion_time.asc(), Memory.id.asc())
                .limit(batch_limit)
            )
        ).all()
    )
    candidate_ids = [row.id for row in candidates]
    pruned_agents = {row.agent_id for row in candidates}
    for aid in sorted(pruned_agents):
        await _acquire_pg_advisory_lock(db, namespace, aid)
    # Re-read after the cooperating agent boundaries are held. This prevents a
    # stale pre-lock snapshot from generating duplicate prune evidence after a
    # concurrent erase or conflict/supersession mutation commits.
    memories = list(
        (
            await db.execute(
                select(Memory)
                .where(prune_conditions, Memory.id.in_(candidate_ids))
                .order_by(Memory.ingestion_time.asc(), Memory.id.asc())
                .with_for_update()
            )
        ).scalars()
    )
    for mem in memories:
        mem.content_encrypted = None
        mem.embedding = None
        mem.erased_at = now
        await chain_log(
            db, namespace=namespace, agent_id=mem.agent_id,
            op="retention_prune", memory_id=mem.id,
            content_hash=mem.content_hash,
            payload={"cutoff_date": cutoff.isoformat(), "content_ttl_days": pol.content_ttl_days},
        )

    # Same tombstone hazard as erase_subject: pruned content must leave the
    # present-time read model and caches, or recall returns empty husks.
    await remove_live_facts(db, [mem.id for mem in memories])
    for aid in sorted(pruned_agents):
        await _fence_recall_caches_before_commit(db, namespace, aid)

    await db.commit()
    remaining = int(
        (
            await db.execute(
                select(func.count(Memory.id)).where(prune_conditions)
            )
        ).scalar_one()
        or 0
    )
    return RetentionPruneResult(
        namespace=namespace,
        memories_pruned=len(memories),
        cutoff_date=cutoff,
        remaining=remaining,
        complete=remaining == 0,
        batch_limit=batch_limit,
    )


async def erase_subject(
    db: AsyncSession,
    namespace: str,
    subject_id: str,
    request_ref: str,
) -> Any:
    """Compatibility entry point for the durable bounded workflow."""

    from .subject_erasure_service import enqueue_subject_erasure

    job, _ = await enqueue_subject_erasure(
        db,
        namespace=namespace,
        subject_id=subject_id,
        request_ref=request_ref,
        principal_ref="lians:principal:v1:local-privacy-controller",
        auth_method="local",
    )
    return job


async def _removed_unbounded_erase_subject_reference_only() -> None:
    """Marker retained so release notes can name the removed implementation."""

    return None


async def get_knowledge_snapshot(
    db: AsyncSession,
    namespace: str,
    agent_id: str,
    as_of: datetime,
    limit: int = 1000,
    barrier_override: Optional[str] = None,
    recorded_as_of: Optional[datetime] = None,
    after_event_time: Optional[datetime] = None,
    after_id: Optional[UUID] = None,
    include_content: bool = True,
) -> list[MemoryOut]:
    """
    Bounded point-in-time knowledge-state page for memories valid at *as_of*.

    Unlike :func:`recall_memories` (vector search → top-k), this applies no
    relevance ranking. It returns a deterministic keyset page of memories whose
    validity window contains ``as_of``
    (``valid_from <= as_of < valid_to``) and whose ``event_time <= as_of``,
    ordered by ``event_time`` ascending. No relevance filter is applied —
    Callers combine :func:`count_knowledge_snapshot` with route completeness and
    cursor fields to distinguish a whole snapshot from one bounded page.

    Content is decrypted where the per-subject key is still live; memories whose
    subject key was crypto-shredded return ``content=None`` (existence and
    metadata preserved, content unrecoverable). This is the read side of the
    GDPR/HIPAA erasure guarantee.

    ``recorded_as_of`` is the independent transaction-time cutoff. When it is
    supplied, facts ingested later are excluded and business-time closures
    learned later are ignored. Omitting it preserves the legacy event-time-only
    behavior.
    """
    business_validity = or_(Memory.valid_to.is_(None), Memory.valid_to > as_of)
    if recorded_as_of is not None:
        # A later correction may backdate valid_to. Before the transaction-time
        # closure was recorded, that business interval was still believed open.
        business_validity = or_(
            business_validity,
            Memory.system_valid_to > recorded_as_of,
        )

    stmt = (
        select(Memory)
        .where(
            and_(
                Memory.namespace == namespace,
                Memory.agent_id == agent_id,
                Memory.valid_from <= as_of,
                business_validity,
                Memory.event_time <= as_of,
                # No erased_at filter: crypto-shredded memories appear as
                # tombstones (content=None, existence + hash preserved) — an
                # examiner must see that a fact existed even after erasure.
            )
        )
        .order_by(Memory.event_time.asc(), Memory.id.asc())
        .limit(limit)
    )
    if recorded_as_of is not None:
        stmt = stmt.where(Memory.system_valid_from <= recorded_as_of)

    barrier_condition = _barrier_filter(Memory.barrier_group, barrier_override)
    if barrier_condition is not None:
        stmt = stmt.where(barrier_condition)
    if (after_event_time is None) != (after_id is None):
        raise ValueError("snapshot cursor requires both after_event_time and after_id")
    if after_event_time is not None and after_id is not None:
        stmt = stmt.where(
            or_(
                Memory.event_time > after_event_time,
                and_(Memory.event_time == after_event_time, Memory.id > after_id),
            )
        )
    result = await db.execute(stmt)
    mems = result.scalars().all()

    if not include_content:
        # Hash-only receipts must not unwrap keys or materialize plaintext that
        # will be discarded by the portable envelope.
        return [_memory_to_out(memory, None) for memory in mems]

    # Decrypt content using the namespace's live subject keys.
    from .ranking import _decrypt

    subject_keys = await _keys_for_rows(db, namespace, mems)
    return [_memory_to_out(m, _decrypt(m, subject_keys)) for m in mems]


async def measure_knowledge_snapshot_bytes(
    db: AsyncSession,
    namespace: str,
    agent_id: str,
    as_of: datetime,
    *,
    include_content: bool,
    barrier_override: Optional[str] = None,
    recorded_as_of: Optional[datetime] = None,
    after_event_time: Optional[datetime] = None,
    after_id: Optional[UUID] = None,
    limit: int | None = None,
) -> tuple[int, int]:
    """Return selected rows plus a conservative serialized-byte upper estimate.

    This aggregate runs before ORM hydration, subject-key loading, or plaintext
    decryption. Stored JSON is multiplied by four; source text and ciphertext
    are multiplied by six to cover worst-case JSON escaping after decryption.
    Fixed fields receive a 2 KiB allowance per row. Without ``limit`` the
    cardinality is the exact full snapshot total; with ``limit`` it measures
    only that deterministic keyset page so later pages remain traversable.
    """

    business_validity = or_(Memory.valid_to.is_(None), Memory.valid_to > as_of)
    if recorded_as_of is not None:
        business_validity = or_(
            business_validity,
            Memory.system_valid_to > recorded_as_of,
        )
    conditions: list[Any] = [
        Memory.namespace == namespace,
        Memory.agent_id == agent_id,
        Memory.valid_from <= as_of,
        business_validity,
        Memory.event_time <= as_of,
    ]
    if recorded_as_of is not None:
        conditions.append(Memory.system_valid_from <= recorded_as_of)
    barrier_condition = _barrier_filter(Memory.barrier_group, barrier_override)
    if barrier_condition is not None:
        conditions.append(barrier_condition)
    if (after_event_time is None) != (after_id is None):
        raise ValueError("snapshot cursor requires both after_event_time and after_id")
    if after_event_time is not None and after_id is not None:
        conditions.append(
            or_(
                Memory.event_time > after_event_time,
                and_(Memory.event_time == after_event_time, Memory.id > after_id),
            )
        )

    row_bytes = (
        literal(2_048)
        + 4 * func.coalesce(func.length(cast(Memory.metadata_, Text)), 0)
        + 6 * func.coalesce(func.length(Memory.source), 0)
    )
    if include_content:
        # Ciphertext is at least as large as plaintext. Six times its byte
        # length covers worst-case JSON escaping of control characters in the
        # decrypted string before the response is serialized.
        row_bytes = row_bytes + 6 * func.coalesce(
            func.length(Memory.content_encrypted), 0
        )
    if limit is None:
        aggregate = select(
            func.count(Memory.id),
            func.coalesce(func.sum(row_bytes), 0),
        ).where(*conditions)
    else:
        bounded = (
            select(row_bytes.label("estimated_bytes"))
            .where(*conditions)
            .order_by(Memory.event_time.asc(), Memory.id.asc())
            .limit(limit)
            .subquery()
        )
        aggregate = select(
            func.count(),
            func.coalesce(func.sum(bounded.c.estimated_bytes), 0),
        ).select_from(bounded)
    count, estimated_bytes = (await db.execute(aggregate)).one()
    return int(count or 0), int(estimated_bytes or 0)


async def count_knowledge_snapshot(
    db: AsyncSession,
    namespace: str,
    agent_id: str,
    as_of: datetime,
    *,
    barrier_override: Optional[str] = None,
    recorded_as_of: Optional[datetime] = None,
) -> int:
    """Return exact visible snapshot cardinality without hydrating memory rows."""
    business_validity = or_(Memory.valid_to.is_(None), Memory.valid_to > as_of)
    if recorded_as_of is not None:
        business_validity = or_(business_validity, Memory.system_valid_to > recorded_as_of)
    conditions: list[Any] = [
        Memory.namespace == namespace,
        Memory.agent_id == agent_id,
        Memory.valid_from <= as_of,
        business_validity,
        Memory.event_time <= as_of,
    ]
    if recorded_as_of is not None:
        conditions.append(Memory.system_valid_from <= recorded_as_of)
    barrier_condition = _barrier_filter(Memory.barrier_group, barrier_override)
    if barrier_condition is not None:
        conditions.append(barrier_condition)
    return int(
        (
            await db.execute(select(func.count(Memory.id)).where(*conditions))
        ).scalar_one()
        or 0
    )


def _lineage_node(mem: Memory, content: Optional[str]) -> LineageNode:
    return LineageNode(
        id=mem.id,
        content=content,
        content_hash=mem.content_hash,
        event_time=mem.event_time,
        ingestion_time=mem.ingestion_time,
        valid_from=mem.valid_from,
        valid_to=mem.valid_to,
        source=mem.source,
        importance=mem.importance,
        supersession_confidence=mem.supersession_confidence,
        erased_at=mem.erased_at,
        metadata=dict(mem.metadata_ or {}),
        # A current graph tip has no successor and its business interval is open.
        is_current=(mem.superseded_by is None and mem.valid_to is None),
    )


_LINEAGE_BIND_BATCH = 400
_LINEAGE_RELATIONS = frozenset(
    {"SUPERSEDES", "REFINES", "CONFIRMS", "ADDS", "CONTRADICTS_SAME_TIME"}
)


def _lineage_graph_statement(
    *,
    namespace: str,
    agent_id: str,
    memory_id: UUID,
    barrier_override: Optional[str],
    limit: int,
):
    """Build a bounded recursive walk of the visible weak component.

    ``UNION`` (rather than ``UNION ALL``) makes the walk terminate even if a
    legacy or out-of-band writer introduced a cycle. The outer statement has no
    sort, allowing the database to stop once the explicit limit is satisfied.
    """

    anchor_conditions: list[Any] = [
        Memory.id == memory_id,
        Memory.namespace == namespace,
        Memory.agent_id == agent_id,
    ]
    anchor_barrier = _barrier_filter(Memory.barrier_group, barrier_override)
    if anchor_barrier is not None:
        anchor_conditions.append(anchor_barrier)
    lineage = (
        select(
            Memory.id.label("id"),
            Memory.superseded_by.label("superseded_by"),
        )
        .where(*anchor_conditions)
        .cte("memory_lineage_graph", recursive=True)
    )

    neighbor = aliased(Memory, name="lineage_neighbor")
    neighbor_conditions: list[Any] = [
        neighbor.namespace == namespace,
        neighbor.agent_id == agent_id,
    ]
    neighbor_barrier = _barrier_filter(neighbor.barrier_group, barrier_override)
    if neighbor_barrier is not None:
        neighbor_conditions.append(neighbor_barrier)
    lineage = lineage.union(
        select(
            neighbor.id.label("id"),
            neighbor.superseded_by.label("superseded_by"),
        )
        .join(
            lineage,
            or_(
                neighbor.id == lineage.c.superseded_by,
                neighbor.superseded_by == lineage.c.id,
            ),
        )
        .where(*neighbor_conditions)
    )
    return select(lineage.c.id, lineage.c.superseded_by).limit(limit)


def _lineage_memory_bytes_expression():
    """Conservative response-size estimate evaluated before ORM hydration."""

    return (
        literal(2_048)
        + func.coalesce(func.length(Memory.content_encrypted), 0)
        + 4 * func.coalesce(func.length(cast(Memory.metadata_, Text)), 0)
        + 4 * func.coalesce(func.length(Memory.source), 0)
    )


def _ranked_lineage_events(namespace: str, memory_ids: list[UUID]):
    """Latest immutable supersession event for each bounded source-node set."""

    return (
        select(
            EventLog.id.label("id"),
            EventLog.memory_id.label("memory_id"),
            EventLog.payload.label("payload"),
            EventLog.created_at.label("created_at"),
            EventLog.chain_position.label("chain_position"),
            func.row_number()
            .over(
                partition_by=EventLog.memory_id,
                order_by=(EventLog.chain_position.desc(), EventLog.id.desc()),
            )
            .label("lineage_rank"),
        )
        .where(
            EventLog.namespace == namespace,
            EventLog.op == "supersede",
            EventLog.memory_id.in_(memory_ids),
        )
        .subquery("ranked_lineage_events")
    )


def _lineage_sort_key(mem: Memory) -> tuple[str, str]:
    return mem.event_time.isoformat(), str(mem.id)


def _topologically_order_lineage(
    memories: dict[UUID, Memory],
) -> tuple[list[Memory], list[UUID], list[UUID], int]:
    """Order a bounded induced supersession DAG and reject persisted cycles."""

    indegree = {memory_id: 0 for memory_id in memories}
    outgoing: dict[UUID, UUID] = {}
    for memory_id, mem in memories.items():
        successor = mem.superseded_by
        if successor in memories:
            outgoing[memory_id] = successor
            indegree[successor] += 1

    root_ids = sorted(
        (memory_id for memory_id, degree in indegree.items() if degree == 0),
        key=lambda value: _lineage_sort_key(memories[value]),
    )
    queue = [
        (*_lineage_sort_key(memories[memory_id]), memory_id)
        for memory_id in root_ids
    ]
    heapq.heapify(queue)
    ordered_ids: list[UUID] = []
    while queue:
        _event_time, _stable_id, memory_id = heapq.heappop(queue)
        ordered_ids.append(memory_id)
        successor = outgoing.get(memory_id)
        if successor is None:
            continue
        indegree[successor] -= 1
        if indegree[successor] == 0:
            heapq.heappush(
                queue,
                (*_lineage_sort_key(memories[successor]), successor),
            )

    if len(ordered_ids) != len(memories):
        raise ValueError("memory lineage contains a supersession cycle")
    tip_ids = sorted(
        (
            memory_id
            for memory_id, mem in memories.items()
            if mem.superseded_by not in memories
        ),
        key=lambda value: _lineage_sort_key(memories[value]),
    )
    return (
        [memories[memory_id] for memory_id in ordered_ids],
        root_ids,
        tip_ids,
        len(outgoing),
    )


def _lineage_edge(
    older: Memory,
    newer: Memory,
    event: Any | None,
) -> LineageEdge:
    """Bind one structural pointer to its latest immutable audit evidence."""

    payload: dict[str, Any] = {}
    binding_status = "missing"
    if event is not None:
        if isinstance(event.payload, dict):
            payload = dict(event.payload)
            binding_status = (
                "bound"
                if str(payload.get("superseded_by", "")) == str(newer.id)
                else "target_mismatch"
            )
        else:
            binding_status = "malformed"

    fallback_confidence = older.supersession_confidence
    if (
        fallback_confidence is None
        or not math.isfinite(float(fallback_confidence))
        or not 0.0 <= float(fallback_confidence) <= 1.0
    ):
        fallback_confidence = 1.0

    relation_value = payload.get("relation")
    relation = (
        relation_value
        if isinstance(relation_value, str) and relation_value in _LINEAGE_RELATIONS
        else "SUPERSEDES"
    )
    confidence_value = payload.get("confidence")
    try:
        confidence = float(confidence_value)
    except (TypeError, ValueError):
        confidence = float(fallback_confidence)
        if binding_status == "bound":
            binding_status = "malformed"
    if not math.isfinite(confidence) or not 0.0 <= confidence <= 1.0:
        confidence = float(fallback_confidence)
        if binding_status == "bound":
            binding_status = "malformed"

    stage_value = payload.get("adjudication_stage", 2)
    if isinstance(stage_value, bool):
        stage = 2
        if binding_status == "bound":
            binding_status = "malformed"
    else:
        try:
            stage = int(stage_value)
        except (TypeError, ValueError):
            stage = 2
            if binding_status == "bound":
                binding_status = "malformed"
    if stage not in {1, 2, 3}:
        stage = 2
        if binding_status == "bound":
            binding_status = "malformed"

    rationale_value = payload.get("rationale")
    rationale = rationale_value if isinstance(rationale_value, str) else None
    if rationale_value is not None and rationale is None and binding_status == "bound":
        binding_status = "malformed"
    if relation_value is not None and relation == "SUPERSEDES" and (
        relation_value != "SUPERSEDES"
    ):
        if binding_status == "bound":
            binding_status = "malformed"

    return LineageEdge(
        from_id=older.id,
        to_id=newer.id,
        relation=relation,
        confidence=confidence,
        rationale=rationale,
        adjudication_stage=stage,
        superseded_at=(
            event.created_at
            if event is not None and binding_status == "bound"
            else older.valid_to or older.ingestion_time
        ),
        audit_event_id=event.id if event is not None else None,
        audit_chain_position=event.chain_position if event is not None else None,
        audit_binding_status=binding_status,
    )


async def get_memory_lineage(
    db: AsyncSession,
    namespace: str,
    memory_id: UUID,
    barrier_override: Optional[str] = None,
    max_nodes: int = 1000,
) -> MemoryLineageResult:
    """
    Return the bounded caller-visible supersession graph containing a memory.

    Supersession can converge when one new fact replaces several earlier facts,
    so the result is a topologically ordered DAG rather than a flattened chain.
    The queried memory may sit anywhere in the graph: the singular root and tip
    fields are compatibility aliases; their plural forms are authoritative.
    Each edge reports whether its latest immutable audit event is structurally
    bound to the returned successor.
    """
    from fastapi import HTTPException

    if not 3 <= max_nodes <= 5_000:
        raise HTTPException(status_code=422, detail="max_nodes must be between 3 and 5000")

    queried_conditions: list[Any] = [
        Memory.id == memory_id,
        Memory.namespace == namespace,
    ]
    queried_barrier = _barrier_filter(Memory.barrier_group, barrier_override)
    if queried_barrier is not None:
        queried_conditions.append(queried_barrier)
    queried = (
        await db.execute(
            select(
                Memory.id,
                Memory.agent_id,
                Memory.superseded_by,
            ).where(*queried_conditions)
        )
    ).one_or_none()
    if queried is None:
        raise HTTPException(status_code=404, detail="Memory not found")

    graph_statement = _lineage_graph_statement(
        namespace=namespace,
        agent_id=queried.agent_id,
        memory_id=memory_id,
        barrier_override=barrier_override,
        limit=max_nodes + 1,
    )
    candidate_rows = list((await db.execute(graph_statement)).all())
    candidate_links = [(row.id, row.superseded_by) for row in candidate_rows]
    if memory_id not in {row_id for row_id, _ in candidate_links}:
        # Recursive CTEs emit the anchor first on supported databases, but keep
        # the security boundary explicit if a future backend does not.
        candidate_links.insert(0, (memory_id, queried.superseded_by))
    has_more = len(candidate_links) > max_nodes
    selected_links = candidate_links[:max_nodes]
    pointer_snapshot = dict(selected_links)
    selected_ids = list(pointer_snapshot)

    memory_count = 0
    memory_estimated_bytes = 0
    memory_row_bytes = _lineage_memory_bytes_expression()
    for start in range(0, len(selected_ids), _LINEAGE_BIND_BATCH):
        chunk = selected_ids[start : start + _LINEAGE_BIND_BATCH]
        inventory_conditions: list[Any] = [
            Memory.namespace == namespace,
            Memory.agent_id == queried.agent_id,
            Memory.id.in_(chunk),
        ]
        inventory_barrier = _barrier_filter(Memory.barrier_group, barrier_override)
        if inventory_barrier is not None:
            inventory_conditions.append(inventory_barrier)
        count, estimated_bytes = (
            await db.execute(
                select(
                    func.count(Memory.id),
                    func.coalesce(func.sum(memory_row_bytes), 0),
                ).where(*inventory_conditions)
            )
        ).one()
        memory_count += int(count or 0)
        memory_estimated_bytes += int(estimated_bytes or 0)
    if memory_count != len(selected_ids):
        raise HTTPException(
            status_code=409,
            detail={
                "code": "lineage_snapshot_changed",
                "message": "The lineage changed while it was being read; retry the request",
            },
        )

    selected_id_set = set(selected_ids)
    edge_source_ids = [
        source_id
        for source_id, successor_id in selected_links
        if successor_id in selected_id_set
    ]
    event_variable_bytes = 0
    for start in range(0, len(edge_source_ids), _LINEAGE_BIND_BATCH):
        chunk = edge_source_ids[start : start + _LINEAGE_BIND_BATCH]
        ranked = _ranked_lineage_events(namespace, chunk)
        event_variable_bytes += int(
            (
                await db.execute(
                    select(
                        func.coalesce(
                            func.sum(
                                4
                                * func.coalesce(
                                    func.length(cast(ranked.c.payload, Text)),
                                    0,
                                )
                            ),
                            0,
                        )
                    ).where(ranked.c.lineage_rank == 1)
                )
            ).scalar_one()
            or 0
        )
    estimated_bytes = (
        memory_estimated_bytes + event_variable_bytes + 1_024 * len(edge_source_ids)
    )
    byte_limit = get_settings().lineage_response_bytes_limit
    if estimated_bytes > byte_limit:
        raise HTTPException(
            status_code=413,
            detail={
                "code": "lineage_response_byte_capacity_exceeded",
                "message": "The requested lineage graph exceeds the response byte budget",
                "estimated_bytes": estimated_bytes,
                "byte_limit": byte_limit,
                "candidate_nodes": len(selected_ids),
                "candidate_edges": len(edge_source_ids),
                "requested_max_nodes": max_nodes,
            },
        )

    memory_fields = (
        Memory.id,
        Memory.namespace,
        Memory.agent_id,
        Memory.subject_id,
        Memory.content_encrypted,
        Memory.content_hash,
        Memory.event_time,
        Memory.ingestion_time,
        Memory.valid_from,
        Memory.valid_to,
        Memory.superseded_by,
        Memory.supersession_confidence,
        Memory.barrier_group,
        Memory.importance,
        Memory.source,
        Memory.erased_at,
        Memory.metadata_,
    )
    memories: dict[UUID, Memory] = {}
    for start in range(0, len(selected_ids), _LINEAGE_BIND_BATCH):
        chunk = selected_ids[start : start + _LINEAGE_BIND_BATCH]
        hydration_conditions: list[Any] = [
            Memory.namespace == namespace,
            Memory.agent_id == queried.agent_id,
            Memory.id.in_(chunk),
        ]
        hydration_barrier = _barrier_filter(Memory.barrier_group, barrier_override)
        if hydration_barrier is not None:
            hydration_conditions.append(hydration_barrier)
        hydrated = (
            (
                await db.execute(
                    select(Memory)
                    .options(load_only(*memory_fields, raiseload=True))
                    .where(*hydration_conditions)
                )
            )
            .scalars()
            .all()
        )
        memories.update((mem.id, mem) for mem in hydrated)
    if len(memories) != len(selected_ids) or any(
        memories[memory_id].superseded_by != pointer_snapshot[memory_id]
        for memory_id in selected_ids
        if memory_id in memories
    ):
        raise HTTPException(
            status_code=409,
            detail={
                "code": "lineage_snapshot_changed",
                "message": "The lineage changed while it was being read; retry the request",
            },
        )

    try:
        ordered, root_ids, tip_ids, edge_count = _topologically_order_lineage(memories)
    except ValueError as exc:
        raise HTTPException(
            status_code=409,
            detail={
                "code": "lineage_cycle_detected",
                "message": "Stored supersession pointers contain a cycle",
            },
        ) from exc

    event_rows: dict[UUID, Any] = {}
    for start in range(0, len(edge_source_ids), _LINEAGE_BIND_BATCH):
        chunk = edge_source_ids[start : start + _LINEAGE_BIND_BATCH]
        ranked = _ranked_lineage_events(namespace, chunk)
        rows = (
            await db.execute(
                select(
                    ranked.c.id,
                    ranked.c.memory_id,
                    ranked.c.payload,
                    ranked.c.created_at,
                    ranked.c.chain_position,
                ).where(ranked.c.lineage_rank == 1)
            )
        ).all()
        event_rows.update((row.memory_id, row) for row in rows)

    order_index = {mem.id: index for index, mem in enumerate(ordered)}
    edges = [
        _lineage_edge(mem, memories[mem.superseded_by], event_rows.get(mem.id))
        for mem in ordered
        if mem.superseded_by in memories
    ]
    edges.sort(key=lambda edge: (order_index[edge.from_id], order_index[edge.to_id]))

    # If the first walk fit, repeat its pointers-only projection at the end.
    # This catches a concurrent new predecessor that would not modify an
    # already-selected row under READ COMMITTED isolation.
    if not has_more:
        final_rows = list((await db.execute(graph_statement)).all())
        final_links = {(row.id, row.superseded_by) for row in final_rows}
        if final_links != set(candidate_links):
            raise HTTPException(
                status_code=409,
                detail={
                    "code": "lineage_snapshot_changed",
                    "message": "The lineage changed while it was being read; retry the request",
                },
            )

    subject_keys = await _keys_for_rows(db, namespace, ordered)
    from .ranking import _decrypt

    nodes = [_lineage_node(mem, _decrypt(mem, subject_keys)) for mem in ordered]
    root_complete = not has_more
    tip_complete = not has_more and all(
        memories[tip_id].superseded_by is None for tip_id in tip_ids
    )
    complete = root_complete and tip_complete
    is_chain = (
        len(root_ids) == 1
        and len(tip_ids) == 1
        and edge_count == max(0, len(memories) - 1)
    )
    return MemoryLineageResult(
        agent_id=str(queried.agent_id),
        namespace=namespace,
        queried_id=memory_id,
        root_id=root_ids[0],
        tip_id=tip_ids[0],
        root_ids=root_ids,
        tip_ids=tip_ids,
        shape="chain" if is_chain else "dag",
        depth=len(nodes),
        edge_count=len(edges),
        truncated=not complete,
        has_more=has_more,
        complete=complete,
        root_complete=root_complete,
        tip_complete=tip_complete,
        reachable_nodes=len(candidate_links),
        reachable_nodes_is_lower_bound=has_more,
        audit_binding_complete=all(
            edge.audit_binding_status == "bound" for edge in edges
        ),
        max_nodes=max_nodes,
        nodes=nodes,
        edges=edges,
    )


async def get_structured_fact_history(
    db: AsyncSession,
    namespace: str,
    agent_id: str,
    key_values: dict[str, str],
    adapter,
    limit: int = 100,
    barrier_override: Optional[str] = None,
    diagnostics: Optional[dict[str, Any]] = None,
) -> list[MemoryOut]:
    """
    Return every recorded version of a structured fact, ordered by event_time asc.

    ``key_values`` is an already-normalized structured-key map (e.g.
    ``{"ticker": "AAPL", "metric": "eps"}`` for finance, ``{"patient_id": ...,
    "condition": ...}`` for healthcare, ``{"matter_id": ..., "claim_type": ...}``
    for legal). Superseded versions are included so analysts can see how the fact
    evolved. Entity normalization is applied through the domain ``adapter`` so
    'Apple Inc.', 'AAPL', and ISIN 'US0378331005' all collapse to one series.
    """
    alias_map = {canonical: adapter.key_aliases(canonical) for canonical in key_values}
    metadata_presence = [
        or_(
            *(
                Memory.metadata_[alias].as_string().is_not(None)
                for alias in aliases
            )
        )
        for aliases in alias_map.values()
    ]
    scan_limit = min(20_000, max(5_000, limit * 50))
    stmt = (
        select(Memory)
        .where(
            and_(
                Memory.namespace == namespace,
                Memory.agent_id == agent_id,
                Memory.erased_at.is_(None),
                *metadata_presence,
            )
        )
        .order_by(Memory.event_time.asc(), Memory.id.asc())
        .limit(scan_limit + 1)
    )
    barrier_condition = _barrier_filter(Memory.barrier_group, barrier_override)
    if barrier_condition is not None:
        stmt = stmt.where(barrier_condition)
    fetched = (await db.execute(stmt)).scalars().all()
    scan_truncated = len(fetched) > scan_limit
    rows = fetched[:scan_limit]

    # For each requested (canonical) key, accept any of its metadata aliases.
    # e.g. for finance, 'ticker' is satisfied by metadata 'ticker' | 'entity' |
    # 'isin' | 'cusip' — all normalized to the same canonical value.
    matched: list[Memory] = []
    for mem in rows:
        meta = dict(mem.metadata_ or {})
        ok = True
        for canonical, want in key_values.items():
            found = None
            for alias in alias_map[canonical]:
                if alias in meta:
                    found = adapter.normalize(canonical, str(meta[alias]))
                    break
            if found != want:
                ok = False
                break
        if ok:
            matched.append(mem)
    total_matches_in_window = len(matched)
    returned = matched[:limit]
    if diagnostics is not None:
        diagnostics.update(
            rows_scanned=len(rows),
            scan_limit=scan_limit,
            scan_complete=not scan_truncated,
            total_is_lower_bound=scan_truncated,
            matches_in_scan=total_matches_in_window,
            has_more=total_matches_in_window > limit or scan_truncated,
        )

    subject_keys = await _keys_for_rows(db, namespace, returned)
    from .ranking import _decrypt
    return [_memory_to_out(m, _decrypt(m, subject_keys)) for m in returned]


# ── Conflicts ──────────────────────────────────────────────────────────────────


async def list_conflicts(
    db: AsyncSession,
    namespace: str,
    status: Optional[str] = "open",
    limit: int = 50,
    barrier_override: Optional[str] = None,
    after_detected_at: Optional[datetime] = None,
    after_id: Optional[UUID] = None,
) -> ConflictListResult:
    """
    List conflict flags for a namespace, newest first.

    Each conflict carries the decrypted content, source, and event-time of *both*
    disagreeing memories so a reviewer can decide which source to trust. Pass
    ``status`` to filter (``open`` | ``accept_a`` | ``accept_b`` | ``dismissed``),
    or ``None`` for all statuses.
    """
    total, rows = await _visible_conflict_rows(
        db,
        namespace,
        status=status,
        limit=limit + 1,
        barrier_override=barrier_override,
        after_detected_at=after_detected_at,
        after_id=after_id,
    )
    has_more = len(rows) > limit
    rows = rows[:limit]
    memory_rows = [memory for _, mem_a, mem_b in rows for memory in (mem_a, mem_b)]
    subject_keys = await _keys_for_rows(db, namespace, memory_rows)
    from .ranking import _decrypt

    conflicts: list[ConflictFlagOut] = []
    for flag, mem_a, mem_b in rows:
        conflicts.append(ConflictFlagOut(
            id=flag.id,
            namespace=flag.namespace,
            agent_id=flag.agent_id,
            memory_a_id=flag.memory_a_id,
            memory_b_id=flag.memory_b_id,
            memory_a_content=_decrypt(mem_a, subject_keys) if mem_a else None,
            memory_b_content=_decrypt(mem_b, subject_keys) if mem_b else None,
            memory_a_source=mem_a.source if mem_a else None,
            memory_b_source=mem_b.source if mem_b else None,
            memory_a_event_time=mem_a.event_time if mem_a else flag.detected_at,
            memory_b_event_time=mem_b.event_time if mem_b else flag.detected_at,
            confidence=flag.confidence,
            detected_at=flag.detected_at,
            status=flag.status,
            resolved_at=flag.resolved_at,
            resolver_note=flag.resolver_note,
        ))

    return ConflictListResult(
        conflicts=conflicts,
        total=total,
        returned=len(conflicts),
        complete=after_detected_at is None and not has_more,
        has_more=has_more,
        next_detected_at=(rows[-1][0].detected_at if has_more and rows else None),
        next_id=(rows[-1][0].id if has_more and rows else None),
        status_filter=status,
    )


async def resolve_conflict(
    db: AsyncSession,
    namespace: str,
    conflict_id: UUID,
    req: ConflictResolveRequest,
    barrier_override: Optional[str] = None,
) -> ConflictResolveResult:
    """
    Resolve a conflict flag and append a tamper-evident ``conflict_resolved`` event.

    ``accept_a`` invalidates memory_b; ``accept_b`` invalidates memory_a;
    ``dismiss`` leaves both live. Resolving a non-existent / cross-namespace
    conflict raises 404; resolving an already-resolved one raises 409; an unknown
    resolution raises 422.
    """
    from fastapi import HTTPException

    if req.resolution not in ("accept_a", "accept_b", "dismiss"):
        raise HTTPException(status_code=422, detail="resolution must be accept_a, accept_b, or dismiss")

    flag = await db.get(ConflictFlag, conflict_id)
    if flag is None or flag.namespace != namespace:
        raise HTTPException(status_code=404, detail="Conflict not found")
    if barrier_override is not None:
        mem_a = await db.get(Memory, flag.memory_a_id)
        mem_b = await db.get(Memory, flag.memory_b_id)
        if (
            mem_a is None
            or mem_b is None
            or not _barrier_visible(mem_a, barrier_override)
            or not _barrier_visible(mem_b, barrier_override)
        ):
            raise HTTPException(status_code=404, detail="Conflict not found")
    await _acquire_pg_advisory_lock(db, namespace, flag.agent_id)
    flag = (
        await db.execute(
            select(ConflictFlag)
            .where(ConflictFlag.id == conflict_id, ConflictFlag.namespace == namespace)
            .execution_options(populate_existing=True)
            .with_for_update()
        )
    ).scalar_one_or_none()
    if flag is None:
        raise HTTPException(status_code=404, detail="Conflict not found")
    if flag.status != "open":
        raise HTTPException(status_code=409, detail="Conflict already resolved")
    now = datetime.now(timezone.utc)
    invalidated: Optional[UUID] = None

    if req.resolution == "accept_a":
        invalidated = flag.memory_b_id
    elif req.resolution == "accept_b":
        invalidated = flag.memory_a_id

    if invalidated is not None:
        loser = (
            await db.execute(
                select(Memory)
                .where(Memory.id == invalidated, Memory.namespace == namespace)
                .execution_options(populate_existing=True)
                .with_for_update()
            )
        ).scalar_one_or_none()
        if loser is not None:
            loser.close_validity(valid_to=now, recorded_at=now)
            await remove_live_facts(db, [invalidated])

    flag.status = "dismissed" if req.resolution == "dismiss" else req.resolution
    flag.resolved_at = now
    flag.resolver_note = req.note

    await chain_log(
        db, namespace=namespace, agent_id=flag.agent_id,
        op="conflict_resolved", memory_id=flag.memory_b_id,
        content_hash=None,
        payload={
            "conflict_id": str(conflict_id),
            "resolution": req.resolution,
            "memory_invalidated": str(invalidated) if invalidated else None,
            "note": req.note,
            "resolved_at": now.isoformat(),
        },
    )
    await _fence_recall_caches_before_commit(db, namespace, flag.agent_id)
    await db.commit()

    return ConflictResolveResult(
        conflict_id=conflict_id,
        resolution=req.resolution,
        resolved_at=now,
        memory_invalidated=invalidated,
    )


# ── Erasure certificate ────────────────────────────────────────────────────────


async def get_erasure_certificate(
    db: AsyncSession,
    namespace: str,
    subject_id: str,
) -> Optional[dict]:
    """Compatibility wrapper for the first bounded certificate page."""

    from .subject_erasure_service import (
        SubjectErasureNotComplete,
        erasure_certificate_dict,
        get_subject_erasure_job_for_subject,
    )

    job = await get_subject_erasure_job_for_subject(
        db,
        namespace=namespace,
        subject_id=subject_id,
    )
    if job is None:
        return None
    try:
        return await erasure_certificate_dict(
            db,
            job=job,
            limit=100,
            after_memory_id=None,
        )
    except SubjectErasureNotComplete:
        return None
