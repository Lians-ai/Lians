"""Bounded, point-in-time backtest contamination detection.

The detector distinguishes future events from historical events whose revised
record was ingested only after the simulated checkpoint.  Exact database-side
counts determine cleanliness; the detailed flags are a deterministic keyset
page and never masquerade as the complete set when the page is truncated.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional
from uuid import UUID

from sqlalchemy import and_, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from .models import Memory
from .ranking import _decrypt as _decrypt_memory_content
from .subject_key_loader import load_subject_keys

FUTURE_EVENT = "future_event"
LATE_REVISION = "late_revision"


@dataclass
class ContaminationFlag:
    memory_id: UUID
    event_time: datetime
    ingestion_time: datetime
    contamination_type: str
    delta_days: float
    content_preview: Optional[str]
    source: Optional[str]
    metadata: dict = field(default_factory=dict)


@dataclass
class ContaminationReport:
    agent_id: str
    namespace: str
    simulation_as_of: datetime
    memories_checked: int
    flags_total: int
    flags_returned: int
    flags_complete: bool
    has_more: bool
    next_event_time: Optional[datetime]
    next_id: Optional[UUID]
    flags: list[ContaminationFlag]
    contamination_rate: float
    is_clean: bool


def _aware(value: datetime) -> datetime:
    """SQLite may return naive timestamps even for timezone-aware columns."""
    return value if value.tzinfo is not None else value.replace(tzinfo=timezone.utc)


async def check_contamination(
    db: AsyncSession,
    namespace: str,
    agent_id: str,
    simulation_as_of: datetime,
    barrier_override: Optional[str] = None,
    flag_limit: int = 1000,
    after_event_time: Optional[datetime] = None,
    after_id: Optional[UUID] = None,
) -> ContaminationReport:
    """Return exact contamination truth plus one bounded flag page."""
    if (after_event_time is None) != (after_id is None):
        raise ValueError("backtest flag cursor requires both after_event_time and after_id")

    contaminant_conditions = [
        Memory.namespace == namespace,
        Memory.agent_id == agent_id,
        Memory.erased_at.is_(None),
        or_(
            Memory.event_time > simulation_as_of,
            Memory.ingestion_time > simulation_as_of,
        ),
    ]
    all_memory_conditions = [
        Memory.namespace == namespace,
        Memory.agent_id == agent_id,
        Memory.erased_at.is_(None),
    ]
    if barrier_override is not None:
        barrier_condition = or_(
            Memory.barrier_group.is_(None),
            Memory.barrier_group == barrier_override,
        )
        contaminant_conditions.append(barrier_condition)
        all_memory_conditions.append(barrier_condition)

    flags_total = int(
        (
            await db.execute(
                select(func.count(Memory.id)).where(*contaminant_conditions)
            )
        ).scalar_one()
        or 0
    )
    total_count = int(
        (
            await db.execute(
                select(func.count(Memory.id)).where(*all_memory_conditions)
            )
        ).scalar_one()
        or 0
    )

    page_conditions = list(contaminant_conditions)
    if after_event_time is not None and after_id is not None:
        page_conditions.append(
            or_(
                Memory.event_time > after_event_time,
                and_(Memory.event_time == after_event_time, Memory.id > after_id),
            )
        )
    bounded_limit = max(1, min(10_000, flag_limit))
    raw_candidates = list(
        (
            await db.execute(
                select(Memory)
                .where(*page_conditions)
                .order_by(Memory.event_time.asc(), Memory.id.asc())
                .limit(bounded_limit + 1)
            )
        ).scalars().all()
    )
    has_more = len(raw_candidates) > bounded_limit
    candidates = raw_candidates[:bounded_limit]

    subject_keys = await load_subject_keys(
        db,
        namespace,
        (memory.subject_id for memory in candidates),
    )
    simulation_checkpoint = _aware(simulation_as_of)
    flags: list[ContaminationFlag] = []
    for memory in candidates:
        is_future = _aware(memory.event_time) > simulation_checkpoint
        contamination_type = FUTURE_EVENT if is_future else LATE_REVISION
        reference_time = memory.event_time if is_future else memory.ingestion_time
        delta_days = (
            _aware(reference_time) - simulation_checkpoint
        ).total_seconds() / 86_400.0
        content = _decrypt_memory_content(memory, subject_keys)
        preview = (content[:120] + "...") if content and len(content) > 120 else content
        flags.append(
            ContaminationFlag(
                memory_id=memory.id,
                event_time=memory.event_time,
                ingestion_time=memory.ingestion_time,
                contamination_type=contamination_type,
                delta_days=round(delta_days, 2),
                content_preview=preview,
                source=memory.source,
                metadata=dict(memory.metadata_ or {}),
            )
        )

    next_memory = candidates[-1] if has_more and candidates else None
    rate = flags_total / total_count if total_count else 0.0
    return ContaminationReport(
        agent_id=agent_id,
        namespace=namespace,
        simulation_as_of=simulation_as_of,
        memories_checked=total_count,
        flags_total=flags_total,
        flags_returned=len(flags),
        flags_complete=after_event_time is None and not has_more,
        has_more=has_more,
        next_event_time=next_memory.event_time if next_memory is not None else None,
        next_id=next_memory.id if next_memory is not None else None,
        flags=flags,
        contamination_rate=round(rate, 4),
        is_clean=flags_total == 0,
    )
