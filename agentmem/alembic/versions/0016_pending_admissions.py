"""pending_admissions table for memory admission control

Revision ID: 0016_pending_admissions
Revises: 0015_apikey_role
Create Date: 2026-06-30

High-risk memory writes (PII/PHI/MNPI) held for human review in admission
enforce mode are parked here until an admin approves or rejects them.
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID, JSONB


revision = "0016_pending_admissions"
down_revision = "0015_apikey_role"
branch_labels = None
depends_on = None


def upgrade() -> None:
    is_pg = op.get_bind().dialect.name == "postgresql"
    json_type = JSONB if is_pg else sa.JSON

    op.create_table(
        "pending_admissions",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("namespace", sa.String(), nullable=False),
        sa.Column("agent_id", sa.String(), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("event_time", sa.DateTime(timezone=True), nullable=False),
        sa.Column("source", sa.String(), nullable=True),
        sa.Column("subject_id", sa.String(), nullable=True),
        sa.Column("metadata", json_type, nullable=False, server_default="{}"),
        sa.Column("importance", sa.Float(), nullable=False, server_default="0.5"),
        sa.Column("risk_tags", json_type, nullable=False, server_default="[]"),
        sa.Column("reasons", json_type, nullable=False, server_default="[]"),
        sa.Column("status", sa.String(), nullable=False, server_default="pending"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("resolver_note", sa.Text(), nullable=True),
        sa.Column("memory_id", UUID(as_uuid=True), nullable=True),
    )
    op.create_index("ix_pending_admissions_namespace", "pending_admissions", ["namespace"])
    op.create_index("ix_pending_admissions_status", "pending_admissions", ["status"])


def downgrade() -> None:
    op.drop_index("ix_pending_admissions_status", table_name="pending_admissions")
    op.drop_index("ix_pending_admissions_namespace", table_name="pending_admissions")
    op.drop_table("pending_admissions")
