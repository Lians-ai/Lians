"""Align experience RLS policies with the runtime namespace variable.

Revision ID: 0029_experience_rls
Revises: 0028_decision_envelopes
"""

from alembic import op

revision = "0029_experience_rls"
down_revision = "0028_decision_envelopes"
branch_labels = None
depends_on = None


def _replace_policy(table: str, setting: str) -> None:
    policy = f"{table}_namespace_isolation"
    op.execute(f"DROP POLICY IF EXISTS {policy} ON {table}")
    op.execute(
        f"""
        CREATE POLICY {policy} ON {table}
        USING (
          current_setting('{setting}', true) = '__admin__'
          OR namespace = current_setting('{setting}', true)
        )
        WITH CHECK (
          current_setting('{setting}', true) = '__admin__'
          OR namespace = current_setting('{setting}', true)
        )
        """
    )


def upgrade() -> None:
    if op.get_bind().dialect.name != "postgresql":
        return
    for table in ("agent_experiences", "reflection_proposals"):
        _replace_policy(table, "app.current_namespace")


def downgrade() -> None:
    if op.get_bind().dialect.name != "postgresql":
        return
    for table in ("agent_experiences", "reflection_proposals"):
        _replace_policy(table, "agentmem.namespace")
