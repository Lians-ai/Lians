"""relationships graph layer (bitemporal edges + RLS barrier isolation)

Revision ID: 0012_relationships
Revises: 0011_rls_barriers
Create Date: 2026-06-29

Adds the ``relationships`` table — a bitemporal knowledge-graph edge store that
mirrors the temporal/audit/barrier columns of ``memories``. Enables compliance
graph queries (conflict-of-interest, related-party, care-network) without a
separate graph database: N-hop traversal runs over Postgres.
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID, JSONB


revision = "0012_relationships"
down_revision = "0011_rls_barriers"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    is_pg = bind.dialect.name == "postgresql"
    json_type = JSONB if is_pg else sa.JSON

    op.create_table(
        "relationships",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("namespace", sa.String(), nullable=False),
        sa.Column("agent_id", sa.String(), nullable=False),
        sa.Column("src_entity", sa.String(), nullable=False),
        sa.Column("rel_type", sa.String(), nullable=False),
        sa.Column("dst_entity", sa.String(), nullable=False),
        sa.Column("event_time", sa.DateTime(timezone=True), nullable=False),
        sa.Column("ingestion_time", sa.DateTime(timezone=True), nullable=False),
        sa.Column("valid_from", sa.DateTime(timezone=True), nullable=False),
        sa.Column("valid_to", sa.DateTime(timezone=True), nullable=True),
        sa.Column("invalidated_by", UUID(as_uuid=True),
                  sa.ForeignKey("relationships.id"), nullable=True),
        sa.Column("barrier_group", sa.String(), nullable=True),
        sa.Column("subject_id", sa.String(), nullable=True),
        sa.Column("source", sa.String(), nullable=True),
        sa.Column("metadata", json_type, nullable=False, server_default="{}"),
        sa.Column("content_hash", sa.String(), nullable=False),
    )
    op.create_index("ix_relationships_namespace", "relationships", ["namespace"])
    op.create_index("ix_relationships_agent_id", "relationships", ["agent_id"])
    op.create_index("ix_relationships_src_entity", "relationships", ["src_entity"])
    op.create_index("ix_relationships_dst_entity", "relationships", ["dst_entity"])
    op.create_index("ix_relationships_rel_type", "relationships", ["rel_type"])
    op.create_index("ix_relationships_barrier_group", "relationships", ["barrier_group"])
    op.create_index("ix_relationships_subject_id", "relationships", ["subject_id"])
    op.create_index("ix_relationships_content_hash", "relationships", ["content_hash"])
    op.create_index("ix_rel_ns_agent_src", "relationships",
                    ["namespace", "agent_id", "src_entity"])
    op.create_index("ix_rel_ns_agent_dst", "relationships",
                    ["namespace", "agent_id", "dst_entity"])

    if is_pg:
        # Same barrier-isolation policy as memories/live_facts (migration 0011).
        # One statement per execute: asyncpg rejects multi-command prepared
        # statements (see 0011 / 0004).
        op.execute("ALTER TABLE relationships ENABLE ROW LEVEL SECURITY")
        op.execute("ALTER TABLE relationships FORCE ROW LEVEL SECURITY")
        op.execute("""
            CREATE POLICY barrier_isolation ON relationships
                USING (
                    barrier_group IS NULL
                    OR current_setting('agentmem.barrier_group', true) IS NULL
                    OR barrier_group = current_setting('agentmem.barrier_group', true)
                )
        """)


def downgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        op.execute("DROP POLICY IF EXISTS barrier_isolation ON relationships;")
    op.drop_table("relationships")
