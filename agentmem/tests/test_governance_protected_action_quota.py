"""Durable protected-action quota contract (definitions only in build phase)."""

from __future__ import annotations

import inspect
from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path

import pytest
from lians.governance_service import (
    GovernanceViolation,
    get_effective_governance,
    reserve_namespace_usage,
)
from lians.models import NamespacePolicy


@pytest.mark.asyncio
async def test_protected_action_reservation_fails_closed_at_daily_limit(db):
    db.add(
        NamespacePolicy(
            namespace="protected-action-limited",
            governance_status="active",
            policy_version=1,
            protected_actions_daily_limit=1,
        )
    )
    await db.commit()

    await reserve_namespace_usage(
        db,
        namespace="protected-action-limited",
        protected_actions=1,
    )
    await db.commit()

    effective = await get_effective_governance(db, "protected-action-limited")
    assert effective.usage.protected_actions == 1
    assert effective.usage.protected_actions_remaining == 0

    with pytest.raises(GovernanceViolation) as captured:
        await reserve_namespace_usage(
            db,
            namespace="protected-action-limited",
            protected_actions=1,
        )
    await db.rollback()

    assert captured.value.status_code == 429
    assert captured.value.detail["code"] == "namespace_daily_quota_exceeded"
    assert captured.value.detail["metric"] == "protected_actions"
    assert captured.value.detail["remaining"] == 0
    assert "Retry-After" in captured.value.headers


@pytest.mark.asyncio
async def test_rolled_back_protected_action_reservation_consumes_no_capacity(db):
    db.add(
        NamespacePolicy(
            namespace="protected-action-rollback",
            governance_status="active",
            policy_version=1,
            protected_actions_daily_limit=1,
        )
    )
    await db.commit()

    await reserve_namespace_usage(
        db,
        namespace="protected-action-rollback",
        protected_actions=1,
    )
    await db.rollback()

    effective = await get_effective_governance(db, "protected-action-rollback")
    assert effective.usage.protected_actions == 0
    assert effective.usage.protected_actions_remaining == 1


@pytest.mark.asyncio
async def test_disabled_policy_tracks_protected_actions_without_enforcing_limit(db):
    db.add(
        NamespacePolicy(
            namespace="protected-action-disabled",
            governance_status="disabled",
            policy_version=2,
            protected_actions_daily_limit=0,
        )
    )
    await db.commit()

    await reserve_namespace_usage(
        db,
        namespace="protected-action-disabled",
        protected_actions=2,
    )
    await db.commit()

    effective = await get_effective_governance(db, "protected-action-disabled")
    assert effective.usage.protected_actions == 2
    assert effective.usage.protected_actions_remaining is None


def test_migration_is_strictly_ordered_and_adds_scale_indexes() -> None:
    migration_path = (
        Path(__file__).parents[1]
        / "alembic"
        / "versions"
        / "0050_protected_action_governance.py"
    )
    spec = spec_from_file_location("migration_0050_protected_actions", migration_path)
    assert spec is not None and spec.loader is not None
    migration = module_from_spec(spec)
    spec.loader.exec_module(migration)

    assert migration.revision == "0050_protected_action_governance"
    assert migration.down_revision == "0049_autonomous_impact_worker"
    index_names = {row[0] for row in migration._GLOBAL_INVENTORY_INDEXES}
    assert index_names == {
        "ix_decision_evidence_coverage_global_status",
        "ix_investigation_case_global_status",
        "ix_remediation_task_global_status_due",
    }
    upgrade_source = inspect.getsource(migration.upgrade)
    assert "protected_actions_daily_limit" in upgrade_source
    assert "protected_actions" in upgrade_source
