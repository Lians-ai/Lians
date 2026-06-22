"""Add conflict_flags table for same-time fact disagreement detection

Revision ID: 0008_conflict_flags
Revises: 0007_billing
Create Date: 2026-06-21

Conflict flags are created when two memories report different values for the
same structured fact (same ticker/metric/etc.) at the same event_time.  Both
memories remain valid until a human resolves which source to trust.

Resolution options:
  accept_a  — memory_a is authoritative; memory_b is invalidated
  accept_b  — memory_b is authoritative; memory_a is invalidated
  dismissed — both memories remain live (legitimate source disagreement)

Every resolution writes a conflict_resolved event to the audit chain.
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "0008_conflict_flags"
down_revision = "0007_billing"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "conflict_flags",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("namespace", sa.String(), nullable=False),
        sa.Column("agent_id", sa.String(), nullable=False),
        # memory_a = the pre-existing memory that was already live
        # memory_b = the newly ingested memory that triggered conflict detection
        sa.Column("memory_a_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("memory_b_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("confidence", sa.Float(), nullable=False),
        sa.Column(
            "detected_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        # open | accept_a | accept_b | dismissed
        sa.Column("status", sa.String(), server_default="open", nullable=False),
        sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("resolver_note", sa.Text(), nullable=True),
        sa.ForeignKeyConstraint(["memory_a_id"], ["memories.id"]),
        sa.ForeignKeyConstraint(["memory_b_id"], ["memories.id"]),
    )

    # Primary lookup: all open conflicts for a namespace
    op.create_index("ix_conflict_flags_namespace", "conflict_flags", ["namespace"])
    op.create_index(
        "ix_conflict_flags_ns_status",
        "conflict_flags",
        ["namespace", "status"],
    )
    # Fast lookup by individual memory ID (used in resolve path)
    op.create_index("ix_conflict_flags_memory_a", "conflict_flags", ["memory_a_id"])
    op.create_index("ix_conflict_flags_memory_b", "conflict_flags", ["memory_b_id"])


def downgrade() -> None:
    op.drop_index("ix_conflict_flags_memory_b", table_name="conflict_flags")
    op.drop_index("ix_conflict_flags_memory_a", table_name="conflict_flags")
    op.drop_index("ix_conflict_flags_ns_status", table_name="conflict_flags")
    op.drop_index("ix_conflict_flags_namespace", table_name="conflict_flags")
    op.drop_table("conflict_flags")
