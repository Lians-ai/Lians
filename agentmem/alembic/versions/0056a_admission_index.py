"""Build the pending-admission review index online.

Revision ID: 0056a_admission_index
Revises: 0056_auth_lookup_expand

PostgreSQL uses a resumable concurrent build. Offline SQL is refused because it
cannot preserve the required transaction boundary for CREATE INDEX CONCURRENTLY.
"""

from __future__ import annotations

from typing import Any

import sqlalchemy as sa
from alembic import context, op

revision = "0056a_admission_index"
down_revision = "0056_auth_lookup_expand"
branch_labels = None
depends_on = None

_INDEX = "ix_pending_admission_ns_status_barrier_created_id"
_EXPECTED_KEYS = (
    "namespace",
    "status",
    "barrier_group",
    "created_at",
    "id",
)


def _index_state() -> dict[str, Any] | None:
    row = op.get_bind().execute(
        sa.text(
            """SELECT index.indisvalid,
                      index.indisunique,
                      index.indpred IS NULL AS no_predicate,
                      access_method.amname AS access_method,
                      base.relname AS table_name,
                      array_agg(
                          pg_get_indexdef(
                              index.indexrelid,
                              key.ordinality,
                              true
                          ) ORDER BY key.ordinality
                      ) AS keys
               FROM pg_catalog.pg_index AS index
               JOIN pg_catalog.pg_class AS relation
                 ON relation.oid = index.indexrelid
               JOIN pg_catalog.pg_namespace AS schema
                 ON schema.oid = relation.relnamespace
               JOIN pg_catalog.pg_am AS access_method
                 ON access_method.oid = relation.relam
               JOIN pg_catalog.pg_class AS base
                 ON base.oid = index.indrelid
               CROSS JOIN LATERAL generate_series(
                   1, index.indnkeyatts
               ) AS key(ordinality)
               WHERE schema.nspname = 'public'
                 AND relation.relname = :index_name
               GROUP BY index.indisvalid, index.indisunique, index.indpred,
                        index.indexrelid, access_method.amname, base.relname"""
        ),
        {"index_name": _INDEX},
    ).mappings().one_or_none()
    return dict(row) if row is not None else None


def _repair_or_create_postgresql_index() -> None:
    state = _index_state()
    if state is not None:
        keys = tuple(str(value) for value in state.get("keys") or ())
        if (
            bool(state.get("indisunique"))
            or not bool(state.get("no_predicate"))
            or str(state.get("access_method")) != "btree"
            or str(state.get("table_name")) != "pending_admissions"
            or keys != _EXPECTED_KEYS
        ):
            raise RuntimeError(f"{_INDEX} exists with an unexpected definition")
        if bool(state.get("indisvalid")):
            return
        op.execute(f"DROP INDEX CONCURRENTLY IF EXISTS public.{_INDEX}")
    op.execute(
        f"""CREATE INDEX CONCURRENTLY {_INDEX}
        ON public.pending_admissions (
            namespace, status, barrier_group, created_at, id
        )"""
    )


def upgrade() -> None:
    if context.is_offline_mode():
        raise RuntimeError(
            "0056a_admission_index requires an online connection so the "
            "pending-admission index can be built and repaired concurrently"
        )
    if op.get_bind().dialect.name == "postgresql":
        with op.get_context().autocommit_block():
            _repair_or_create_postgresql_index()
    else:
        op.create_index(
            _INDEX,
            "pending_admissions",
            list(_EXPECTED_KEYS),
            unique=False,
        )


def downgrade() -> None:
    if context.is_offline_mode():
        raise RuntimeError(
            "0056a_admission_index downgrade requires an online connection"
        )
    if op.get_bind().dialect.name == "postgresql":
        with op.get_context().autocommit_block():
            op.execute(f"DROP INDEX CONCURRENTLY IF EXISTS public.{_INDEX}")
    else:
        op.drop_index(_INDEX, table_name="pending_admissions")
