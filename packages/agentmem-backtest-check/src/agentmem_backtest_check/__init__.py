"""
agentmem-backtest-check — standalone lookahead-bias detector for AI agents.

Answers the question every quant fund fears:
  "Did my agent use data it couldn't have known at simulation time?"

Two contamination classes:

  FUTURE_EVENT    event_time > simulation_as_of
                  The underlying event hadn't happened yet. Clear lookahead.

  LATE_REVISION   event_time <= simulation_as_of
                  AND ingestion_time > simulation_as_of
                  The event is "old" but the corrected/revised figure hadn't
                  landed yet. This is the subtle case — pure vector stores
                  miss it entirely because they only track event_time.

Usage::

    from agentmem_backtest_check import check_contamination
    from datetime import datetime, timezone

    memories = [
        {
            "id": "m1",
            "content": "NVDA Q3 revenue $35.1B",
            "event_time": datetime(2025, 8, 27, tzinfo=timezone.utc),
            "ingestion_time": datetime(2025, 8, 27, tzinfo=timezone.utc),
        },
        {
            "id": "m2",
            "content": "NVDA FY2026 revenue guidance raised to $40B",
            "event_time": datetime(2025, 11, 19, tzinfo=timezone.utc),
            "ingestion_time": datetime(2025, 11, 19, tzinfo=timezone.utc),
        },
    ]

    result = check_contamination(
        memories,
        as_of=datetime(2025, 9, 1, tzinfo=timezone.utc),
    )
    print(result.is_clean)            # False
    print(result.contamination_rate)  # 0.5
    for f in result.flags:
        print(f.contamination_type, f.delta_days, "days")
        print(f.content_preview)

This library has zero runtime dependencies (stdlib only).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Optional, Union


__version__ = "0.1.0"
__all__ = [
    "check_contamination",
    "ContaminationReport",
    "ContaminationFlag",
    "FUTURE_EVENT",
    "LATE_REVISION",
]

FUTURE_EVENT = "future_event"
LATE_REVISION = "late_revision"


@dataclass
class ContaminationFlag:
    """A single memory flagged as lookahead-biased."""
    id: Any                            # the memory's original id
    event_time: datetime
    ingestion_time: datetime
    contamination_type: str            # FUTURE_EVENT | LATE_REVISION
    delta_days: float                  # days past simulation_as_of
    content_preview: Optional[str]
    metadata: dict = field(default_factory=dict)


@dataclass
class ContaminationReport:
    """Full contamination scan result."""
    simulation_as_of: datetime
    memories_checked: int
    flags: list[ContaminationFlag]
    contamination_rate: float          # flags / memories_checked
    is_clean: bool

    def summary(self) -> str:
        if self.is_clean:
            return (
                f"CLEAN — {self.memories_checked} memories checked, "
                f"0 contaminated (as of {self.simulation_as_of.date()})"
            )
        n = len(self.flags)
        rate = self.contamination_rate * 100
        return (
            f"CONTAMINATED — {n}/{self.memories_checked} memories flagged "
            f"({rate:.1f}%) as of {self.simulation_as_of.date()}"
        )


def _utc(dt: datetime) -> datetime:
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def check_contamination(
    memories: list[Union[dict, Any]],
    as_of: datetime,
    *,
    event_time_field: str = "event_time",
    ingestion_time_field: str = "ingestion_time",
    id_field: str = "id",
    content_field: str = "content",
    metadata_field: str = "metadata",
    preview_length: int = 120,
) -> ContaminationReport:
    """
    Scan *memories* for lookahead bias relative to *as_of*.

    *memories* may be plain dicts or objects with attributes — the library
    handles both. Pass field name overrides if your schema differs.

    Parameters
    ----------
    memories:
        Sequence of memory records to scan. Each must expose event_time and
        ingestion_time (see field name params below).
    as_of:
        The simulation checkpoint. Memories that couldn't have been known
        at this point in time are flagged.
    event_time_field / ingestion_time_field / id_field / content_field / metadata_field:
        Field name overrides for dict-style memories.
    preview_length:
        How many characters of content to include in the flag preview.

    Returns
    -------
    ContaminationReport
    """
    sim = _utc(as_of)
    flags: list[ContaminationFlag] = []

    def _get(mem: Any, field_name: str, default: Any = None) -> Any:
        if isinstance(mem, dict):
            return mem.get(field_name, default)
        return getattr(mem, field_name, default)

    for mem in memories:
        raw_event = _get(mem, event_time_field)
        raw_ingest = _get(mem, ingestion_time_field)

        if raw_event is None or raw_ingest is None:
            continue

        event_t = _utc(raw_event)
        ingest_t = _utc(raw_ingest)

        is_future = event_t > sim
        is_late = (not is_future) and (ingest_t > sim)

        if not (is_future or is_late):
            continue

        ctype = FUTURE_EVENT if is_future else LATE_REVISION
        ref_t = event_t if is_future else ingest_t
        delta = (ref_t - sim).total_seconds() / 86400.0

        content = _get(mem, content_field)
        if content:
            preview = (content[:preview_length] + "…") if len(content) > preview_length else content
        else:
            preview = None

        flags.append(ContaminationFlag(
            id=_get(mem, id_field),
            event_time=raw_event,
            ingestion_time=raw_ingest,
            contamination_type=ctype,
            delta_days=round(delta, 2),
            content_preview=preview,
            metadata=_get(mem, metadata_field) or {},
        ))

    total = len(memories)
    rate = len(flags) / total if total > 0 else 0.0

    return ContaminationReport(
        simulation_as_of=as_of,
        memories_checked=total,
        flags=flags,
        contamination_rate=round(rate, 4),
        is_clean=len(flags) == 0,
    )
