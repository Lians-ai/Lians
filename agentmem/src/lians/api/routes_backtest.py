"""
POST /v1/backtest/check — lookahead-bias contamination detection.

This is the open-sourceable thin primitive from SCALE.md §6:
  "Open-source one thin, genuinely useful primitive — a point-in-time-correctness
   checker or backtest-contamination detector."

The quant engineer who finds this endpoint is the next design partner.
"""
from datetime import datetime
from uuid import UUID

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field, model_validator
from sqlalchemy.ext.asyncio import AsyncSession

from ..db import get_db
from ..schemas import ContaminationFlagOut, ContaminationReportOut
from ..backtest import check_contamination
from .deps import get_auth, AuthContext

router = APIRouter(prefix="/v1", tags=["backtest"])


class BacktestCheckRequest(BaseModel):
    agent_id: str
    simulation_as_of: datetime = Field(
        ...,
        description="The simulation checkpoint timestamp. Memories with "
                    "event_time > this value are flagged as FUTURE_EVENT; "
                    "memories revised after this timestamp are LATE_REVISION.",
    )
    flag_limit: int = Field(
        default=1000,
        ge=1,
        le=10_000,
        description="Maximum detailed flags in this deterministic page.",
    )
    after_event_time: datetime | None = None
    after_id: UUID | None = None

    @model_validator(mode="after")
    def validate_cursor(self):
        if (self.after_event_time is None) != (self.after_id is None):
            raise ValueError(
                "backtest flag cursor requires both after_event_time and after_id"
            )
        return self


@router.post("/backtest/check", response_model=ContaminationReportOut)
async def backtest_contamination_check(
    req: BacktestCheckRequest,
    auth: AuthContext = Depends(get_auth),
    db: AsyncSession = Depends(get_db),
):
    """
    Detect lookahead bias in a backtest by scanning an agent's memory store.

    Returns exact contamination cardinality and cleanliness plus a bounded,
    keyset-paginated page of detailed flags.

    **Two contamination classes:**

    - `future_event` — `event_time > simulation_as_of`. The underlying event had
      not yet occurred at simulation time. Clear lookahead bias.

    - `late_revision` — `event_time <= simulation_as_of` but
      `ingestion_time > simulation_as_of`. The event is historical, but the
      *revised* or *corrected* version of the figure hadn't landed yet. This is
      the subtle case that pure vector stores miss entirely — they only index
      event_time, not when the revision arrived.

    `is_clean: true` is scoped to recorded memories visible inside the caller's
    authenticated namespace and information barrier. It does not prove that an
    external simulation consumed no unrecorded or out-of-band future input.

    **Why this matters for quant funds:** An AI agent that ingested a revised
    earnings figure on T+5 but ran a backtest "as of" T+2 used data it couldn't
    have seen. The agent's alpha may be entirely illusory. This endpoint makes
    the recorded Lians-memory portion of that question auditable in one call.
    """
    auth.require("read")
    report = await check_contamination(
        db, auth.namespace, req.agent_id, req.simulation_as_of,
        barrier_override=auth.barrier_group,
        flag_limit=req.flag_limit,
        after_event_time=req.after_event_time,
        after_id=req.after_id,
    )

    flags_out = [
        ContaminationFlagOut(
            memory_id=f.memory_id,
            event_time=f.event_time,
            ingestion_time=f.ingestion_time,
            contamination_type=f.contamination_type,
            delta_days=f.delta_days,
            content_preview=f.content_preview,
            source=f.source,
            metadata=f.metadata,
        )
        for f in report.flags
    ]

    return ContaminationReportOut(
        agent_id=report.agent_id,
        namespace=report.namespace,
        simulation_as_of=report.simulation_as_of,
        memories_checked=report.memories_checked,
        flags_total=report.flags_total,
        flags_returned=report.flags_returned,
        flags_complete=report.flags_complete,
        has_more=report.has_more,
        next_event_time=report.next_event_time,
        next_id=report.next_id,
        flags=flags_out,
        contamination_rate=report.contamination_rate,
        is_clean=report.is_clean,
    )
