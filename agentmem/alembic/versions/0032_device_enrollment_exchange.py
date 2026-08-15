"""Add short-lived zero-knowledge device enrollment exchange.

Revision ID: 0032_device_enrollment_exchange
Revises: 0031_zero_knowledge_sync
"""

import sqlalchemy as sa
from alembic import op

revision = "0032_device_enrollment_exchange"
down_revision = "0031_zero_knowledge_sync"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "sync_enrollments",
        sa.Column("request_id", sa.String(length=36), primary_key=True),
        sa.Column("namespace", sa.String(), nullable=False),
        sa.Column("device_id", sa.String(length=64), nullable=False),
        sa.Column("device_name", sa.String(length=80), nullable=False),
        sa.Column("verification_code", sa.String(length=9), nullable=False),
        sa.Column("request", sa.JSON(), nullable=False),
        sa.Column("approval", sa.JSON(), nullable=True),
        sa.Column(
            "workspace_id",
            sa.String(length=36),
            sa.ForeignKey("sync_workspaces.workspace_id", ondelete="CASCADE"),
            nullable=True,
        ),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("approved_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_sync_enrollments_namespace", "sync_enrollments", ["namespace"])
    op.create_index(
        "ix_sync_enrollments_namespace_expires",
        "sync_enrollments",
        ["namespace", "expires_at"],
    )
    op.create_index(
        "ix_sync_enrollments_namespace_workspace",
        "sync_enrollments",
        ["namespace", "workspace_id"],
    )
    if op.get_bind().dialect.name == "postgresql":
        op.execute("ALTER TABLE sync_enrollments ENABLE ROW LEVEL SECURITY")
        op.execute("ALTER TABLE sync_enrollments FORCE ROW LEVEL SECURITY")
        op.execute(
            """
            CREATE POLICY rls_sync_enrollments_namespace ON sync_enrollments
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
    if op.get_bind().dialect.name == "postgresql":
        op.execute(
            "DROP POLICY IF EXISTS rls_sync_enrollments_namespace ON sync_enrollments"
        )
        op.execute("ALTER TABLE sync_enrollments NO FORCE ROW LEVEL SECURITY")
        op.execute("ALTER TABLE sync_enrollments DISABLE ROW LEVEL SECURITY")
    op.drop_index(
        "ix_sync_enrollments_namespace_workspace", table_name="sync_enrollments"
    )
    op.drop_index(
        "ix_sync_enrollments_namespace_expires", table_name="sync_enrollments"
    )
    op.drop_index("ix_sync_enrollments_namespace", table_name="sync_enrollments")
    op.drop_table("sync_enrollments")
