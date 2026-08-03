"""Persist exact impact-snapshot cardinality and completion invariants.

Revision ID: 0051_impact_snapshot_row_count
Revises: 0050_protected_action_governance

Coverage and link sequences are global allocation identifiers, not row counts.
This revision records the exact tenant/barrier-visible scan cardinality frozen by
each job so progress telemetry and exhaustive-completion claims remain truthful
when sequence values contain gaps or interleaved tenants.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0051_impact_snapshot_row_count"
down_revision = "0050_protected_action_governance"
branch_labels = None
depends_on = None

_TABLE = "decision_impact_assessment_jobs"
_POSTGRES_FUNCTION = "lians_impact_snapshot_count_guard_update"
_TRIGGER = "trg_decision_impact_assessment_jobs_snapshot_count_guard_update"


def _backfill_exact_snapshot_counts() -> None:
    op.execute(
        sa.text(
            """
            UPDATE decision_impact_assessment_jobs AS job
            SET snapshot_decision_count = (
                SELECT count(*)
                FROM decision_evidence_coverage_sets AS coverage
                JOIN decision_records AS decision
                  ON decision.id = coverage.decision_id
                 AND decision.namespace = coverage.namespace
                WHERE coverage.namespace = job.namespace
                  AND decision.namespace = job.namespace
                  AND coverage.sequence <= job.snapshot_max_coverage_sequence
                  AND (
                    job.barrier_group IS NULL
                    OR (
                      (coverage.barrier_group IS NULL
                       OR coverage.barrier_group = job.barrier_group)
                      AND
                      (decision.barrier_group IS NULL
                       OR decision.barrier_group = job.barrier_group)
                    )
                  )
            )
            """
        )
    )


def _install_postgresql_guard() -> None:
    op.execute(
        f"""CREATE OR REPLACE FUNCTION public.{_POSTGRES_FUNCTION}()
        RETURNS trigger
        LANGUAGE plpgsql
        SET search_path = pg_catalog, public
        AS $$
        BEGIN
            IF NEW.snapshot_decision_count IS DISTINCT FROM
               OLD.snapshot_decision_count THEN
                RAISE EXCEPTION 'impact assessment snapshot count is immutable';
            END IF;
            IF NEW.decisions_scanned > NEW.snapshot_decision_count THEN
                RAISE EXCEPTION 'impact assessment scan exceeds frozen snapshot';
            END IF;
            IF NEW.status = 'completed'
               AND NEW.decisions_scanned <> NEW.snapshot_decision_count THEN
                RAISE EXCEPTION 'completed impact assessment did not scan its snapshot';
            END IF;
            RETURN NEW;
        END;
        $$"""
    )
    op.execute(f"DROP TRIGGER IF EXISTS {_TRIGGER} ON {_TABLE}")
    op.execute(
        f"""CREATE TRIGGER {_TRIGGER}
        BEFORE UPDATE ON {_TABLE}
        FOR EACH ROW
        EXECUTE FUNCTION public.{_POSTGRES_FUNCTION}()"""
    )


def _install_sqlite_guard() -> None:
    op.execute(f"DROP TRIGGER IF EXISTS {_TRIGGER}")
    op.execute(
        f"""CREATE TRIGGER {_TRIGGER}
        BEFORE UPDATE ON {_TABLE}
        BEGIN
            SELECT RAISE(
                ABORT,
                'impact assessment snapshot count is immutable'
            ) WHERE NEW.snapshot_decision_count IS NOT OLD.snapshot_decision_count;
            SELECT RAISE(
                ABORT,
                'impact assessment scan exceeds frozen snapshot'
            ) WHERE NEW.decisions_scanned > NEW.snapshot_decision_count;
            SELECT RAISE(
                ABORT,
                'completed impact assessment did not scan its snapshot'
            ) WHERE
                NEW.status = 'completed'
                AND NEW.decisions_scanned IS NOT NEW.snapshot_decision_count;
        END"""
    )


def upgrade() -> None:
    dialect = op.get_bind().dialect.name
    op.add_column(
        _TABLE,
        sa.Column(
            "snapshot_decision_count",
            sa.BigInteger(),
            nullable=False,
            server_default="0",
        ),
    )
    _backfill_exact_snapshot_counts()

    if dialect == "postgresql":
        op.create_check_constraint(
            "ck_impact_assessment_snapshot_decision_count",
            _TABLE,
            "snapshot_decision_count >= 0",
        )
        op.create_check_constraint(
            "ck_impact_assessment_scan_within_snapshot",
            _TABLE,
            "decisions_scanned <= snapshot_decision_count",
        )
        op.create_check_constraint(
            "ck_impact_assessment_completed_snapshot_count",
            _TABLE,
            "status <> 'completed' OR decisions_scanned = snapshot_decision_count",
        )
        _install_postgresql_guard()
    elif dialect == "sqlite":
        # SQLite cannot add named table checks without rebuilding the table,
        # which would discard the existing 0043/0049 integrity triggers. The
        # dedicated trigger preserves those guards and enforces the new rules.
        _install_sqlite_guard()


def downgrade() -> None:
    dialect = op.get_bind().dialect.name
    if dialect == "postgresql":
        op.execute(f"DROP TRIGGER IF EXISTS {_TRIGGER} ON {_TABLE}")
        op.execute(f"DROP FUNCTION IF EXISTS public.{_POSTGRES_FUNCTION}()")
        op.drop_constraint(
            "ck_impact_assessment_completed_snapshot_count",
            _TABLE,
            type_="check",
        )
        op.drop_constraint(
            "ck_impact_assessment_scan_within_snapshot",
            _TABLE,
            type_="check",
        )
        op.drop_constraint(
            "ck_impact_assessment_snapshot_decision_count",
            _TABLE,
            type_="check",
        )
    elif dialect == "sqlite":
        op.execute(f"DROP TRIGGER IF EXISTS {_TRIGGER}")
    op.drop_column(_TABLE, "snapshot_decision_count")
