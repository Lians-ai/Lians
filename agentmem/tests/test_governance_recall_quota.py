"""Durable namespace recall quota contract (definitions only in build phase)."""

from __future__ import annotations

import pytest
from lians.governance_service import (
    GovernanceViolation,
    get_effective_governance,
    reserve_namespace_usage,
)
from lians.models import NamespacePolicy


@pytest.mark.asyncio
async def test_recall_reservation_is_durable_and_fails_closed_at_limit(db):
    db.add(
        NamespacePolicy(
            namespace="recall-limited",
            governance_status="active",
            policy_version=1,
            recalls_daily_limit=1,
        )
    )
    await db.commit()

    await reserve_namespace_usage(db, namespace="recall-limited", recalls=1)
    await db.commit()

    effective = await get_effective_governance(db, "recall-limited")
    assert effective.usage.recalls == 1
    assert effective.usage.recalls_remaining == 0

    with pytest.raises(GovernanceViolation) as captured:
        await reserve_namespace_usage(db, namespace="recall-limited", recalls=1)
    await db.rollback()

    assert captured.value.status_code == 429
    assert captured.value.detail["code"] == "namespace_daily_quota_exceeded"
    assert captured.value.detail["metric"] == "recalls"
    assert captured.value.detail["remaining"] == 0
    assert "Retry-After" in captured.value.headers


@pytest.mark.asyncio
async def test_rolled_back_recall_reservation_consumes_no_capacity(db):
    db.add(
        NamespacePolicy(
            namespace="recall-rollback",
            governance_status="active",
            policy_version=1,
            recalls_daily_limit=1,
        )
    )
    await db.commit()

    await reserve_namespace_usage(db, namespace="recall-rollback", recalls=1)
    await db.rollback()

    effective = await get_effective_governance(db, "recall-rollback")
    assert effective.usage.recalls == 0
    assert effective.usage.recalls_remaining == 1


@pytest.mark.asyncio
async def test_disabled_policy_tracks_recall_usage_without_enforcing_old_limit(db):
    db.add(
        NamespacePolicy(
            namespace="recall-disabled",
            governance_status="disabled",
            policy_version=2,
            recalls_daily_limit=0,
        )
    )
    await db.commit()

    await reserve_namespace_usage(db, namespace="recall-disabled", recalls=2)
    await db.commit()

    effective = await get_effective_governance(db, "recall-disabled")
    assert effective.usage.recalls == 2
    assert effective.usage.recalls_remaining is None
