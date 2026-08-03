"""Add indexes used by exact durable-inventory aggregation.

Revision ID: 0048_observability_indexes
Revises: 0047_recall_governance_quota

The observability refresher groups Recorder events and impact jobs across the
internal admin boundary.  These low-cardinality indexes keep those exact
aggregates on narrow index pages as the underlying evidence tables grow.
PostgreSQL builds and drops them concurrently so a routine release does not
take a table-wide write lock on high-volume evidence ingestion.
"""

from __future__ import annotations

from alembic import op

revision = "0048_observability_indexes"
down_revision = "0047_recall_governance_quota"
branch_labels = None
depends_on = None

_INDEXES = (
    (
        "ix_recorder_events_capture_mode",
        "recorder_events",
        ["capture_mode"],
    ),
    (
        "ix_impact_assessment_status_created",
        "decision_impact_assessment_jobs",
        ["status", "created_at"],
    ),
)


def upgrade() -> None:
    if op.get_bind().dialect.name == "postgresql":
        with op.get_context().autocommit_block():
            for name, table, columns in _INDEXES:
                op.create_index(
                    name,
                    table,
                    columns,
                    unique=False,
                    postgresql_concurrently=True,
                )
        return

    for name, table, columns in _INDEXES:
        op.create_index(name, table, columns, unique=False)


def downgrade() -> None:
    if op.get_bind().dialect.name == "postgresql":
        with op.get_context().autocommit_block():
            for name, table, _columns in reversed(_INDEXES):
                op.drop_index(
                    name,
                    table_name=table,
                    postgresql_concurrently=True,
                )
        return

    for name, table, _columns in reversed(_INDEXES):
        op.drop_index(name, table_name=table)
