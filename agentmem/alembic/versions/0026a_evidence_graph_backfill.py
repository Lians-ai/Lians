"""Resumably backfill the normalized evidence graph.

Revision ID: 0026a_evidence_graph_backfill
Revises: 0026_evidence_graph

The expand revision owns the empty graph tables and a one-row high-water mark.
This data revision processes only that immutable snapshot in bounded, idempotent
pages.  Every page commits independently; deterministic artifact/link IDs and
conflict-safe inserts make replay safe after cancellation or timeout.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path
from types import ModuleType

import sqlalchemy as sa
from alembic import context, op
from sqlalchemy.dialects import postgresql

revision = "0026a_evidence_graph_backfill"
down_revision = "0026_evidence_graph"
branch_labels = None
depends_on = None

_PROGRESS_TABLE = "lians_migration_0026_evidence_progress"


def _transformer() -> ModuleType:
    """Load the immutable adjacent expand revision's bounded transformer."""
    path = Path(__file__).with_name("0026_evidence_graph.py")
    spec = importlib.util.spec_from_file_location("lians_migration_0026_expand", path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Cannot load evidence backfill transformer from {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _create_progress_snapshot() -> None:
    op.create_table(
        _PROGRESS_TABLE,
        sa.Column("singleton", sa.Boolean(), primary_key=True),
        sa.Column(
            "snapshot_max_decision_id",
            postgresql.UUID(as_uuid=True),
            nullable=True,
        ),
        sa.Column("last_decision_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.CheckConstraint("singleton", name="ck_0026_progress_singleton"),
    )
    op.execute(
        f"""INSERT INTO public.{_PROGRESS_TABLE} (
                singleton,
                snapshot_max_decision_id,
                last_decision_id
            )
            SELECT true,
                   (
                       SELECT id
                       FROM public.decision_records
                       ORDER BY id DESC
                       LIMIT 1
                   ),
                   NULL"""
    )


def upgrade() -> None:
    if op.get_bind().dialect.name != "postgresql":
        return
    if context.is_offline_mode():
        raise RuntimeError(
            "0026a_evidence_graph_backfill requires an online PostgreSQL "
            "connection so bounded evidence pages commit and resume safely. "
            "Generate reviewed offline DDL only through 0026_evidence_graph, "
            "then run 0026a online."
        )
    transformer = _transformer()
    with op.get_context().autocommit_block():
        # Evidence and decision tables are FORCE RLS. Session scope is
        # deliberate because each page is its own autocommit transaction.
        op.execute(
            sa.text("SELECT set_config('app.current_namespace', '__admin__', false)")
        )
        op.execute(
            sa.text("SELECT set_config('agentmem.barrier_group', '', false)")
        )
        transformer._backfill()
    op.drop_table(_PROGRESS_TABLE)


def downgrade() -> None:
    if op.get_bind().dialect.name == "postgresql":
        op.execute(
            sa.text("SELECT set_config('app.current_namespace', '__admin__', true)")
        )
        op.execute(
            sa.text("SELECT set_config('agentmem.barrier_group', '', true)")
        )
        _create_progress_snapshot()
