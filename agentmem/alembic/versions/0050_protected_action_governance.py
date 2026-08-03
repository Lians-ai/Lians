"""Add protected-action governance and product-inventory indexes.

Revision ID: 0050_protected_action_governance
Revises: 0049_autonomous_impact_worker

Gate capacity is reserved in the same transaction as permit consumption,
audit evidence, and durable metering. Status-leading indexes keep the global,
tenant-neutral observability refresh on narrow index pages as inventories grow.
The indexed tables are introduced earlier in this same unreleased migration
graph, so the indexes deliberately remain transactional: a failed upgrade can
roll back cleanly instead of leaving committed DDL behind an unstamped head.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0050_protected_action_governance"
down_revision = "0049_autonomous_impact_worker"
branch_labels = None
depends_on = None

_GLOBAL_INVENTORY_INDEXES = (
    (
        "ix_decision_evidence_coverage_global_status",
        "decision_evidence_kind_coverage",
        ["status", "namespace", "decision_id", "kind"],
    ),
    (
        "ix_investigation_case_global_status",
        "investigation_cases",
        ["status"],
    ),
    (
        "ix_remediation_task_global_status_due",
        "remediation_tasks",
        ["status", "due_at"],
    ),
)


def _create_inventory_indexes() -> None:
    for name, table, columns in _GLOBAL_INVENTORY_INDEXES:
        op.create_index(name, table, columns, unique=False)


def _drop_inventory_indexes() -> None:
    for name, table, _columns in reversed(_GLOBAL_INVENTORY_INDEXES):
        op.drop_index(name, table_name=table)


def upgrade() -> None:
    with op.batch_alter_table("namespace_policies") as batch:
        batch.add_column(
            sa.Column("protected_actions_daily_limit", sa.BigInteger(), nullable=True)
        )
        batch.create_check_constraint(
            "ck_namespace_policy_protected_action_quota",
            "protected_actions_daily_limit IS NULL "
            "OR protected_actions_daily_limit >= 0",
        )

    with op.batch_alter_table("namespace_daily_usage") as batch:
        batch.add_column(
            sa.Column(
                "protected_actions",
                sa.BigInteger(),
                nullable=False,
                server_default="0",
            )
        )
        batch.create_check_constraint(
            "ck_namespace_usage_protected_actions",
            "protected_actions >= 0",
        )

    _create_inventory_indexes()


def downgrade() -> None:
    _drop_inventory_indexes()

    with op.batch_alter_table("namespace_daily_usage") as batch:
        batch.drop_constraint(
            "ck_namespace_usage_protected_actions",
            type_="check",
        )
        batch.drop_column("protected_actions")

    with op.batch_alter_table("namespace_policies") as batch:
        batch.drop_constraint(
            "ck_namespace_policy_protected_action_quota",
            type_="check",
        )
        batch.drop_column("protected_actions_daily_limit")
