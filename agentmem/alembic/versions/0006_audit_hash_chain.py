"""Add prev_hash and row_hash to event_log for SEC 17a-4 tamper-evidence

Each event_log row now stores:
  prev_hash — SHA-256 of the previous row in the same namespace (or 64 zeros
               for the first row — the "genesis" sentinel).
  row_hash  — SHA-256(prev_hash || id || namespace || agent_id || op ||
               memory_id || content_hash || created_at ISO-8601).

Any modification of a historical row breaks the chain from that point forward,
detectable by GET /v1/admin/audit/verify?namespace=<ns>.

Existing rows are backfilled with a proper chain ordered by (created_at, id)
per namespace, so the chain is intact from day 0.

Revision ID: 0006_audit_hash_chain
Revises: 0005_retention_policy
Create Date: 2026-06-19
"""
from __future__ import annotations

import hashlib
from alembic import op
import sqlalchemy as sa
from sqlalchemy import text

revision = "0006_audit_hash_chain"
down_revision = "0005_retention_policy"
branch_labels = None
depends_on = None

GENESIS_HASH = "0" * 64


def _compute_hash(prev_hash, row_id, namespace, agent_id, op_val, memory_id, content_hash, created_at_iso):
    fields = [
        prev_hash,
        str(row_id),
        namespace or "",
        agent_id or "",
        op_val or "",
        str(memory_id) if memory_id is not None else "null",
        content_hash if content_hash is not None else "null",
        created_at_iso,
    ]
    return hashlib.sha256("|".join(fields).encode()).hexdigest()


def upgrade() -> None:
    op.add_column("event_log", sa.Column("prev_hash", sa.String(64), nullable=True))
    op.add_column("event_log", sa.Column("row_hash", sa.String(64), nullable=True))
    op.create_index("ix_event_log_row_hash", "event_log", ["row_hash"], unique=False)

    conn = op.get_bind()

    rows = conn.execute(text(
        "SELECT id, namespace, agent_id, op, memory_id, content_hash, created_at "
        "FROM event_log ORDER BY namespace, created_at, id"
    )).fetchall()

    tips: dict[str, str] = {}

    for row in rows:
        ns = row[1]
        prev_hash = tips.get(ns, GENESIS_HASH)

        created_at = row[6]
        if hasattr(created_at, "isoformat"):
            created_at_iso = created_at.isoformat()
        else:
            created_at_iso = str(created_at)

        row_hash = _compute_hash(
            prev_hash=prev_hash,
            row_id=row[0],
            namespace=row[1],
            agent_id=row[2],
            op_val=row[3],
            memory_id=row[4],
            content_hash=row[5],
            created_at_iso=created_at_iso,
        )

        conn.execute(
            text("UPDATE event_log SET prev_hash=:p, row_hash=:r WHERE id=:id"),
            {"p": prev_hash, "r": row_hash, "id": str(row[0])},
        )
        tips[ns] = row_hash


def downgrade() -> None:
    op.drop_index("ix_event_log_row_hash", table_name="event_log")
    op.drop_column("event_log", "row_hash")
    op.drop_column("event_log", "prev_hash")
