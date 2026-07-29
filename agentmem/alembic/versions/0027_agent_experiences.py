"""Add governed agent experiences and reflection proposals.

Revision ID: 0027_agent_experiences
Revises: 0026_durable_jobs
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "0027_agent_experiences"
down_revision = "0026_durable_jobs"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "agent_experiences",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("namespace", sa.String(), nullable=False),
        sa.Column("agent_id", sa.String(), nullable=False),
        sa.Column("task", sa.Text(), nullable=False),
        sa.Column("task_key", sa.String(length=300), nullable=False),
        sa.Column("decision", postgresql.JSONB(), nullable=False),
        sa.Column(
            "context_memory_ids",
            postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
        sa.Column(
            "metadata",
            postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column("outcome", postgresql.JSONB(), nullable=True),
        sa.Column("reward", sa.Float(), nullable=True),
        sa.Column("reviewer_feedback", sa.Text(), nullable=True),
        sa.Column("status", sa.String(), nullable=False, server_default="open"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index(
        "ix_agent_experiences_ns_agent_created",
        "agent_experiences",
        ["namespace", "agent_id", "created_at"],
    )
    op.create_index("ix_agent_experiences_namespace", "agent_experiences", ["namespace"])
    op.create_index("ix_agent_experiences_agent_id", "agent_experiences", ["agent_id"])
    op.create_index("ix_agent_experiences_task_key", "agent_experiences", ["task_key"])
    op.create_index("ix_agent_experiences_status", "agent_experiences", ["status"])

    op.create_table(
        "reflection_proposals",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("namespace", sa.String(), nullable=False),
        sa.Column("agent_id", sa.String(), nullable=False),
        sa.Column("task_key", sa.String(length=300), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column(
            "supporting_experience_ids",
            postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
        sa.Column("confidence", sa.Float(), nullable=False),
        sa.Column("status", sa.String(), nullable=False, server_default="pending"),
        sa.Column("reviewer_note", sa.Text(), nullable=True),
        sa.Column(
            "promoted_memory_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("memories.id"),
            nullable=True,
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("reviewed_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index(
        "ix_reflection_proposals_ns_status",
        "reflection_proposals",
        ["namespace", "status", "created_at"],
    )
    op.create_index("ix_reflection_proposals_namespace", "reflection_proposals", ["namespace"])
    op.create_index("ix_reflection_proposals_agent_id", "reflection_proposals", ["agent_id"])
    op.create_index("ix_reflection_proposals_task_key", "reflection_proposals", ["task_key"])
    op.create_index("ix_reflection_proposals_status", "reflection_proposals", ["status"])
    op.create_index(
        "uq_reflection_pending_task",
        "reflection_proposals",
        ["namespace", "agent_id", "task_key"],
        unique=True,
        postgresql_where=sa.text("status = 'pending'"),
    )

    for table in ("agent_experiences", "reflection_proposals"):
        op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY")
        op.execute(
            f"""
            CREATE POLICY {table}_namespace_isolation ON {table}
            USING (
              current_setting('agentmem.namespace', true) = '__admin__'
              OR namespace = current_setting('agentmem.namespace', true)
            )
            WITH CHECK (
              current_setting('agentmem.namespace', true) = '__admin__'
              OR namespace = current_setting('agentmem.namespace', true)
            )
            """
        )


def downgrade() -> None:
    op.drop_table("reflection_proposals")
    op.drop_table("agent_experiences")
