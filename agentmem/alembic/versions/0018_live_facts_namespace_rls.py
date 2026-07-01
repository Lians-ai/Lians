"""Add permissive namespace RLS policy to live_facts and relationships

Revision ID: 0018_live_facts_namespace_rls
Revises: 0017_apikey_barrier
Create Date: 2026-07-01

Bug
---
live_facts (created in 0010) and relationships (created in 0012) only ever
carried the ``barrier_isolation`` policy. Migration 0013 recreated that policy
``AS RESTRICTIVE``. In PostgreSQL a restrictive policy only *narrows* access; a
table that has RLS ENABLED + FORCED but **no PERMISSIVE policy** default-denies
every row. The result: every INSERT into live_facts/relationships failed with
``new row violates row-level security policy`` and every SELECT returned nothing.

memories/event_log/subject_keys/namespace_policies were unaffected because 0004
(and 0005) gave them a permissive ``*_namespace`` policy; live_facts and
relationships never got one because they did not exist yet in 0004.

Fix
---
Add the same permissive namespace policy used on memories (rls_memories_namespace,
migration 0004). The permissive policy GRANTS namespace-scoped access (and the
INSERT WITH CHECK), and the restrictive ``barrier_isolation`` policy from 0013
then AND-narrows it by information barrier — exactly how memories already works.
"""
from alembic import op


revision = "0018_live_facts_namespace_rls"
down_revision = "0017_apikey_barrier"
branch_labels = None
depends_on = None

# Tables that have RLS forced + only a RESTRICTIVE barrier policy, and so need a
# permissive namespace policy to be reachable at all.
_TABLES = ("live_facts", "relationships")

# Mirrors rls_memories_namespace from 0004: caller must carry a matching
# app.current_namespace, or the admin sentinel bypasses.
_POLICY = """
    USING (
        namespace = current_setting('app.current_namespace', true)
        OR current_setting('app.current_namespace', true) = '__admin__'
    )
    WITH CHECK (
        namespace = current_setting('app.current_namespace', true)
        OR current_setting('app.current_namespace', true) = '__admin__'
    )
"""


def upgrade() -> None:
    if op.get_bind().dialect.name != "postgresql":
        return  # RLS is Postgres-only; SQLite tests use application-layer filtering
    for table in _TABLES:
        op.execute(f"DROP POLICY IF EXISTS rls_{table}_namespace ON {table}")
        op.execute(f"CREATE POLICY rls_{table}_namespace ON {table}{_POLICY}")


def downgrade() -> None:
    if op.get_bind().dialect.name != "postgresql":
        return
    for table in _TABLES:
        op.execute(f"DROP POLICY IF EXISTS rls_{table}_namespace ON {table}")
