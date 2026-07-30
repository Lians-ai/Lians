"""Add crash-safe durable job outbox.

Revision ID: 0026_durable_jobs
Revises: 0025_memory_feedback
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "0026_durable_jobs"
down_revision = "0025_memory_feedback"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "durable_jobs",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("namespace", sa.String(), nullable=False),
        sa.Column("kind", sa.String(), nullable=False),
        sa.Column("payload", postgresql.JSONB(), nullable=False, server_default="{}"),
        sa.Column("dedupe_key", sa.String(), nullable=True),
        sa.Column("status", sa.String(), nullable=False, server_default="pending"),
        sa.Column("attempts", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("max_attempts", sa.Integer(), nullable=False, server_default="8"),
        sa.Column("available_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("lease_until", sa.DateTime(timezone=True), nullable=True),
        sa.Column("leased_by", sa.String(), nullable=True),
        sa.Column("last_error", sa.String(500), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.UniqueConstraint(
            "namespace", "kind", "dedupe_key", name="uq_durable_job_dedupe"
        ),
    )
    op.create_index("ix_durable_jobs_namespace", "durable_jobs", ["namespace"])
    op.create_index("ix_durable_jobs_kind", "durable_jobs", ["kind"])
    op.create_index("ix_durable_jobs_status", "durable_jobs", ["status"])
    op.create_index(
        "ix_durable_jobs_claim",
        "durable_jobs",
        ["status", "available_at", "lease_until"],
    )
    op.execute("ALTER TABLE durable_jobs ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE durable_jobs FORCE ROW LEVEL SECURITY")
    op.execute(
        """
        CREATE POLICY durable_jobs_namespace_isolation ON durable_jobs
        USING (
            namespace = current_setting('app.current_namespace', true)
            OR current_setting('app.current_namespace', true) = '__admin__'
        )
        WITH CHECK (
            namespace = current_setting('app.current_namespace', true)
            OR current_setting('app.current_namespace', true) = '__admin__'
        )
        """
    )


def downgrade() -> None:
    op.drop_table("durable_jobs")
