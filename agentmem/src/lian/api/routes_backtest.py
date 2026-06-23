"""
POST /v1/backtest/check — lookahead-bias contamination detection.

This is the open-sourceable thin primitive from SCALE.md §6:
  "Open-source one thin, genuinely useful primitive — a point-in-time-correctness
   checker or backtest-contamination detector."

The quant engineer who finds this endpoint is the next design partner.
"""
from datetime import datetime

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
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


@router.post("/backtest/check", response_model=ContaminationReportOut)
async def backtest_contamination_check(
    req: BacktestCheckRequest,
    auth: AuthContext = Depends(get_auth),
    db: AsyncSession = Depends(get_db),
):
    """
    Detect lookahead bias in a backtest by scanning an agent's memory store.

    Returns a contamination report flagging every memory the agent possessed
    that it couldn't have known at `simulation_as_of`.

    **Two contamination classes:**

    - `future_event` — `event_time > simulation_as_of`. The underlying event had
      not yet occurred at simulation time. Clear lookahead bias.

    - `late_revision` — `event_time <= simulation_as_of` but
      `ingestion_time > simulation_as_of`. The event is historical, but the
      *revised* or *corrected* version of the figure hadn't landed yet. This is
      the subtle case that pure vector stores miss entirely — they only index
      event_time, not when the revision arrived.

    A report with `is_clean: true` is the proof a risk committee needs before
    trusting a backtest result.

    **Why this matters for quant funds:** An AI agent that ingested a revised
    earnings figure on T+5 but ran a backtest "as of" T+2 used data it couldn't
    have seen. The agent's alpha may be entirely illusory. This endpoint makes
    that auditable in a single call.
    """
    auth.require("read")
    report = await check_contamination(db, auth.namespace, req.agent_id, req.simulation_as_of)

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
        flags=flags_out,
        contamination_rate=report.contamination_rate,
        is_clean=report.is_clean,
    )
