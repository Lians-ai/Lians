"""Add OTLP span retention and ValidMind synchronization metadata.

Revision ID: 0023_partner_integrations
Revises: 0022_tenant_agent_keys
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "0023_partner_integrations"
down_revision = "0022_tenant_agent_keys"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "otel_spans",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("namespace", sa.String(), nullable=False),
        sa.Column("trace_id", sa.String(32), nullable=False),
        sa.Column("span_id", sa.String(16), nullable=False),
        sa.Column("parent_span_id", sa.String(16), nullable=True),
        sa.Column("name", sa.String(), nullable=False),
        sa.Column("kind", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("start_time_unix_nano", sa.String(), nullable=False),
        sa.Column("end_time_unix_nano", sa.String(), nullable=False),
        sa.Column("status_code", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("status_message", sa.String(), nullable=True),
        sa.Column("service_name", sa.String(), nullable=True),
        sa.Column("scope_name", sa.String(), nullable=True),
        sa.Column("scope_version", sa.String(), nullable=True),
        sa.Column("resource_attributes", sa.JSON(), nullable=False, server_default="{}"),
        sa.Column("attributes", sa.JSON(), nullable=False, server_default="{}"),
        sa.Column("events", sa.JSON(), nullable=False, server_default="[]"),
        sa.Column("links", sa.JSON(), nullable=False, server_default="[]"),
        sa.Column("is_genai", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("model_id", sa.String(), nullable=True),
        sa.Column("model_version", sa.String(), nullable=True),
        sa.Column("payload_hash", sa.String(64), nullable=False),
        sa.Column("received_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_otel_spans_namespace", "otel_spans", ["namespace"])
    op.create_index("ix_otel_spans_is_genai", "otel_spans", ["is_genai"])
    op.create_index("ix_otel_spans_model_id", "otel_spans", ["model_id"])
    op.create_index("ix_otel_spans_service_name", "otel_spans", ["service_name"])
    op.create_index("ix_otel_spans_received_at", "otel_spans", ["received_at"])
    op.create_index(
        "uq_otel_span_ns_trace_span",
        "otel_spans",
        ["namespace", "trace_id", "span_id"],
        unique=True,
    )
    op.create_index(
        "ix_otel_span_ns_received", "otel_spans", ["namespace", "received_at"]
    )
    op.create_table(
        "validmind_model_links",
        sa.Column("namespace", sa.String(), primary_key=True),
        sa.Column("external_id", sa.String(), primary_key=True),
        sa.Column("vm_cuid", sa.String(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("validmind_model_links")
    op.drop_table("otel_spans")
