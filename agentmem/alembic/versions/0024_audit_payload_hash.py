"""Version the audit hash format and protect canonical event payloads.

Historical rows are marked v1 and retain their existing hashes. New rows are
written as v2 by the application and include canonical JSON payload in the
row hash.

Revision ID: 0024_audit_payload_hash
Revises: 0023_partner_integrations
"""
from alembic import op
import sqlalchemy as sa


revision = "0024_audit_payload_hash"
down_revision = "0023_partner_integrations"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "event_log",
        sa.Column("hash_version", sa.Integer(), nullable=False, server_default="1"),
    )


def downgrade() -> None:
    op.drop_column("event_log", "hash_version")
