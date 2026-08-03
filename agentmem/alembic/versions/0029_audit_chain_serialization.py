"""Serialize namespace audit chains and reject historical forks.

Revision ID: 0029_audit_chain_serialization
Revises: 0028_control_plane
"""

from alembic import op
import sqlalchemy as sa

revision = "0029_audit_chain_serialization"
down_revision = "0028_control_plane"
branch_labels = None
depends_on = None


def upgrade() -> None:
    connection = op.get_bind()
    forks = connection.execute(
        sa.text(
            """
            SELECT namespace, prev_hash, COUNT(*) AS child_count
            FROM event_log
            WHERE prev_hash IS NOT NULL
            GROUP BY namespace, prev_hash
            HAVING COUNT(*) > 1
            LIMIT 20
            """
        )
    ).fetchall()
    if forks:
        details = "; ".join(
            f"namespace={row[0]!r} prev_hash={str(row[1])[:16]}… children={row[2]}"
            for row in forks
        )
        raise RuntimeError(
            "Cannot enforce a linear audit chain while historical forks exist. "
            "Export and reconcile the affected namespace chains first: " + details
        )

    op.create_index(
        "uq_event_log_namespace_prev_hash",
        "event_log",
        ["namespace", "prev_hash"],
        unique=True,
    )


def downgrade() -> None:
    op.drop_index("uq_event_log_namespace_prev_hash", table_name="event_log")
