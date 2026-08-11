"""Add hosted workspace metadata and governed connector registry.

Revision ID: 0031_workspaces_connectors
Revises: 0030_force_hosted_mcp_rls
"""

import sqlalchemy as sa
from alembic import op

revision = "0031_workspaces_connectors"
down_revision = "0030_force_hosted_mcp_rls"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "workspaces",
        sa.Column("namespace", sa.String(), primary_key=True),
        sa.Column("display_name", sa.String(length=200), nullable=False),
        sa.Column("plan", sa.String(length=50), nullable=False, server_default="developer"),
        sa.Column("region", sa.String(length=100), nullable=True),
        sa.Column("settings", sa.JSON(), nullable=False, server_default="{}"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_table(
        "connectors",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("namespace", sa.String(), nullable=False),
        sa.Column("kind", sa.String(length=50), nullable=False),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("agent_id", sa.String(length=255), nullable=False),
        sa.Column("scope", sa.String(length=1000), nullable=True),
        sa.Column("status", sa.String(length=50), nullable=False, server_default="active"),
        sa.Column("config", sa.JSON(), nullable=False, server_default="{}"),
        sa.Column("cursor", sa.String(length=1000), nullable=True),
        sa.Column("last_sync_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_error", sa.String(length=500), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("namespace", "name", name="uq_connector_namespace_name"),
    )
    op.create_index("ix_connectors_namespace", "connectors", ["namespace"])
    op.create_index("ix_connectors_kind", "connectors", ["kind"])
    op.create_index("ix_connectors_agent_id", "connectors", ["agent_id"])
    op.create_index("ix_connectors_status", "connectors", ["status"])
    op.create_index(
        "ix_connectors_ns_kind_status", "connectors", ["namespace", "kind", "status"]
    )
    if op.get_bind().dialect.name == "postgresql":
        for table in ("workspaces", "connectors"):
            op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
            op.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY")
            op.execute(
                f"""
                CREATE POLICY rls_{table}_namespace ON {table}
                USING (
                  current_setting('app.current_namespace', true) = '__admin__'
                  OR namespace = current_setting('app.current_namespace', true)
                )
                WITH CHECK (
                  current_setting('app.current_namespace', true) = '__admin__'
                  OR namespace = current_setting('app.current_namespace', true)
                )
                """
            )


def downgrade() -> None:
    op.drop_table("connectors")
    op.drop_table("workspaces")
