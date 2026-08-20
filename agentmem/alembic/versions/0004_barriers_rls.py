"""Add information barriers + PostgreSQL Row-Level Security

Two changes in one migration because both are part of the same security
hardening sprint and RLS policies reference the new barrier_group column.

Changes:
  1. memories.barrier_group - nullable text, indexed; tags each memory with the
     barrier group of the writing agent so recall can enforce Chinese walls.

  2. agent_barrier_groups table - maps (agent_id, namespace) → group_name.
     Assignments are managed via POST /v1/admin/barriers.

  3. Postgres Row-Level Security on memories, event_log, subject_keys.
     Policy: every query must carry a matching app.current_namespace session
     variable (set by FastAPI deps.py on every authenticated request).
     Admin operations set app.current_namespace = '__admin__' to bypass.

     NOTE: RLS is enforced only for non-superuser DB roles.  The app user
     (agentmem) must NOT be a superuser.  The Alembic user needs CREATERLS
     or superuser privilege to create policies.

     On SQLite (unit tests) this entire migration is a no-op.

Revision ID: 0004_barriers_rls
Revises: 0003_jsonb_gin
Create Date: 2026-06-18
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa

revision = "0004_barriers_rls"
down_revision = "0003_jsonb_gin"
branch_labels = None
depends_on = None


def _is_postgres() -> bool:
    return op.get_bind().dialect.name == "postgresql"


def upgrade() -> None:
    # ── 1. barrier_group column on memories ─────────────────────────────────
    op.add_column("memories", sa.Column("barrier_group", sa.String(), nullable=True))
    op.create_index("ix_memories_barrier_group", "memories", ["barrier_group"])

    # ── 2. agent_barrier_groups table ───────────────────────────────────────
    op.create_table(
        "agent_barrier_groups",
        sa.Column("agent_id", sa.String(), primary_key=True),
        sa.Column("namespace", sa.String(), nullable=False),
        sa.Column("group_name", sa.String(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
    )
    op.create_index("ix_agent_barrier_groups_namespace", "agent_barrier_groups", ["namespace"])
    op.create_index("ix_agent_barrier_groups_group_name", "agent_barrier_groups", ["group_name"])

    if not _is_postgres():
        return  # RLS is PG-only

    # ── 3. Row-Level Security ────────────────────────────────────────────────
    # memories
    op.execute(sa.text("ALTER TABLE memories ENABLE ROW LEVEL SECURITY"))
    op.execute(sa.text("""
        CREATE POLICY rls_memories_namespace ON memories
        USING (
            namespace = current_setting('app.current_namespace', true)
            OR current_setting('app.current_namespace', true) = '__admin__'
        )
        WITH CHECK (
            namespace = current_setting('app.current_namespace', true)
            OR current_setting('app.current_namespace', true) = '__admin__'
        )
    """))

    # event_log
    op.execute(sa.text("ALTER TABLE event_log ENABLE ROW LEVEL SECURITY"))
    op.execute(sa.text("""
        CREATE POLICY rls_event_log_namespace ON event_log
        USING (
            namespace = current_setting('app.current_namespace', true)
            OR current_setting('app.current_namespace', true) = '__admin__'
        )
        WITH CHECK (
            namespace = current_setting('app.current_namespace', true)
            OR current_setting('app.current_namespace', true) = '__admin__'
        )
    """))

    # subject_keys
    op.execute(sa.text("ALTER TABLE subject_keys ENABLE ROW LEVEL SECURITY"))
    op.execute(sa.text("""
        CREATE POLICY rls_subject_keys_namespace ON subject_keys
        USING (
            namespace = current_setting('app.current_namespace', true)
            OR current_setting('app.current_namespace', true) = '__admin__'
        )
        WITH CHECK (
            namespace = current_setting('app.current_namespace', true)
            OR current_setting('app.current_namespace', true) = '__admin__'
        )
    """))

    # agent_barrier_groups - admin-only table; RLS keeps it namespace-scoped too
    op.execute(sa.text("ALTER TABLE agent_barrier_groups ENABLE ROW LEVEL SECURITY"))
    op.execute(sa.text("""
        CREATE POLICY rls_agent_barrier_groups_namespace ON agent_barrier_groups
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
        for table in ("agent_barrier_groups", "subject_keys", "event_log", "memories"):
            op.execute(sa.text(f"DROP POLICY IF EXISTS rls_{table}_namespace ON {table}"))
            op.execute(sa.text(f"ALTER TABLE {table} DISABLE ROW LEVEL SECURITY"))

    op.drop_index("ix_agent_barrier_groups_group_name", table_name="agent_barrier_groups")
    op.drop_index("ix_agent_barrier_groups_namespace", table_name="agent_barrier_groups")
    op.drop_table("agent_barrier_groups")

    op.drop_index("ix_memories_barrier_group", table_name="memories")
    op.drop_column("memories", "barrier_group")
