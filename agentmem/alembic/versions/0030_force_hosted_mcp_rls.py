"""Force tenant RLS on every table used by the hosted MCP surface.

Revision ID: 0030_force_hosted_mcp_rls
Revises: 0029_fix_experience_namespace_rls
"""

from alembic import op

revision = "0030_force_hosted_mcp_rls"
down_revision = "0029_fix_experience_namespace_rls"
branch_labels = None
depends_on = None


_EXISTING_POLICY_TABLES = (
    "event_log",
    "subject_keys",
    "namespace_policies",
    "agent_barrier_groups",
)

_NEW_POLICY_TABLES = ("idempotency_keys", "conflict_flags")


def upgrade() -> None:
    if op.get_bind().dialect.name != "postgresql":
        return
    for table in _EXISTING_POLICY_TABLES:
        op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY")

    for table in _NEW_POLICY_TABLES:
        op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY")
        op.execute(
            f"""
            CREATE POLICY rls_{table}_namespace ON {table}
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
    if op.get_bind().dialect.name != "postgresql":
        return
    for table in _NEW_POLICY_TABLES:
        op.execute(f"DROP POLICY IF EXISTS rls_{table}_namespace ON {table}")
        op.execute(f"ALTER TABLE {table} NO FORCE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE {table} DISABLE ROW LEVEL SECURITY")
    for table in _EXISTING_POLICY_TABLES:
        op.execute(f"ALTER TABLE {table} NO FORCE ROW LEVEL SECURITY")
