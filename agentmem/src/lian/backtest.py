"""
Backtest-contamination detector — open-sourceable thin primitive.

Answers the question every quant fund fears:
  "Did my agent use data it couldn't have known at simulation time?"

Lookahead bias in an AI agent is subtle. A memory may carry an event_time
from last quarter — which looks historical — but if the *revision* of that
figure arrived after the simulation checkpoint, the agent used the corrected
number before it existed.

Two contamination classes:

  FUTURE_EVENT    event_time > simulation_as_of
                  The underlying event had not yet happened. Clear lookahead.

  LATE_REVISION   event_time <= simulation_as_of
                  AND ingestion_time > simulation_as_of
                  The event is "old" but the corrected/revised report hadn't
                  landed yet at simulation time. This is the subtle case that
                  vector stores miss entirely — they only see event_time.

This module is intentionally self-contained (no AgentMem-specific imports
beyond the ORM model) so it can be extracted and open-sourced as a
standalone library.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional
from uuid import UUID

from sqlalchemy import select, and_, or_
from sqlalchemy.ext.asyncio import AsyncSession

from .models import Memory
from .memory_service import _memory_to_out, _decrypt_memory_content, _resolve_subject_key


FUTURE_EVENT = "future_event"
LATE_REVISION = "late_revision"


@dataclass
class ContaminationFlag:
    memory_id: UUID
    event_time: datetime
    ingestion_time: datetime
    contamination_type: str          # FUTURE_EVENT | LATE_REVISION
    delta_days: float                # days "into the future" relative to sim checkpoint
    content_preview: Optional[str]   # first 120 chars; None if erased
    source: Optional[str]
    metadata: dict = field(default_factory=dict)


@dataclass
class ContaminationReport:
    agent_id: str
    namespace: str
    simulation_as_of: datetime
    memories_checked: int
    flags: list[ContaminationFlag]
    contamination_rate: float        # flags / memories_checked (0.0 if none checked)
    is_clean: bool


async def check_contamination(
    db: AsyncSession,
    namespace: str,
    agent_id: str,
    simulation_as_of: datetime,
) -> ContaminationReport:
    """
    Scan every memory an agent possessed and flag those that constitute
    lookahead bias relative to `simulation_as_of`.

    Returns a ContaminationReport with per-memory flags and a summary.
    A clean report (is_clean=True) is the proof a quant fund needs before
    trusting a backtest result.
    """
    stmt = (
        select(Memory)
        .where(
            and_(
                Memory.namespace == namespace,
                Memory.agent_id == agent_id,
                Memory.erased_at.is_(None),
                # Either class of contamination — handle in Python for clarity
                or_(
                    Memory.event_time > simulation_as_of,
                    Memory.ingestion_time > simulation_as_of,
                ),
            )
        )
        .order_by(Memory.event_time.asc())
    )
    result = await db.execute(stmt)
    candidates = result.scalars().all()

    # Also count total memories for the contamination_rate denominator
    from sqlalchemy import func
    count_stmt = select(func.count(Memory.id)).where(
        and_(
            Memory.namespace == namespace,
            Memory.agent_id == agent_id,
            Memory.erased_at.is_(None),
        )
    )
    total_count = (await db.execute(count_stmt)).scalar_one()

    # Decrypt content for preview
    subject_keys: dict[str, bytes | None] = {}
    sids = {m.subject_id for m in candidates if m.subject_id}
    for sid in sids:
        try:
            subject_keys[sid] = await _resolve_subject_key(db, sid, namespace)
        except Exception:
            subject_keys[sid] = None

    from datetime import timezone as _tz

    def _aware(dt: datetime) -> datetime:
        """Ensure datetime is timezone-aware (SQLite strips tz info)."""
        if dt.tzinfo is None:
            return dt.replace(tzinfo=_tz.utc)
        return dt

    # Normalise simulation_as_of to UTC-aware for comparison
    sim = _aware(simulation_as_of)

    flags: list[ContaminationFlag] = []
    for mem in candidates:
        is_future = _aware(mem.event_time) > sim
        is_late = (not is_future) and _aware(mem.ingestion_time) > sim

        if not (is_future or is_late):
            continue

        ctype = FUTURE_EVENT if is_future else LATE_REVISION
        ref_time = _aware(mem.event_time) if is_future else _aware(mem.ingestion_time)
        delta = (ref_time - sim).total_seconds() / 86400.0

        content = _decrypt_memory_content(mem, subject_keys)
        preview = (content[:120] + "…") if content and len(content) > 120 else content

        flags.append(ContaminationFlag(
            memory_id=mem.id,
            event_time=mem.event_time,
            ingestion_time=mem.ingestion_time,
            contamination_type=ctype,
            delta_days=round(delta, 2),
            content_preview=preview,
            source=mem.source,
            metadata=dict(mem.metadata_ or {}),
        ))

    rate = len(flags) / total_count if total_count > 0 else 0.0
    return ContaminationReport(
        agent_id=agent_id,
        namespace=namespace,
        simulation_as_of=simulation_as_of,
        memories_checked=total_count,
        flags=flags,
        contamination_rate=round(rate, 4),
        is_clean=len(flags) == 0,
    )
