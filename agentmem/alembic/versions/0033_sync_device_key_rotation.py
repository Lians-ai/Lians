"""Add signed device revocation and workspace-key rotation evidence.

Revision ID: 0033_sync_device_key_rotation
Revises: 0032_device_enrollment_exchange
"""

import sqlalchemy as sa
from alembic import op

revision = "0033_sync_device_key_rotation"
down_revision = "0032_device_enrollment_exchange"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "sync_key_rotations",
        sa.Column(
            "workspace_id",
            sa.String(length=36),
            sa.ForeignKey("sync_workspaces.workspace_id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("epoch", sa.Integer(), primary_key=True),
        sa.Column("rotation_id", sa.String(length=36), nullable=False, unique=True),
        sa.Column("namespace", sa.String(), nullable=False),
        sa.Column("revoked_device_id", sa.String(length=64), nullable=False),
        sa.Column("initiator_device_id", sa.String(length=64), nullable=False),
        sa.Column("document", sa.JSON(), nullable=False),
        sa.Column("signature", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index(
        "ix_sync_key_rotations_namespace",
        "sync_key_rotations",
        ["namespace"],
    )
    op.create_index(
        "ix_sync_key_rotations_namespace_workspace",
        "sync_key_rotations",
        ["namespace", "workspace_id", "epoch"],
    )
    if op.get_bind().dialect.name == "postgresql":
        op.execute("ALTER TABLE sync_key_rotations ENABLE ROW LEVEL SECURITY")
        op.execute("ALTER TABLE sync_key_rotations FORCE ROW LEVEL SECURITY")
        op.execute(
            """
            CREATE POLICY rls_sync_key_rotations_namespace ON sync_key_rotations
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
            "DROP POLICY IF EXISTS rls_sync_key_rotations_namespace ON sync_key_rotations"
        )
        op.execute("ALTER TABLE sync_key_rotations NO FORCE ROW LEVEL SECURITY")
        op.execute("ALTER TABLE sync_key_rotations DISABLE ROW LEVEL SECURITY")
    op.drop_index(
        "ix_sync_key_rotations_namespace_workspace",
        table_name="sync_key_rotations",
    )
    op.drop_index(
        "ix_sync_key_rotations_namespace",
        table_name="sync_key_rotations",
    )
    op.drop_table("sync_key_rotations")
