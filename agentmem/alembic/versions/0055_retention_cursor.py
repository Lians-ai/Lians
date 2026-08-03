"""Add a durable singleton cursor for bounded retention sweeps.

Revision ID: 0055_retention_cursor
Revises: 0054a_otel_barrier_contract

The scheduler holds its existing session advisory lock while reading and
advancing this row. Cursor advancement happens only after a bounded page has
been attempted, so a crash can repeat work but cannot skip a key range.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0055_retention_cursor"
down_revision = "0054a_otel_barrier_contract"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "retention_scheduler_state",
        sa.Column("id", sa.Integer(), autoincrement=False, nullable=False),
        sa.Column("namespace_cursor", sa.String(), nullable=True),
        sa.Column(
            "sweep_generation",
            sa.BigInteger(),
            nullable=False,
            server_default=sa.text("0"),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
        sa.CheckConstraint(
            "id = 1",
            name="ck_retention_scheduler_state_singleton",
        ),
        sa.CheckConstraint(
            "sweep_generation >= 0",
            name="ck_retention_scheduler_state_generation",
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.execute(
        "INSERT INTO retention_scheduler_state "
        "(id, namespace_cursor, sweep_generation) VALUES (1, NULL, 0)"
    )

    if op.get_bind().dialect.name == "postgresql":
        # Default privileges grant broad DML to the runtime capability role.
        # This global control row needs only bounded read/advance authority.
        op.execute(
            "REVOKE ALL ON TABLE public.retention_scheduler_state "
            "FROM PUBLIC, lians_runtime"
        )
        op.execute(
            "GRANT SELECT ON TABLE public.retention_scheduler_state "
            "TO lians_runtime"
        )
        op.execute(
            "GRANT UPDATE (namespace_cursor, sweep_generation, updated_at) "
            "ON TABLE public.retention_scheduler_state TO lians_runtime"
        )


def downgrade() -> None:
    if op.get_bind().dialect.name == "postgresql":
        op.execute(
            "REVOKE ALL ON TABLE public.retention_scheduler_state "
            "FROM PUBLIC, lians_runtime"
        )
    op.drop_table("retention_scheduler_state")
