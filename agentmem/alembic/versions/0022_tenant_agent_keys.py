"""Scope agent identifiers and barrier assignments to their namespace.

Revision ID: 0022_tenant_agent_keys
Revises: 0021_record_barriers

Both tables originally used ``agent_id`` as a global primary key even though
agent IDs are tenant-controlled and only meaningful inside a namespace. A
tenant could therefore reserve a common ID (for example ``default``) and cause
another tenant's insert or barrier assignment to fail.
"""
from alembic import op


revision = "0022_tenant_agent_keys"
down_revision = "0021_record_barriers"
branch_labels = None
depends_on = None

_TABLES = ("agents", "agent_barrier_groups")


def upgrade() -> None:
    if op.get_bind().dialect.name != "postgresql":
        return
    for table in _TABLES:
        op.execute(f"ALTER TABLE {table} DROP CONSTRAINT IF EXISTS {table}_pkey")
        op.execute(f"ALTER TABLE {table} ADD PRIMARY KEY (namespace, agent_id)")


def downgrade() -> None:
    if op.get_bind().dialect.name != "postgresql":
        return
    for table in _TABLES:
        op.execute(f"ALTER TABLE {table} DROP CONSTRAINT IF EXISTS {table}_pkey")
        op.execute(f"ALTER TABLE {table} ADD PRIMARY KEY (agent_id)")
