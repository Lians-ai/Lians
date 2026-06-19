"""Add namespace_policies table for retention and compliance configuration

Enables per-namespace content TTL and audit retention settings required by
SEC 17a-4 and CFTC swap dealer regulations (minimum 5-year retention).

A legal_hold flag blocks any automated pruning when True (litigation hold).

Changes:
  1. namespace_policies table — one row per namespace controlling:
     - content_ttl_days: prune memory content after N days (NULL = forever)
     - audit_retention_days: minimum event_log retention (default 1825 / 5yr)
     - legal_hold: block all automated pruning when True

  2. RLS on namespace_policies (PostgreSQL only) — admin-only bypass, same
     pattern as other tables.

Revision ID: 0005_retention_policy
Revises: 0004_barriers_rls
Create Date: 2026-06-18
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa

revision = "0005_retention_policy"
down_revision = "0004_barriers_rls"
branch_labels = None
depends_on = None


def _is_postgres() -> bool:
    return op.get_bind().dialect.name == "postgresql"


def upgrade() -> None:
    op.create_table(
        "namespace_policies",
        sa.Column("namespace", sa.String(), primary_key=True),
        sa.Column("content_ttl_days", sa.Integer(), nullable=True),
        sa.Column("audit_retention_days", sa.Integer(), nullable=False, server_default="1825"),
        sa.Column("legal_hold", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
    )

    if not _is_postgres():
        return

    op.execute(sa.text("ALTER TABLE namespace_policies ENABLE ROW LEVEL SECURITY"))
    op.execute(sa.text("""
        CREATE POLICY rls_namespace_policies ON namespace_policies
        USING (
            namespace = current_setting('app.current_namespace', true)
            OR current_setting('app.current_namespace', true) = '__admin__'
        )
        WITH CHECK (
            namespace = current_setting('app.current_namespace', true)
            OR current_setting('app.current_namespace', true) = '__admin__'
        )
    """))


def downgrade() -> None:
    if _is_postgres():
        op.execute(sa.text(
            "DROP POLICY IF EXISTS rls_namespace_policies ON namespace_policies"
        ))
        op.execute(sa.text("ALTER TABLE namespace_policies DISABLE ROW LEVEL SECURITY"))

    op.drop_table("namespace_policies")
