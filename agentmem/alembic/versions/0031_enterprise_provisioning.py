"""Add tenant-scoped SCIM 2.0 enterprise provisioning.

Revision ID: 0031_enterprise_provisioning
Revises: 0030_identity_federation
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql


revision = "0031_enterprise_provisioning"
down_revision = "0030_identity_federation"
branch_labels = None
depends_on = None


def _uuid() -> sa.types.TypeEngine:
    return postgresql.UUID(as_uuid=True)


def upgrade() -> None:
    op.create_table(
        "scim_tenant_configs",
        sa.Column("id", _uuid(), primary_key=True),
        sa.Column("namespace", sa.String(length=255), nullable=False),
        sa.Column(
            "provider_id",
            _uuid(),
            sa.ForeignKey("trusted_identity_providers.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column(
            "subject_attribute",
            sa.String(length=32),
            nullable=False,
            server_default="externalId",
        ),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.UniqueConstraint("namespace", name="uq_scim_tenant_config_namespace"),
        sa.UniqueConstraint("id", "namespace", name="uq_scim_tenant_id_namespace"),
        sa.CheckConstraint(
            "subject_attribute IN ('externalId', 'userName')",
            name="ck_scim_tenant_subject_attribute",
        ),
    )
    op.create_index("ix_scim_tenant_provider", "scim_tenant_configs", ["provider_id"])
    op.create_index(
        "ix_scim_tenant_active", "scim_tenant_configs", ["enabled", "revoked_at"]
    )

    op.create_table(
        "scim_bearer_credentials",
        sa.Column("id", _uuid(), primary_key=True),
        sa.Column("tenant_config_id", _uuid(), nullable=False),
        sa.Column("namespace", sa.String(length=255), nullable=False),
        sa.Column("token_hash", sa.String(length=64), nullable=False),
        sa.Column("token_hint", sa.String(length=24), nullable=False),
        sa.Column("label", sa.String(length=200), nullable=True),
        sa.Column(
            "rotated_from_id",
            _uuid(),
            sa.ForeignKey("scim_bearer_credentials.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "replaced_by_id",
            _uuid(),
            sa.ForeignKey("scim_bearer_credentials.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(
            ["tenant_config_id", "namespace"],
            ["scim_tenant_configs.id", "scim_tenant_configs.namespace"],
            ondelete="RESTRICT",
            name="fk_scim_credential_tenant_namespace",
        ),
        sa.UniqueConstraint("token_hash", name="uq_scim_bearer_token_hash"),
        sa.CheckConstraint("length(token_hash) = 64", name="ck_scim_token_hash_length"),
        sa.CheckConstraint(
            "expires_at IS NULL OR expires_at > created_at",
            name="ck_scim_credential_expiry",
        ),
    )
    op.create_index(
        "ix_scim_credential_tenant", "scim_bearer_credentials", ["tenant_config_id"]
    )
    op.create_index(
        "ix_scim_credential_namespace", "scim_bearer_credentials", ["namespace"]
    )
    op.create_index(
        "ix_scim_credential_active",
        "scim_bearer_credentials",
        ["tenant_config_id", "revoked_at", "expires_at"],
    )

    op.create_table(
        "scim_users",
        sa.Column("id", _uuid(), primary_key=True),
        sa.Column("tenant_config_id", _uuid(), nullable=False),
        sa.Column("namespace", sa.String(length=255), nullable=False),
        sa.Column("external_id", sa.String(length=512), nullable=True),
        sa.Column("user_name", sa.String(length=512), nullable=False),
        sa.Column("display_name", sa.String(length=512), nullable=True),
        sa.Column("name", sa.JSON(), nullable=False, server_default="{}"),
        sa.Column("emails", sa.JSON(), nullable=False, server_default="[]"),
        sa.Column("active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column(
            "identity_binding_id",
            _uuid(),
            sa.ForeignKey("identity_bindings.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(
            ["tenant_config_id", "namespace"],
            ["scim_tenant_configs.id", "scim_tenant_configs.namespace"],
            ondelete="RESTRICT",
            name="fk_scim_user_tenant_namespace",
        ),
        sa.UniqueConstraint(
            "id", "tenant_config_id", "namespace", name="uq_scim_user_tenant_identity"
        ),
        sa.UniqueConstraint(
            "tenant_config_id", "user_name", name="uq_scim_user_tenant_username"
        ),
        sa.UniqueConstraint(
            "tenant_config_id", "external_id", name="uq_scim_user_tenant_external_id"
        ),
        sa.UniqueConstraint("identity_binding_id", name="uq_scim_user_identity_binding"),
    )
    op.create_index("ix_scim_user_namespace", "scim_users", ["namespace"])
    op.create_index(
        "ix_scim_user_tenant_active",
        "scim_users",
        ["tenant_config_id", "active", "deleted_at"],
    )
    op.create_index("ix_scim_user_binding", "scim_users", ["identity_binding_id"])

    op.create_table(
        "scim_groups",
        sa.Column("id", _uuid(), primary_key=True),
        sa.Column("tenant_config_id", _uuid(), nullable=False),
        sa.Column("namespace", sa.String(length=255), nullable=False),
        sa.Column("external_id", sa.String(length=512), nullable=True),
        sa.Column("display_name", sa.String(length=512), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(
            ["tenant_config_id", "namespace"],
            ["scim_tenant_configs.id", "scim_tenant_configs.namespace"],
            ondelete="RESTRICT",
            name="fk_scim_group_tenant_namespace",
        ),
        sa.UniqueConstraint(
            "id", "tenant_config_id", "namespace", name="uq_scim_group_tenant_identity"
        ),
        sa.UniqueConstraint(
            "tenant_config_id", "display_name", name="uq_scim_group_tenant_display_name"
        ),
        sa.UniqueConstraint(
            "tenant_config_id", "external_id", name="uq_scim_group_tenant_external_id"
        ),
    )
    op.create_index("ix_scim_group_namespace", "scim_groups", ["namespace"])
    op.create_index(
        "ix_scim_group_tenant_active", "scim_groups", ["tenant_config_id", "deleted_at"]
    )

    op.create_table(
        "scim_group_members",
        sa.Column("group_id", _uuid(), primary_key=True),
        sa.Column("user_id", _uuid(), primary_key=True),
        sa.Column("tenant_config_id", _uuid(), nullable=False),
        sa.Column("namespace", sa.String(length=255), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.ForeignKeyConstraint(
            ["tenant_config_id", "namespace"],
            ["scim_tenant_configs.id", "scim_tenant_configs.namespace"],
            ondelete="CASCADE",
            name="fk_scim_member_tenant_namespace",
        ),
        sa.ForeignKeyConstraint(
            ["group_id", "tenant_config_id", "namespace"],
            ["scim_groups.id", "scim_groups.tenant_config_id", "scim_groups.namespace"],
            ondelete="CASCADE",
            name="fk_scim_member_group_tenant",
        ),
        sa.ForeignKeyConstraint(
            ["user_id", "tenant_config_id", "namespace"],
            ["scim_users.id", "scim_users.tenant_config_id", "scim_users.namespace"],
            ondelete="CASCADE",
            name="fk_scim_member_user_tenant",
        ),
    )
    op.create_index("ix_scim_membership_user", "scim_group_members", ["user_id"])
    op.create_index(
        "ix_scim_membership_tenant", "scim_group_members", ["tenant_config_id"]
    )
    op.create_index("ix_scim_membership_namespace", "scim_group_members", ["namespace"])

    op.create_table(
        "scim_group_entitlements",
        sa.Column("id", _uuid(), primary_key=True),
        sa.Column("tenant_config_id", _uuid(), nullable=False),
        sa.Column("namespace", sa.String(length=255), nullable=False),
        sa.Column("group_id", _uuid(), nullable=False),
        sa.Column("role", sa.String(length=64), nullable=True),
        sa.Column("scopes", sa.JSON(), nullable=False, server_default="[]"),
        sa.Column("barrier_group", sa.String(length=255), nullable=True),
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.ForeignKeyConstraint(
            ["tenant_config_id", "namespace"],
            ["scim_tenant_configs.id", "scim_tenant_configs.namespace"],
            ondelete="CASCADE",
            name="fk_scim_entitlement_tenant_namespace",
        ),
        sa.ForeignKeyConstraint(
            ["group_id", "tenant_config_id", "namespace"],
            ["scim_groups.id", "scim_groups.tenant_config_id", "scim_groups.namespace"],
            ondelete="CASCADE",
            name="fk_scim_entitlement_group_tenant",
        ),
        sa.UniqueConstraint("group_id", name="uq_scim_group_entitlement_group"),
        sa.CheckConstraint(
            "role IS NULL OR role IN ('owner', 'analyst', 'compliance', 'readonly')",
            name="ck_scim_entitlement_role",
        ),
    )
    op.create_index(
        "ix_scim_entitlement_tenant", "scim_group_entitlements", ["tenant_config_id"]
    )
    op.create_index(
        "ix_scim_entitlement_namespace", "scim_group_entitlements", ["namespace"]
    )

    if op.get_bind().dialect.name != "postgresql":
        return
    tables = (
        "scim_tenant_configs",
        "scim_bearer_credentials",
        "scim_users",
        "scim_groups",
        "scim_group_members",
        "scim_group_entitlements",
    )
    for table in tables:
        op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY")
        op.execute(
            f"""CREATE POLICY rls_{table}_namespace ON {table}
            USING (
                namespace = current_setting('app.current_namespace', true)
                OR current_setting('app.current_namespace', true) = '__admin__'
            )
            WITH CHECK (
                namespace = current_setting('app.current_namespace', true)
                OR current_setting('app.current_namespace', true) = '__admin__'
            )"""
        )


def downgrade() -> None:
    tables = (
        "scim_group_entitlements",
        "scim_group_members",
        "scim_groups",
        "scim_users",
        "scim_bearer_credentials",
        "scim_tenant_configs",
    )
    if op.get_bind().dialect.name == "postgresql":
        for table in tables:
            op.execute(f"DROP POLICY IF EXISTS rls_{table}_namespace ON {table}")
            op.execute(f"ALTER TABLE {table} NO FORCE ROW LEVEL SECURITY")
            op.execute(f"ALTER TABLE {table} DISABLE ROW LEVEL SECURITY")
    for table in tables:
        op.drop_table(table)
