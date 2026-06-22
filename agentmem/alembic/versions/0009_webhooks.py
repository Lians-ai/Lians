"""Add webhook_endpoints and webhook_deliveries tables

Revision ID: 0009_webhooks
Revises: 0008_conflict_flags
Create Date: 2026-06-21

webhook_endpoints  — registered receiver URLs per namespace with HMAC secret
webhook_deliveries — delivery attempt log (status_code, retry count, errors)

Supported event types:
  memory.superseded       — a memory was invalidated by a newer fact
  memory.conflict         — a same-time contradiction was detected
  memory.erased           — a subject's DEK was destroyed (GDPR Art. 17)
  supersession.rejected   — a human reviewer rejected a supersession
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "0009_webhooks"
down_revision = "0008_conflict_flags"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "webhook_endpoints",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("namespace", sa.String(), nullable=False),
        sa.Column("url", sa.Text(), nullable=False),
        sa.Column("secret", sa.String(), nullable=False),
        sa.Column("events", postgresql.JSONB(), nullable=False),
        sa.Column("enabled", sa.Boolean(), server_default="true", nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column("description", sa.Text(), nullable=True),
    )
    op.create_index("ix_webhook_endpoints_namespace", "webhook_endpoints", ["namespace"])
    op.create_index(
        "ix_webhook_endpoints_ns_enabled",
        "webhook_endpoints",
        ["namespace", "enabled"],
    )

    op.create_table(
        "webhook_deliveries",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "endpoint_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("webhook_endpoints.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("event_type", sa.String(), nullable=False),
        sa.Column("payload", postgresql.JSONB(), nullable=False),
        sa.Column("attempt", sa.Integer(), server_default="1", nullable=False),
        sa.Column("status_code", sa.Integer(), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("delivered_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
    )
    op.create_index(
        "ix_webhook_deliveries_endpoint_id",
        "webhook_deliveries",
        ["endpoint_id"],
    )
    op.create_index(
        "ix_webhook_deliveries_endpoint_created",
        "webhook_deliveries",
        ["endpoint_id", "created_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_webhook_deliveries_endpoint_created", table_name="webhook_deliveries")
    op.drop_index("ix_webhook_deliveries_endpoint_id", table_name="webhook_deliveries")
    op.drop_table("webhook_deliveries")
    op.drop_index("ix_webhook_endpoints_ns_enabled", table_name="webhook_endpoints")
    op.drop_index("ix_webhook_endpoints_namespace", table_name="webhook_endpoints")
    op.drop_table("webhook_endpoints")
