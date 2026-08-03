"""Bounded point-in-time audit reconstruction."""
from __future__ import annotations

from datetime import datetime
from typing import Optional

from sqlalchemy import Text, cast, exists, func, literal, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from .embeddings import get_embedding_provider
from .memory_service import (
    _memory_to_out,
    count_knowledge_snapshot,
    get_knowledge_snapshot,
    measure_knowledge_snapshot_bytes,
)
from .models import EventLog, Memory
from .ranking import hybrid_recall
from .schemas import AuditReconstructResult


class AuditReconstructionCapacityExceeded(RuntimeError):
    """A reconstruction would exceed its bounded response materialization."""

    code = "audit_reconstruction_byte_capacity_exceeded"

    def __init__(
        self,
        *,
        estimated_bytes: int,
        byte_limit: int,
        memory_estimated_bytes: int,
        event_estimated_bytes: int,
    ) -> None:
        super().__init__("The requested audit reconstruction exceeds the byte budget")
        self.public_message = str(self)
        self.estimated_bytes = estimated_bytes
        self.byte_limit = byte_limit
        self.memory_estimated_bytes = memory_estimated_bytes
        self.event_estimated_bytes = event_estimated_bytes


async def reconstruct(
    db: AsyncSession,
    namespace: str,
    agent_id: str,
    as_of: datetime,
    query: Optional[str] = None,
    k: int = 20,
    barrier_override: Optional[str] = None,
    *,
    memory_limit: int = 1_000,
    event_limit: int = 5_000,
    max_response_bytes: int | None = None,
) -> AuditReconstructResult:
    """Return a bounded, cardinality-scored knowledge and event reconstruction."""
    measured_rows, memory_estimated_bytes = await measure_knowledge_snapshot_bytes(
        db,
        namespace,
        agent_id,
        as_of,
        include_content=True,
        barrier_override=barrier_override,
        # Ranked historical recall decrypts a bounded candidate window before
        # selecting k. Conservatively require the whole visible snapshot to fit;
        # unranked reconstruction measures only its deterministic response page.
        limit=None if query else memory_limit + 1,
    )
    memory_total = (
        measured_rows
        if query
        else await count_knowledge_snapshot(
            db,
            namespace,
            agent_id,
            as_of,
            barrier_override=barrier_override,
        )
    )

    event_filters = [
        EventLog.namespace == namespace,
        EventLog.agent_id == agent_id,
        EventLog.created_at <= as_of,
    ]
    if barrier_override is not None:
        visible_memory = exists(
            select(Memory.id).where(
                Memory.id == EventLog.memory_id,
                Memory.namespace == namespace,
                or_(
                    Memory.barrier_group.is_(None),
                    Memory.barrier_group == barrier_override,
                ),
            )
        )
        event_filters.extend((EventLog.memory_id.is_not(None), visible_memory))

    event_total = int(
        (
            await db.execute(select(func.count(EventLog.id)).where(*event_filters))
        ).scalar_one()
        or 0
    )
    event_row_bytes = (
        literal(1_024)
        + 4 * func.coalesce(func.length(EventLog.op), 0)
        + 4 * func.coalesce(func.length(EventLog.content_hash), 0)
        + 4 * func.coalesce(func.length(cast(EventLog.payload, Text)), 0)
    )
    bounded_event_bytes = (
        select(event_row_bytes.label("estimated_bytes"))
        .where(*event_filters)
        .order_by(EventLog.chain_position.asc())
        .limit(event_limit + 1)
        .subquery()
    )
    event_estimated_bytes = int(
        (
            await db.execute(
                select(
                    func.coalesce(
                        func.sum(bounded_event_bytes.c.estimated_bytes),
                        0,
                    )
                ).select_from(bounded_event_bytes)
            )
        ).scalar_one()
        or 0
    )
    estimated_bytes = memory_estimated_bytes + event_estimated_bytes
    if max_response_bytes is not None and estimated_bytes > max_response_bytes:
        raise AuditReconstructionCapacityExceeded(
            estimated_bytes=estimated_bytes,
            byte_limit=max_response_bytes,
            memory_estimated_bytes=memory_estimated_bytes,
            event_estimated_bytes=event_estimated_bytes,
        )

    retrieval_degraded = False
    candidate_window_complete = True
    if query:
        try:
            q_emb = await get_embedding_provider().embed_one(query)
        except Exception:
            q_emb = []
            retrieval_degraded = True
        diagnostics: dict[str, object] = {}
        results = await hybrid_recall(
            db=db,
            namespace=namespace,
            agent_id=agent_id,
            query=query,
            query_embedding=q_emb,
            k=k,
            as_of=as_of,
            barrier_group=barrier_override,
            diagnostics=diagnostics,
        )
        memories = [_memory_to_out(mem, content) for mem, _, content in results]
        candidate_window_complete = bool(
            diagnostics.get("candidate_window_complete", True)
        )
        memories_complete = False
        memories_mode = "ranked_query"
    else:
        fetched_memories = await get_knowledge_snapshot(
            db,
            namespace,
            agent_id,
            as_of,
            memory_limit,
            barrier_override=barrier_override,
        )
        memories = fetched_memories
        memories_complete = memory_total <= memory_limit
        memories_mode = "knowledge_snapshot"

    fetched_events = list(
        (
            await db.execute(
                select(EventLog)
                .where(*event_filters)
                .order_by(EventLog.chain_position.asc())
                .limit(event_limit)
            )
        ).scalars()
    )
    events_complete = event_total <= event_limit
    log_rows = fetched_events
    event_trail = [
        {
            "id": str(row.id),
            "op": row.op,
            "memory_id": str(row.memory_id) if row.memory_id else None,
            "content_hash": row.content_hash,
            "payload": dict(row.payload or {}),
            "created_at": row.created_at.isoformat(),
            "chain_position": row.chain_position,
        }
        for row in log_rows
    ]

    return AuditReconstructResult(
        memories=memories,
        event_trail=event_trail,
        as_of=as_of,
        memory_total=memory_total,
        memories_returned=len(memories),
        memories_complete=memories_complete,
        memories_mode=memories_mode,
        event_total=event_total,
        events_returned=len(event_trail),
        events_complete=events_complete,
        retrieval_degraded=retrieval_degraded,
        candidate_window_complete=candidate_window_complete,
    )
