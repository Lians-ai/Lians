"""Add opaque zero-knowledge workspace synchronization.

Revision ID: 0031_zero_knowledge_sync
Revises: 0030_force_hosted_mcp_rls
"""

import sqlalchemy as sa
from alembic import op

revision = "0031_zero_knowledge_sync"
down_revision = "0030_force_hosted_mcp_rls"
branch_labels = None
depends_on = None


_TABLES = ("sync_workspaces", "sync_devices", "sync_revisions")


def _rls(table: str) -> None:
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


def upgrade() -> None:
    op.create_table(
        "sync_workspaces",
        sa.Column("workspace_id", sa.String(length=36), primary_key=True),
        sa.Column("namespace", sa.String(), nullable=False),
        sa.Column("epoch", sa.Integer(), nullable=False),
        sa.Column("root_device", sa.JSON(), nullable=False),
        sa.Column("head_revision", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("head_hash", sa.String(length=64), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_sync_workspaces_namespace", "sync_workspaces", ["namespace"])
    op.create_index(
        "ix_sync_workspaces_namespace_created",
        "sync_workspaces",
        ["namespace", "created_at"],
    )
    op.create_table(
        "sync_devices",
        sa.Column(
            "workspace_id",
            sa.String(length=36),
            sa.ForeignKey("sync_workspaces.workspace_id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("device_id", sa.String(length=64), primary_key=True),
        sa.Column("namespace", sa.String(), nullable=False),
        sa.Column("descriptor", sa.JSON(), nullable=False),
        sa.Column("grant", sa.JSON(), nullable=True),
        sa.Column("grant_signature", sa.JSON(), nullable=True),
        sa.Column("enrolled_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_sync_devices_namespace", "sync_devices", ["namespace"])
    op.create_index(
        "ix_sync_devices_namespace_workspace",
        "sync_devices",
        ["namespace", "workspace_id"],
    )
    op.create_table(
        "sync_revisions",
        sa.Column(
            "workspace_id",
            sa.String(length=36),
            sa.ForeignKey("sync_workspaces.workspace_id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("revision", sa.Integer(), primary_key=True),
        sa.Column("namespace", sa.String(), nullable=False),
        sa.Column("device_id", sa.String(length=64), nullable=False),
        sa.Column("previous_hash", sa.String(length=64), nullable=True),
        sa.Column("object_hash", sa.String(length=64), nullable=False),
        sa.Column("envelope", sa.JSON(), nullable=False),
        sa.Column("authored_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("stored_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint(
            "workspace_id", "object_hash", name="uq_sync_revision_object_hash"
        ),
    )
    op.create_index("ix_sync_revisions_namespace", "sync_revisions", ["namespace"])
    op.create_index(
        "ix_sync_revisions_namespace_workspace",
        "sync_revisions",
        ["namespace", "workspace_id", "revision"],
    )
    if op.get_bind().dialect.name == "postgresql":
        for table in _TABLES:
            _rls(table)


def downgrade() -> None:
    if op.get_bind().dialect.name == "postgresql":
        for table in reversed(_TABLES):
            op.execute(f"DROP POLICY IF EXISTS rls_{table}_namespace ON {table}")
            op.execute(f"ALTER TABLE {table} NO FORCE ROW LEVEL SECURITY")
            op.execute(f"ALTER TABLE {table} DISABLE ROW LEVEL SECURITY")
    op.drop_index("ix_sync_revisions_namespace_workspace", table_name="sync_revisions")
    op.drop_index("ix_sync_revisions_namespace", table_name="sync_revisions")
    op.drop_table("sync_revisions")
    op.drop_index("ix_sync_devices_namespace_workspace", table_name="sync_devices")
    op.drop_index("ix_sync_devices_namespace", table_name="sync_devices")
    op.drop_table("sync_devices")
    op.drop_index("ix_sync_workspaces_namespace_created", table_name="sync_workspaces")
    op.drop_index("ix_sync_workspaces_namespace", table_name="sync_workspaces")
    op.drop_table("sync_workspaces")
