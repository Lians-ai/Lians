"""Add RLS information barrier policies on memories and live_facts.

Revision ID: 0011_rls_barriers
Revises: 0010_live_facts_merkle
Create Date: 2026-06-22

Enables Postgres Row-Level Security on the two tables that hold agent data.
After applying this migration, set RLS_BARRIERS_ENABLED=true in config so
the application sets the session variable before each query.

Policy logic
------------
  ADMIN routes (get_db):
    • Do not SET agentmem.barrier_group
    • current_setting(..., true) returns NULL
    • policy: ``current_setting IS NULL`` → TRUE → all rows visible

  BARRIER-SCOPED routes (get_db_with_barrier):
    • SET LOCAL agentmem.barrier_group = '<group>'
    • policy: ``barrier_group IS NULL OR barrier_group = '<group>'``
    • correctly returns unbarriered + own-group memories only

FORCE ROW LEVEL SECURITY is required because the app user is also the table
owner in single-user deployments (Docker Compose, Fly.io).  Without FORCE,
RLS is bypassed by the owner regardless of the policy.

The downgrade removes all policies and disables RLS so a rollback is clean.
"""
from alembic import op


revision = "0011_rls_barriers"
down_revision = "0010_live_facts_merkle"
branch_labels = None
depends_on = None


_POLICY_SQL = """\
ALTER TABLE memories ENABLE ROW LEVEL SECURITY;
ALTER TABLE memories FORCE ROW LEVEL SECURITY;
CREATE POLICY barrier_isolation ON memories
    USING (
        barrier_group IS NULL
        OR current_setting('agentmem.barrier_group', true) IS NULL
        OR barrier_group = current_setting('agentmem.barrier_group', true)
    );

ALTER TABLE live_facts ENABLE ROW LEVEL SECURITY;
ALTER TABLE live_facts FORCE ROW LEVEL SECURITY;
CREATE POLICY barrier_isolation ON live_facts
    USING (
        barrier_group IS NULL
        OR current_setting('agentmem.barrier_group', true) IS NULL
        OR barrier_group = current_setting('agentmem.barrier_group', true)
    );
"""

_ROLLBACK_SQL = """\
DROP POLICY IF EXISTS barrier_isolation ON memories;
ALTER TABLE memories DISABLE ROW LEVEL SECURITY;
ALTER TABLE memories NO FORCE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS barrier_isolation ON live_facts;
ALTER TABLE live_facts DISABLE ROW LEVEL SECURITY;
ALTER TABLE live_facts NO FORCE ROW LEVEL SECURITY;
"""


def _execute_each(sql: str) -> None:
    """
    Run a multi-statement SQL string one statement at a time.

    asyncpg (the migration driver) rejects multiple commands in a single
    prepared statement, so each ``;``-separated statement must be executed
    individually — matching the one-statement-per-execute pattern in 0004.
    """
    for statement in (s.strip() for s in sql.split(";")):
        if statement:
            op.execute(statement)


def upgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name != "postgresql":
        return  # RLS is a Postgres feature; skip on SQLite (tests)
    _execute_each(_POLICY_SQL)


def downgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name != "postgresql":
        return
    _execute_each(_ROLLBACK_SQL)
