"""idempotency_keys table for exactly-once writes

Revision ID: 0014_idempotency_keys
Revises: 0013_restrictive_barriers
Create Date: 2026-06-29

Maps a client-supplied Idempotency-Key (scoped to a namespace) to the memory it
created. A retried POST /v1/memories carrying the same key returns the original
result instead of inserting a duplicate.
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID


revision = "0014_idempotency_keys"
down_revision = "0013_restrictive_barriers"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "idempotency_keys",
        sa.Column("key", sa.String(), primary_key=True),
        sa.Column("namespace", sa.String(), primary_key=True),
        sa.Column("memory_id", UUID(as_uuid=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_idempotency_keys_created_at", "idempotency_keys", ["created_at"])


def downgrade() -> None:
    op.drop_index("ix_idempotency_keys_created_at", table_name="idempotency_keys")
    op.drop_table("idempotency_keys")
