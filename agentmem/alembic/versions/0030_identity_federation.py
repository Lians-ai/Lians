"""Add native OIDC human and workload identity federation.

Revision ID: 0030_identity_federation
Revises: 0029_audit_chain_serialization
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "0030_identity_federation"
down_revision = "0029_audit_chain_serialization"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "trusted_identity_providers",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("issuer", sa.String(length=2048), nullable=False),
        sa.Column("jwks_uri", sa.String(length=2048), nullable=False),
        sa.Column("audiences", sa.JSON(), nullable=False),
        sa.Column("allowed_algorithms", sa.JSON(), nullable=False),
        sa.Column("required_claims", sa.JSON(), nullable=False),
        sa.Column("required_typ", sa.String(length=100), nullable=True),
        sa.Column("clock_skew_seconds", sa.Integer(), nullable=False),
        sa.Column("max_token_age_seconds", sa.Integer(), nullable=False),
        sa.Column("jwks_cache_seconds", sa.Integer(), nullable=False),
        sa.Column(
            "allow_private_network",
            sa.Boolean(),
            nullable=False,
            server_default=sa.false(),
        ),
        sa.Column(
            "allow_insecure_http",
            sa.Boolean(),
            nullable=False,
            server_default=sa.false(),
        ),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.UniqueConstraint("issuer", name="uq_trusted_identity_provider_issuer"),
        sa.CheckConstraint("version >= 1", name="ck_identity_provider_version"),
        sa.CheckConstraint(
            "clock_skew_seconds >= 0 AND clock_skew_seconds <= 300",
            name="ck_identity_provider_clock_skew",
        ),
        sa.CheckConstraint(
            "max_token_age_seconds >= 30 AND max_token_age_seconds <= 86400",
            name="ck_identity_provider_token_age",
        ),
    )
    op.create_index(
        "ix_trusted_identity_provider_enabled",
        "trusted_identity_providers",
        ["enabled", "revoked_at"],
    )

    op.create_table(
        "identity_bindings",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "provider_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("trusted_identity_providers.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("external_subject", sa.String(length=512), nullable=False),
        sa.Column("namespace", sa.String(length=255), nullable=False),
        sa.Column("principal_type", sa.String(length=32), nullable=False),
        sa.Column("display_name", sa.String(length=255), nullable=True),
        sa.Column("role", sa.String(length=64), nullable=True),
        sa.Column("scopes", sa.JSON(), nullable=False),
        sa.Column("barrier_group", sa.String(length=255), nullable=True),
        sa.Column("authorized_party", sa.String(length=512), nullable=True),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.UniqueConstraint(
            "provider_id",
            "external_subject",
            name="uq_identity_binding_provider_subject",
        ),
        sa.CheckConstraint(
            "principal_type IN ('human','workload')",
            name="ck_identity_binding_principal_type",
        ),
        sa.CheckConstraint(
            "role IS NULL OR role IN ('owner','analyst','compliance','readonly')",
            name="ck_identity_binding_role",
        ),
        sa.CheckConstraint("version >= 1", name="ck_identity_binding_version"),
    )
    op.create_index("ix_identity_binding_namespace", "identity_bindings", ["namespace"])
    op.create_index(
        "ix_identity_binding_active",
        "identity_bindings",
        ["provider_id", "enabled", "revoked_at"],
    )


def downgrade() -> None:
    op.drop_table("identity_bindings")
    op.drop_table("trusted_identity_providers")
