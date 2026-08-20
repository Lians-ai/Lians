"""Make information-barrier RLS policies RESTRICTIVE (real cross-barrier isolation)

Revision ID: 0013_restrictive_barriers
Revises: 0012_relationships
Create Date: 2026-06-29

Background
----------
The barrier_isolation policies added in 0011 (memories, live_facts) and 0012
(relationships) were PERMISSIVE. PostgreSQL combines multiple permissive policies
on a table with OR, so a row was visible if EITHER the namespace policy (0004) OR
the barrier policy permitted it. Because the namespace policy permits every row in
the caller's namespace, the barrier was effectively defeated within a namespace -
isolation was actually being enforced only at the application layer.

This migration recreates the barrier policies AS RESTRICTIVE. Restrictive policies
are AND-ed with the permissive policies, so a row is visible only when the
namespace policy permits it AND the barrier policy permits it - i.e. genuine
Chinese-wall isolation at the database layer.

The USING clause is unchanged: a caller with no ``agentmem.barrier_group`` set
(e.g. a compliance officer) still sees everything in the namespace; a caller scoped
to group A sees only NULL-barrier (shared) rows and group-A rows.
"""
from alembic import op


revision = "0013_restrictive_barriers"
down_revision = "0012_relationships"
branch_labels = None
depends_on = None

_TABLES = ("memories", "live_facts", "relationships")

_USING = """
    USING (
        barrier_group IS NULL
        OR current_setting('agentmem.barrier_group', true) IS NULL
        OR current_setting('agentmem.barrier_group', true) = ''
        OR barrier_group = current_setting('agentmem.barrier_group', true)
    )
"""


def upgrade() -> None:
    if op.get_bind().dialect.name != "postgresql":
        return  # RLS is Postgres-only; SQLite tests use application-layer filtering
    for table in _TABLES:
        op.execute(f"DROP POLICY IF EXISTS barrier_isolation ON {table}")
        op.execute(
            f"CREATE POLICY barrier_isolation ON {table} AS RESTRICTIVE {_USING}"
        )


def downgrade() -> None:
    if op.get_bind().dialect.name != "postgresql":
        return
    for table in _TABLES:
        op.execute(f"DROP POLICY IF EXISTS barrier_isolation ON {table}")
        op.execute(
            f"CREATE POLICY barrier_isolation ON {table} {_USING}"
        )
