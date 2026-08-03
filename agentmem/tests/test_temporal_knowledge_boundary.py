"""Regression coverage for business-time + transaction-time reconstruction."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from lians.memory_service import (
    add_memory,
    apply_supersession_action,
    get_knowledge_snapshot,
)
from lians.models import Memory
from lians.schemas import MemoryAdd, SupersessionAction

NS = "temporal-knowledge-boundary"
AGENT = "underwriter-boundary-test"
ORIGINAL_EVENT = datetime(2026, 1, 1, tzinfo=UTC)
CORRECTION_EVENT = datetime(2026, 1, 15, tzinfo=UTC)
DECISION_EVENT = datetime(2026, 2, 1, tzinfo=UTC)
FACT_KEY = {"ticker": "ACME", "metric": "verified_income", "period": "2026"}


def _utc(value: datetime) -> datetime:
    return value if value.tzinfo is not None else value.replace(tzinfo=UTC)


async def _add(db, content: str, event_time: datetime):
    return await add_memory(
        db,
        NS,
        MemoryAdd(
            agent_id=AGENT,
            content=content,
            event_time=event_time,
            source="verified-income-service",
            metadata=FACT_KEY,
        ),
    )


@pytest.mark.asyncio
async def test_late_backdated_correction_does_not_rewrite_earlier_knowledge(db):
    original = await _add(db, "ACME verified income is USD 72,000", ORIGINAL_EVENT)
    original_row = await db.get(Memory, original.id)
    assert original_row is not None

    # This is the decision's transaction-time evidence cutoff: after the first
    # fact was recorded, before the corrected source record arrived.
    recorded_cutoff = datetime.now(UTC)
    assert _utc(original_row.system_valid_from) <= recorded_cutoff

    correction = await _add(
        db,
        "Correction: ACME verified income is USD 68,000",
        CORRECTION_EVENT,
    )
    await db.refresh(original_row)
    correction_row = await db.get(Memory, correction.id)
    assert correction_row is not None
    assert _utc(original_row.valid_to) == CORRECTION_EVENT
    assert original_row.system_valid_to is not None
    assert _utc(original_row.system_valid_to) > recorded_cutoff

    at_decision = await get_knowledge_snapshot(
        db,
        NS,
        AGENT,
        DECISION_EVENT,
        recorded_as_of=recorded_cutoff,
    )
    at_decision_ids = {item.id for item in at_decision}
    assert original.id in at_decision_ids
    assert correction.id not in at_decision_ids

    # Once the correction has been learned, the same business-time query uses
    # the corrected interval. This also proves existing event-time semantics.
    after_correction = await get_knowledge_snapshot(
        db,
        NS,
        AGENT,
        DECISION_EVENT,
        recorded_as_of=_utc(correction_row.system_valid_from) + timedelta(seconds=1),
    )
    after_ids = {item.id for item in after_correction}
    assert correction.id in after_ids
    assert original.id not in after_ids

    event_time_only = await get_knowledge_snapshot(db, NS, AGENT, DECISION_EVENT)
    assert {item.id for item in event_time_only} == after_ids


@pytest.mark.asyncio
async def test_rejected_supersession_reopens_both_validity_axes(db):
    original = await _add(db, "ACME verified income is USD 72,000", ORIGINAL_EVENT)
    await _add(db, "ACME verified income revised to USD 68,000", CORRECTION_EVENT)

    original_row = await db.get(Memory, original.id)
    assert original_row is not None
    assert _utc(original_row.valid_to) == CORRECTION_EVENT
    assert original_row.system_valid_to is not None

    await apply_supersession_action(
        db,
        NS,
        original.id,
        SupersessionAction(
            action="reject",
            expected_superseded_by=original_row.superseded_by,
            reviewer_note="Not the same applicant",
        ),
    )
    await db.refresh(original_row)
    assert original_row.valid_to is None
    assert original_row.system_valid_to is None
    assert original_row.superseded_by is None
