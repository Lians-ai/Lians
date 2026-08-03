"""Add durable namespace recall quotas.

Revision ID: 0047_recall_governance_quota
Revises: 0046a_idempotency_backfill
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0047_recall_governance_quota"
down_revision = "0046a_idempotency_backfill"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("namespace_policies") as batch:
        batch.add_column(sa.Column("recalls_daily_limit", sa.BigInteger(), nullable=True))
        batch.create_check_constraint(
            "ck_namespace_policy_recall_quota",
            "recalls_daily_limit IS NULL OR recalls_daily_limit >= 0",
        )

    with op.batch_alter_table("namespace_daily_usage") as batch:
        batch.add_column(
            sa.Column(
                "recalls",
                sa.BigInteger(),
                nullable=False,
                server_default="0",
            )
        )
        batch.create_check_constraint(
            "ck_namespace_usage_recalls",
            "recalls >= 0",
        )


def downgrade() -> None:
    with op.batch_alter_table("namespace_daily_usage") as batch:
        batch.drop_constraint("ck_namespace_usage_recalls", type_="check")
        batch.drop_column("recalls")

    with op.batch_alter_table("namespace_policies") as batch:
        batch.drop_constraint("ck_namespace_policy_recall_quota", type_="check")
        batch.drop_column("recalls_daily_limit")
