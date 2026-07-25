"""Enforce namespace and information barriers on record-layer tables.

Revision ID: 0021_record_barriers
Revises: 0020_decision_records
"""
from alembic import op
import sqlalchemy as sa

revision = "0021_record_barriers"
down_revision = "0020_decision_records"
branch_labels = None
depends_on = None

_TABLES = (
    "decision_records",
    "ledger_events",
    "pending_admissions",
    "webhook_endpoints",
)


def upgrade() -> None:
    for table in _TABLES:
        op.add_column(table, sa.Column("barrier_group", sa.String(), nullable=True))
        op.create_index(f"ix_{table}_barrier_group", table, ["barrier_group"])

    if op.get_bind().dialect.name != "postgresql":
        return

    # Existing records have no trustworthy barrier provenance. Deny them to
    # scoped keys by default; unbarriered compliance/owner keys retain access.
    for table in _TABLES:
        op.execute(f"UPDATE {table} SET barrier_group = '__legacy_restricted__'")
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
            f"""CREATE POLICY barrier_isolation ON {table} AS RESTRICTIVE
            USING (
                barrier_group IS NULL
                OR current_setting('agentmem.barrier_group', true) IS NULL
                OR current_setting('agentmem.barrier_group', true) = ''
                OR barrier_group = current_setting('agentmem.barrier_group', true)
            )"""
        )


def downgrade() -> None:
    if op.get_bind().dialect.name == "postgresql":
        for table in _TABLES:
            op.execute(f"DROP POLICY IF EXISTS barrier_isolation ON {table}")
            op.execute(f"DROP POLICY IF EXISTS rls_{table}_namespace ON {table}")
            op.execute(f"ALTER TABLE {table} DISABLE ROW LEVEL SECURITY")
            op.execute(f"ALTER TABLE {table} NO FORCE ROW LEVEL SECURITY")
    for table in _TABLES:
        op.drop_index(f"ix_{table}_barrier_group", table_name=table)
        op.drop_column(table, "barrier_group")
