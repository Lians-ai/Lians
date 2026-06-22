"""Add live_facts and merkle_anchors tables

Revision ID: 0010_live_facts_merkle
Revises: 0009_webhooks
Create Date: 2026-06-22

live_facts  — compact present-time projection of memories (Change 1 of the
              performance roadmap).  One row per currently-valid memory;
              recall queries this table instead of scanning ``memories WHERE
              valid_to IS NULL``, shrinking the ANN search space 5–10×.

              Keyed facts (predicate_key IS NOT NULL) have at most one row per
              (namespace, agent_id, predicate_key) — the supersession engine
              enforces this invariant on the write path.

merkle_anchors — periodic Merkle-root anchors for the windowed audit-chain
              batcher (Change 8 of the performance roadmap).  Only written
              when MERKLE_BATCH_ENABLED=true.  The serial chain in event_log
              continues to work with or without Merkle batching.

Both tables are maintained synchronously on the write path by
``current_facts.py`` and ``merkle_audit.py`` respectively.

BACKFILL
--------
After creating live_facts, this migration backfills it from existing
memories WHERE valid_to IS NULL AND erased_at IS NULL.  The backfill
preserves all columns needed for recall (embedding, content_encrypted,
metadata) so an upgraded database is immediately queryable without a
warm-up period.

On databases without the pgvector extension (e.g. non-PG CI) the
HNSW index creation is skipped gracefully.
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "0010_live_facts_merkle"
down_revision = "0009_webhooks"
branch_labels = None
depends_on = None

EMBED_DIM = 1024  # must match config.embedding_dim


def _is_postgres() -> bool:
    return op.get_bind().dialect.name == "postgresql"


def _has_pgvector() -> bool:
    """Return True if the pgvector extension is available."""
    try:
        result = op.get_bind().execute(
            sa.text("SELECT 1 FROM pg_extension WHERE extname = 'vector'")
        )
        return result.fetchone() is not None
    except Exception:
        return False


def upgrade() -> None:
    # ── live_facts ─────────────────────────────────────────────────────────────
    if _is_postgres():
        embedding_col = sa.Column(
            "embedding",
            postgresql.ARRAY(sa.Float()),   # stored as vector on PG
            nullable=True,
        )
    else:
        embedding_col = sa.Column("embedding", sa.JSON(), nullable=True)

    op.create_table(
        "live_facts",
        sa.Column("id", postgresql.UUID(as_uuid=True) if _is_postgres() else sa.String(), primary_key=True),
        sa.Column("namespace", sa.String(), nullable=False),
        sa.Column("agent_id", sa.String(), nullable=False),
        sa.Column(
            "memory_id",
            postgresql.UUID(as_uuid=True) if _is_postgres() else sa.String(),
            sa.ForeignKey("memories.id"),
            nullable=False,
            unique=True,
        ),
        sa.Column("predicate_key", sa.String(), nullable=True),
        sa.Column("subject_id", sa.String(), nullable=True),
        sa.Column("barrier_group", sa.String(), nullable=True),
        sa.Column("event_time", sa.DateTime(timezone=True), nullable=False),
        sa.Column("importance", sa.Float(), nullable=False, server_default="0.5"),
        sa.Column(
            "metadata",
            postgresql.JSONB() if _is_postgres() else sa.JSON(),
            nullable=False,
            server_default="{}",
        ),
        sa.Column("content_encrypted", sa.LargeBinary(), nullable=True),
        # embedding column — Vector type on PG, JSON on SQLite
        sa.Column("embedding", sa.Text() if not _is_postgres() else sa.Text(), nullable=True),
    )

    # Recreate with proper types depending on dialect
    # (the above sets a placeholder; the actual column type is set via raw DDL on PG)
    if _is_postgres():
        # Drop the placeholder embedding column and add the correct vector type
        op.drop_column("live_facts", "embedding")
        has_vector = _has_pgvector()
        if has_vector:
            op.execute(sa.text(
                f"ALTER TABLE live_facts ADD COLUMN embedding vector({EMBED_DIM})"
            ))
        else:
            # pgvector not available — fall back to JSONB array storage
            op.add_column("live_facts", sa.Column("embedding", postgresql.JSONB(), nullable=True))

    # Indexes — fast path: (namespace, agent_id) and (namespace, agent_id, predicate_key)
    op.create_index("ix_live_facts_namespace", "live_facts", ["namespace"])
    op.create_index("ix_live_facts_agent_id", "live_facts", ["agent_id"])
    op.create_index("ix_live_facts_predicate_key", "live_facts", ["predicate_key"])
    op.create_index("ix_live_facts_subject_id", "live_facts", ["subject_id"])
    op.create_index("ix_live_facts_barrier_group", "live_facts", ["barrier_group"])
    op.create_index("ix_live_facts_ns_agent", "live_facts", ["namespace", "agent_id"])
    op.create_index(
        "ix_live_facts_ns_agent_pred",
        "live_facts",
        ["namespace", "agent_id", "predicate_key"],
    )

    # HNSW vector index — PG + pgvector only
    if _is_postgres() and _has_pgvector():
        op.create_index(
            "ix_live_facts_embedding_hnsw",
            "live_facts",
            ["embedding"],
            postgresql_using="hnsw",
            postgresql_with={"m": 16, "ef_construction": 64},
            postgresql_ops={"embedding": "vector_cosine_ops"},
        )

    # ── Backfill from existing live memories ───────────────────────────────────
    # Copy every memory that is currently valid (valid_to IS NULL, erased_at IS NULL)
    # into live_facts so an upgraded database is immediately queryable.
    #
    # predicate_key is computed from structured metadata keys.  We store NULL
    # here and let the application compute it on next write — existing memories
    # are retrievable via semantic search immediately after upgrade.
    if _is_postgres():
        op.execute(sa.text("""
            INSERT INTO live_facts (
                id, namespace, agent_id, memory_id,
                predicate_key, subject_id, barrier_group,
                event_time, importance, metadata,
                content_encrypted, embedding
            )
            SELECT
                gen_random_uuid(),
                namespace,
                agent_id,
                id AS memory_id,
                NULL AS predicate_key,
                subject_id,
                barrier_group,
                event_time,
                importance,
                metadata,
                content_encrypted,
                embedding
            FROM memories
            WHERE valid_to IS NULL
              AND erased_at IS NULL
        """))

    # ── merkle_anchors ─────────────────────────────────────────────────────────
    op.create_table(
        "merkle_anchors",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True) if _is_postgres() else sa.String(),
            primary_key=True,
        ),
        sa.Column("namespace", sa.String(), nullable=False),
        sa.Column("root_hash", sa.String(64), nullable=False),
        sa.Column("window_size", sa.Integer(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column(
            "prev_anchor_id",
            postgresql.UUID(as_uuid=True) if _is_postgres() else sa.String(),
            sa.ForeignKey("merkle_anchors.id"),
            nullable=True,
        ),
    )
    op.create_index("ix_merkle_anchors_namespace", "merkle_anchors", ["namespace"])


def downgrade() -> None:
    op.drop_index("ix_merkle_anchors_namespace", table_name="merkle_anchors")
    op.drop_table("merkle_anchors")

    if _is_postgres() and _has_pgvector():
        try:
            op.drop_index("ix_live_facts_embedding_hnsw", table_name="live_facts")
        except Exception:
            pass
    op.drop_index("ix_live_facts_ns_agent_pred", table_name="live_facts")
    op.drop_index("ix_live_facts_ns_agent", table_name="live_facts")
    op.drop_index("ix_live_facts_barrier_group", table_name="live_facts")
    op.drop_index("ix_live_facts_subject_id", table_name="live_facts")
    op.drop_index("ix_live_facts_predicate_key", table_name="live_facts")
    op.drop_index("ix_live_facts_agent_id", table_name="live_facts")
    op.drop_index("ix_live_facts_namespace", table_name="live_facts")
    op.drop_table("live_facts")
