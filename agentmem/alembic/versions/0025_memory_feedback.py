"""Add persistent memory feedback and learning signals.

Revision ID: 0025_memory_feedback
Revises: 0024_audit_payload_hash
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "0025_memory_feedback"
down_revision = "0024_audit_payload_hash"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "memory_feedback",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("namespace", sa.String(), nullable=False),
        sa.Column("agent_id", sa.String(), nullable=False),
        sa.Column(
            "memory_id", postgresql.UUID(as_uuid=True),
            sa.ForeignKey("memories.id"), nullable=False,
        ),
        sa.Column("signal", sa.String(), nullable=False),
        sa.Column("weight", sa.Float(), nullable=False, server_default="1.0"),
        sa.Column("outcome", sa.String(), nullable=True),
        sa.Column("query_hash", sa.String(64), nullable=True),
        sa.Column("source", sa.String(), nullable=True),
        sa.Column("note", sa.Text(), nullable=True),
        sa.Column("policy_action", sa.String(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    for name, cols in (
        ("ix_memory_feedback_namespace", ["namespace"]),
        ("ix_memory_feedback_agent_id", ["agent_id"]),
        ("ix_memory_feedback_memory_id", ["memory_id"]),
        ("ix_memory_feedback_signal", ["signal"]),
        ("ix_memory_feedback_outcome", ["outcome"]),
        ("ix_memory_feedback_ns_memory", ["namespace", "memory_id"]),
        ("ix_memory_feedback_ns_created", ["namespace", "created_at"]),
    ):
        op.create_index(name, "memory_feedback", cols)


def downgrade() -> None:
    op.drop_table("memory_feedback")
