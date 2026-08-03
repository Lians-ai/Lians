"""Tenant-managed, expiring workload credential lifecycle.

Revision ID: 0035_workload_credentials
Revises: 0034_namespace_governance
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0035_workload_credentials"
down_revision = "0034_namespace_governance"
branch_labels = None
depends_on = None


def _uuid() -> sa.types.TypeEngine:
    return postgresql.UUID(as_uuid=True)


def upgrade() -> None:
    with op.batch_alter_table("api_keys") as batch:
        # Existing rows and credentials provisioned through X-Admin-Secret remain
        # explicitly identifiable as break-glass credentials. Tenant OIDC flows
        # always override both of these defaults.
        batch.add_column(
            sa.Column(
                "provisioning_source",
                sa.String(length=32),
                nullable=False,
                server_default="breakglass_admin",
            )
        )
        batch.add_column(
            sa.Column(
                "created_by",
                sa.String(length=512),
                nullable=False,
                server_default="breakglass_admin:legacy",
            )
        )
        batch.add_column(sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True))
        batch.add_column(sa.Column("last_used_at", sa.DateTime(timezone=True), nullable=True))
        batch.add_column(
            sa.Column(
                "rotated_from_id",
                _uuid(),
                nullable=True,
            )
        )
        batch.add_column(
            sa.Column("version", sa.Integer(), nullable=False, server_default="1")
        )
        batch.create_foreign_key(
            "fk_api_keys_rotated_from_id",
            "api_keys",
            ["rotated_from_id"],
            ["id"],
            ondelete="RESTRICT",
        )
        batch.create_check_constraint(
            "ck_api_key_provisioning_source",
            "provisioning_source IN ('breakglass_admin', 'tenant_oidc')",
        )
        batch.create_check_constraint("ck_api_key_version", "version >= 1")
        batch.create_check_constraint(
            "ck_api_key_tenant_expiry",
            "provisioning_source <> 'tenant_oidc' OR expires_at IS NOT NULL",
        )
        batch.create_check_constraint(
            "ck_api_key_expiry_after_creation",
            "expires_at IS NULL OR expires_at > created_at",
        )
        batch.create_check_constraint(
            "ck_api_key_last_use_after_creation",
            "last_used_at IS NULL OR last_used_at >= created_at",
        )
        batch.create_check_constraint(
            "ck_api_key_rotation_not_self",
            "rotated_from_id IS NULL OR rotated_from_id <> id",
        )

    op.create_index(
        "ix_api_keys_namespace_source_created",
        "api_keys",
        ["namespace", "provisioning_source", "created_at"],
    )
    op.create_index("ix_api_keys_expires_at", "api_keys", ["expires_at"])
    op.create_index(
        "uq_api_keys_rotated_from_id",
        "api_keys",
        ["rotated_from_id"],
        unique=True,
    )


def downgrade() -> None:
    op.drop_index("uq_api_keys_rotated_from_id", table_name="api_keys")
    op.drop_index("ix_api_keys_expires_at", table_name="api_keys")
    op.drop_index("ix_api_keys_namespace_source_created", table_name="api_keys")
    with op.batch_alter_table("api_keys") as batch:
        batch.drop_constraint("ck_api_key_rotation_not_self", type_="check")
        batch.drop_constraint("ck_api_key_last_use_after_creation", type_="check")
        batch.drop_constraint("ck_api_key_expiry_after_creation", type_="check")
        batch.drop_constraint("ck_api_key_tenant_expiry", type_="check")
        batch.drop_constraint("ck_api_key_version", type_="check")
        batch.drop_constraint("ck_api_key_provisioning_source", type_="check")
        batch.drop_constraint("fk_api_keys_rotated_from_id", type_="foreignkey")
        batch.drop_column("version")
        batch.drop_column("rotated_from_id")
        batch.drop_column("last_used_at")
        batch.drop_column("expires_at")
        batch.drop_column("created_by")
        batch.drop_column("provisioning_source")
