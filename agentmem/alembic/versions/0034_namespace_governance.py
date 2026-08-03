"""Enforceable namespace residency, capture policy, and atomic daily quotas.

Revision ID: 0034_namespace_governance
Revises: 0033_integration_outbox
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0034_namespace_governance"
down_revision = "0033_integration_outbox"
branch_labels = None
depends_on = None


def _uuid() -> sa.types.TypeEngine:
    return postgresql.UUID(as_uuid=True)


def upgrade() -> None:
    with op.batch_alter_table("namespace_policies") as batch:
        batch.add_column(
            sa.Column(
                "governance_status",
                sa.String(length=32),
                nullable=False,
                server_default="unconfigured",
            )
        )
        batch.add_column(sa.Column("allowed_processing_regions", sa.JSON(), nullable=True))
        batch.add_column(sa.Column("allowed_recorder_capture_modes", sa.JSON(), nullable=True))
        batch.add_column(sa.Column("recorder_events_daily_limit", sa.BigInteger(), nullable=True))
        batch.add_column(sa.Column("decision_records_daily_limit", sa.BigInteger(), nullable=True))
        batch.add_column(sa.Column("memory_writes_daily_limit", sa.BigInteger(), nullable=True))
        batch.add_column(
            sa.Column(
                "estimated_ingest_bytes_daily_limit",
                sa.BigInteger(),
                nullable=True,
            )
        )
        batch.add_column(
            sa.Column("policy_version", sa.BigInteger(), nullable=False, server_default="0")
        )
        batch.add_column(
            sa.Column("governance_created_at", sa.DateTime(timezone=True), nullable=True)
        )
        batch.add_column(sa.Column("governance_created_by", sa.String(length=512), nullable=True))
        batch.add_column(
            sa.Column("governance_updated_at", sa.DateTime(timezone=True), nullable=True)
        )
        batch.add_column(sa.Column("governance_updated_by", sa.String(length=512), nullable=True))
        batch.create_check_constraint(
            "ck_namespace_policy_governance_status",
            "governance_status IN ('unconfigured', 'active', 'disabled')",
        )
        batch.create_check_constraint(
            "ck_namespace_policy_version",
            "policy_version >= 0",
        )
        batch.create_check_constraint(
            "ck_namespace_policy_recorder_quota",
            "recorder_events_daily_limit IS NULL OR recorder_events_daily_limit >= 0",
        )
        batch.create_check_constraint(
            "ck_namespace_policy_decision_quota",
            "decision_records_daily_limit IS NULL OR decision_records_daily_limit >= 0",
        )
        batch.create_check_constraint(
            "ck_namespace_policy_memory_quota",
            "memory_writes_daily_limit IS NULL OR memory_writes_daily_limit >= 0",
        )
        batch.create_check_constraint(
            "ck_namespace_policy_ingest_bytes_quota",
            "estimated_ingest_bytes_daily_limit IS NULL OR estimated_ingest_bytes_daily_limit >= 0",
        )

    op.create_table(
        "namespace_daily_usage",
        sa.Column("id", _uuid(), primary_key=True),
        sa.Column("namespace", sa.String(), nullable=False),
        sa.Column("usage_date", sa.Date(), nullable=False),
        sa.Column("recorder_events", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("decision_records", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("memory_writes", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column(
            "estimated_ingest_bytes",
            sa.BigInteger(),
            nullable=False,
            server_default="0",
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.UniqueConstraint(
            "namespace",
            "usage_date",
            name="uq_namespace_daily_usage_namespace_date",
        ),
        sa.CheckConstraint("recorder_events >= 0", name="ck_namespace_usage_recorder"),
        sa.CheckConstraint("decision_records >= 0", name="ck_namespace_usage_decisions"),
        sa.CheckConstraint("memory_writes >= 0", name="ck_namespace_usage_memories"),
        sa.CheckConstraint(
            "estimated_ingest_bytes >= 0",
            name="ck_namespace_usage_ingest_bytes",
        ),
    )
    op.create_index(
        "ix_namespace_daily_usage_namespace",
        "namespace_daily_usage",
        ["namespace"],
    )
    op.create_index(
        "ix_namespace_daily_usage_date_namespace",
        "namespace_daily_usage",
        ["usage_date", "namespace"],
    )

    op.create_table(
        "namespace_policy_revisions",
        sa.Column("id", _uuid(), primary_key=True),
        sa.Column("namespace", sa.String(), nullable=False),
        sa.Column("policy_version", sa.BigInteger(), nullable=False),
        sa.Column("action", sa.String(length=32), nullable=False),
        sa.Column("actor_id", sa.String(length=512), nullable=False),
        sa.Column("policy_snapshot", sa.JSON(), nullable=False),
        sa.Column("snapshot_hash", sa.String(length=64), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.UniqueConstraint(
            "namespace",
            "policy_version",
            name="uq_namespace_policy_revision_namespace_version",
        ),
        sa.CheckConstraint(
            "policy_version > 0",
            name="ck_namespace_policy_revision_version",
        ),
        sa.CheckConstraint(
            "action IN ('created', 'updated', 'enabled', 'disabled', 'cleared')",
            name="ck_namespace_policy_revision_action",
        ),
        sa.CheckConstraint(
            "length(snapshot_hash) = 64",
            name="ck_namespace_policy_revision_hash",
        ),
    )
    op.create_index(
        "ix_namespace_policy_revisions_namespace",
        "namespace_policy_revisions",
        ["namespace"],
    )
    op.create_index(
        "ix_namespace_policy_revision_namespace_created",
        "namespace_policy_revisions",
        ["namespace", "created_at"],
    )

    dialect = op.get_bind().dialect.name
    if dialect == "postgresql":
        _install_postgres_guards()
    elif dialect == "sqlite":
        _install_sqlite_guards()


def _install_postgres_guards() -> None:
    for table in ("namespace_daily_usage", "namespace_policy_revisions"):
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

    op.execute(
        """CREATE FUNCTION lians_namespace_policy_revision_reject_mutation()
        RETURNS trigger AS $$
        BEGIN
            RAISE EXCEPTION 'namespace_policy_revisions is append-only';
        END;
        $$ LANGUAGE plpgsql"""
    )
    op.execute(
        """CREATE TRIGGER trg_namespace_policy_revisions_append_only
        BEFORE UPDATE OR DELETE ON namespace_policy_revisions
        FOR EACH ROW EXECUTE FUNCTION lians_namespace_policy_revision_reject_mutation()"""
    )
    op.execute(
        """CREATE TRIGGER trg_namespace_policy_revisions_no_truncate
        BEFORE TRUNCATE ON namespace_policy_revisions
        FOR EACH STATEMENT EXECUTE FUNCTION lians_namespace_policy_revision_reject_mutation()"""
    )


def _install_sqlite_guards() -> None:
    for operation in ("UPDATE", "DELETE"):
        op.execute(
            f"""CREATE TRIGGER trg_namespace_policy_revisions_no_{operation.lower()}
            BEFORE {operation} ON namespace_policy_revisions
            BEGIN
                SELECT RAISE(ABORT, 'namespace_policy_revisions is append-only');
            END"""
        )


def downgrade() -> None:
    dialect = op.get_bind().dialect.name
    if dialect == "postgresql":
        op.execute(
            "DROP FUNCTION IF EXISTS lians_namespace_policy_revision_reject_mutation() CASCADE"
        )
    op.drop_table("namespace_policy_revisions")
    op.drop_table("namespace_daily_usage")

    with op.batch_alter_table("namespace_policies") as batch:
        batch.drop_constraint(
            "ck_namespace_policy_ingest_bytes_quota",
            type_="check",
        )
        batch.drop_constraint("ck_namespace_policy_memory_quota", type_="check")
        batch.drop_constraint("ck_namespace_policy_decision_quota", type_="check")
        batch.drop_constraint("ck_namespace_policy_recorder_quota", type_="check")
        batch.drop_constraint("ck_namespace_policy_version", type_="check")
        batch.drop_constraint("ck_namespace_policy_governance_status", type_="check")
        batch.drop_column("governance_updated_by")
        batch.drop_column("governance_updated_at")
        batch.drop_column("governance_created_by")
        batch.drop_column("governance_created_at")
        batch.drop_column("policy_version")
        batch.drop_column("estimated_ingest_bytes_daily_limit")
        batch.drop_column("memory_writes_daily_limit")
        batch.drop_column("decision_records_daily_limit")
        batch.drop_column("recorder_events_daily_limit")
        batch.drop_column("allowed_recorder_capture_modes")
        batch.drop_column("allowed_processing_regions")
        batch.drop_column("governance_status")
