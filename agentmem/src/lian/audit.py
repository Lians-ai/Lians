"""
Audit reconstruction: given agent_id + as_of timestamp,
reproduce the exact memory state + event-log trail behind any past decision.
"""
from __future__ import annotations
from datetime import datetime
from typing import Any, Optional

from sqlalchemy import select, and_, or_
from sqlalchemy.ext.asyncio import AsyncSession

from .models import Memory, EventLog
from .schemas import AuditReconstructResult, MemoryOut
from .memory_service import _memory_to_out
from .ranking import hybrid_recall
from .embeddings import get_embedding_provider


async def reconstruct(
    db: AsyncSession,
    namespace: str,
    agent_id: str,
    as_of: datetime,
    query: Optional[str] = None,
    k: int = 20,
) -> AuditReconstructResult:
    """
    Returns the memory state visible at as_of, plus the event log entries
    up to that point — the complete evidentiary trail.
    """
    # Memories valid at as_of
    if query:
        provider = get_embedding_provider()
        q_emb = await provider.embed_one(query)
        results = await hybrid_recall(
            db=db,
            namespace=namespace,
            agent_id=agent_id,
            query=query,
            query_embedding=q_emb,
            k=k,
            as_of=as_of,
        )
        memories = [_memory_to_out(mem, content) for mem, _, content in results]
    else:
        stmt = select(Memory).where(
            and_(
                Memory.namespace == namespace,
                Memory.agent_id == agent_id,
                Memory.valid_from <= as_of,
                or_(Memory.valid_to.is_(None), Memory.valid_to > as_of),
                Memory.event_time <= as_of,
                Memory.erased_at.is_(None),
            )
        )
        result = await db.execute(stmt)
        mems = result.scalars().all()
        memories = [_memory_to_out(m, None) for m in mems]

    # Event log up to as_of
    log_stmt = select(EventLog).where(
        and_(
            EventLog.namespace == namespace,
            EventLog.agent_id == agent_id,
            EventLog.created_at <= as_of,
        )
    ).order_by(EventLog.created_at)
    log_result = await db.execute(log_stmt)
    log_rows = log_result.scalars().all()

    event_trail = [
        {
            "id": str(row.id),
            "op": row.op,
            "memory_id": str(row.memory_id) if row.memory_id else None,
            "content_hash": row.content_hash,
            "payload": dict(row.payload or {}),
            "created_at": row.created_at.isoformat(),
        }
        for row in log_rows
    ]

    return AuditReconstructResult(
        memories=memories,
        event_trail=event_trail,
        as_of=as_of,
    )
