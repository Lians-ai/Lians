"""Build the DecisionRecord provenance index without blocking writers.

Revision ID: 0041a_decision_integrity_idx
Revises: 0041_decision_record_integrity

The established-table index is isolated from expand DDL so an interrupted
concurrent build can be detected, repaired, and resumed without replaying the
column or constraint phase.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import context, op

revision = "0041a_decision_integrity_idx"
down_revision = "0041_decision_record_integrity"
branch_labels = None
depends_on = None

_INDEX = "ix_decision_records_recorded_by_principal_ref"


def _postgresql_index_online() -> None:
    bind = op.get_bind()
    with op.get_context().autocommit_block():
        state = bind.execute(
            sa.text(
                """SELECT index.indisvalid,
                          index.indisunique,
                          index.indpred IS NULL AS no_predicate,
                          access_method.amname AS access_method,
                          array_agg(attribute.attname ORDER BY key.ordinality)
                              AS columns
                   FROM pg_index AS index
                   JOIN pg_class AS relation ON relation.oid = index.indexrelid
                   JOIN pg_namespace AS namespace
                     ON namespace.oid = relation.relnamespace
                   JOIN pg_am AS access_method
                     ON access_method.oid = relation.relam
                   CROSS JOIN LATERAL unnest(index.indkey)
                       WITH ORDINALITY AS key(attnum, ordinality)
                   LEFT JOIN pg_attribute AS attribute
                     ON attribute.attrelid = index.indrelid
                    AND attribute.attnum = key.attnum
                   WHERE namespace.nspname = 'public'
                     AND relation.relname = :index_name
                   GROUP BY index.indisvalid, index.indisunique,
                            (index.indpred IS NULL), access_method.amname"""
            ),
            {"index_name": _INDEX},
        ).mappings().one_or_none()
        if state is not None and (
            bool(state["indisunique"])
            or not bool(state["no_predicate"])
            or str(state["access_method"]) != "btree"
            or list(state["columns"] or []) != ["recorded_by_principal_ref"]
        ):
            raise RuntimeError(f"{_INDEX} exists with an unexpected definition")
        if state is not None and not bool(state["indisvalid"]):
            op.execute(f"DROP INDEX CONCURRENTLY IF EXISTS public.{_INDEX}")
            state = None
        if state is None:
            op.create_index(
                _INDEX,
                "decision_records",
                ["recorded_by_principal_ref"],
                postgresql_concurrently=True,
            )


def upgrade() -> None:
    dialect = op.get_bind().dialect.name
    if dialect == "postgresql":
        if context.is_offline_mode():
            raise RuntimeError(
                "0041a_decision_integrity_idx requires an online "
                "PostgreSQL connection so an interrupted concurrent index can "
                "be inspected and repaired. Generate reviewed offline expand "
                "DDL through 0041_decision_record_integrity, then run 0041a online."
            )
        _postgresql_index_online()
        return
    op.create_index(
        _INDEX,
        "decision_records",
        ["recorded_by_principal_ref"],
    )


def downgrade() -> None:
    if op.get_bind().dialect.name == "postgresql":
        if context.is_offline_mode():
            raise RuntimeError("0041a downgrade requires an online PostgreSQL connection")
        with op.get_context().autocommit_block():
            op.execute(f"DROP INDEX CONCURRENTLY IF EXISTS public.{_INDEX}")
        return
    op.drop_index(_INDEX, table_name="decision_records")
