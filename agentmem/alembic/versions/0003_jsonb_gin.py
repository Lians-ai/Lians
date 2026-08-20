"""Convert metadata/payload columns to JSONB and add GIN indexes

JSONB (binary JSON) is strictly faster than JSON on PostgreSQL:
- Stores pre-parsed binary representation
- Supports GIN indexes (required for fast key/value containment queries)
- Enables `@>` containment operator: metadata @> '{"ticker":"NVDA"}'
  which the HNSW+GIN query planner can use together

Before this migration: filtering by ticker/CUSIP on large namespaces
requires a full table scan of the memories table.  After this migration:
the GIN index allows PostgreSQL to resolve metadata filters in O(log N).

This migration is PostgreSQL-only.  On other databases it is a no-op.

Revision ID: 0003_jsonb_gin
Revises: 0002_api_key_label
Create Date: 2026-06-18
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa

revision = "0003_jsonb_gin"
down_revision = "0002_api_key_label"
branch_labels = None
depends_on = None


def _is_postgres() -> bool:
    return op.get_bind().dialect.name == "postgresql"


def upgrade() -> None:
    if not _is_postgres():
        return

    # Convert memories.metadata from JSON → JSONB
    op.execute(sa.text(
        "ALTER TABLE memories ALTER COLUMN metadata TYPE JSONB USING metadata::JSONB"
    ))

    # Convert event_log.payload from JSON → JSONB
    op.execute(sa.text(
        "ALTER TABLE event_log ALTER COLUMN payload TYPE JSONB USING payload::JSONB"
    ))

    # GIN index on memories.metadata - accelerates ticker/metric/entity filters
    # Uses jsonb_path_ops for faster containment (@>) queries.
    op.execute(sa.text(
        "CREATE INDEX IF NOT EXISTS ix_memories_metadata_gin "
        "ON memories USING GIN (metadata jsonb_path_ops)"
    ))

    # GIN index on event_log.payload - accelerates audit queries by op + payload fields
    op.execute(sa.text(
        "CREATE INDEX IF NOT EXISTS ix_event_log_payload_gin "
        "ON event_log USING GIN (payload jsonb_path_ops)"
    ))


def downgrade() -> None:
    if not _is_postgres():
        return

    op.execute(sa.text("DROP INDEX IF EXISTS ix_memories_metadata_gin"))
    op.execute(sa.text("DROP INDEX IF EXISTS ix_event_log_payload_gin"))

    op.execute(sa.text(
        "ALTER TABLE memories ALTER COLUMN metadata TYPE JSON USING metadata::TEXT::JSON"
    ))
    op.execute(sa.text(
        "ALTER TABLE event_log ALTER COLUMN payload TYPE JSON USING payload::TEXT::JSON"
    ))
