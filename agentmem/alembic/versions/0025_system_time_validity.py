"""Add transaction-time validity for historical knowledge reconstruction.

``memories.valid_from`` / ``valid_to`` remain the business-time interval.
``system_valid_from`` / ``system_valid_to`` record when Lians learned that
interval state, preventing a late, backdated correction from rewriting an
earlier decision boundary.

Revision ID: 0025_system_time_validity
Revises: 0024_audit_payload_hash
"""

import sqlalchemy as sa
from alembic import op

revision = "0025_system_time_validity"
down_revision = "0024_audit_payload_hash"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "memories",
        sa.Column(
            "system_valid_from",
            sa.DateTime(timezone=True),
            nullable=True,
        ),
    )
    op.alter_column(
        "memories",
        "system_valid_from",
        existing_type=sa.DateTime(timezone=True),
        nullable=True,
        server_default=sa.func.now(),
    )
    op.add_column(
        "memories",
        sa.Column("system_valid_to", sa.DateTime(timezone=True), nullable=True),
    )

    op.add_column(
        "decision_records",
        sa.Column(
            "knowledge_recorded_as_of",
            sa.DateTime(timezone=True),
            nullable=True,
        ),
    )
    if op.get_bind().dialect.name == "postgresql":
        # During the rolling expand/backfill window, 0.4.2 writers do not know
        # these columns. Fill only absent values at the database boundary; 0.5
        # writers continue to provide their explicit transaction-time values.
        op.execute(
            """CREATE FUNCTION public.lians_fill_legacy_system_validity()
            RETURNS trigger
            LANGUAGE plpgsql
            SET search_path = pg_catalog, public
            AS $$
            BEGIN
                IF NEW.system_valid_from IS NULL THEN
                    NEW.system_valid_from := COALESCE(
                        NEW.ingestion_time,
                        CURRENT_TIMESTAMP
                    );
                END IF;
                IF NEW.valid_to IS NOT NULL AND NEW.system_valid_to IS NULL THEN
                    NEW.system_valid_to := CURRENT_TIMESTAMP;
                END IF;
                RETURN NEW;
            END;
            $$"""
        )
        op.execute(
            """CREATE TRIGGER trg_memories_fill_legacy_system_validity
            BEFORE INSERT OR UPDATE ON public.memories
            FOR EACH ROW
            EXECUTE FUNCTION public.lians_fill_legacy_system_validity()"""
        )


def downgrade() -> None:
    if op.get_bind().dialect.name == "postgresql":
        op.execute(
            "DROP TRIGGER IF EXISTS trg_memories_fill_legacy_system_validity "
            "ON public.memories"
        )
        op.execute(
            "DROP FUNCTION IF EXISTS public.lians_fill_legacy_system_validity()"
        )
    op.drop_column("decision_records", "knowledge_recorded_as_of")
    op.drop_column("memories", "system_valid_to")
    op.drop_column("memories", "system_valid_from")
