"""Backfill Recorder provenance summaries and build integrity indexes online.

Revision ID: 0042a_recorder_backfill
Revises: 0042_recorder_integrity

The expand revision installs event defaults and the rolling projection trigger.
This revision adds the conservative sentinel to historical run summaries in
independently committed pages and repairs interrupted concurrent index builds.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path
from types import ModuleType

import sqlalchemy as sa
from alembic import context, op

revision = "0042a_recorder_backfill"
down_revision = "0042_recorder_integrity"
branch_labels = None
depends_on = None

_BATCH_SIZE = 2_000
_LEGACY_PRINCIPAL = "lians:principal:v1:legacy-unverified"
_LEGACY_AUTH_METHOD = "legacy_unverified"
_INDEXES = (
    (
        "ix_recorder_events_ingested_by_principal_ref",
        "recorder_events",
        ("ingested_by_principal_ref",),
    ),
    (
        "ix_event_log_recorder_binding_lookup",
        "event_log",
        ("namespace", "op", "content_hash"),
    ),
)


def _expand_module() -> ModuleType:
    path = Path(__file__).with_name("0042_recorder_integrity.py")
    spec = importlib.util.spec_from_file_location("lians_migration_0042_expand", path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Cannot load Recorder expand implementation from {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _drain_run_summaries() -> None:
    statement = sa.text(
        f"""WITH target AS MATERIALIZED (
                SELECT run.id
                  FROM public.recorder_runs AS run
                 WHERE EXISTS (
                           SELECT 1
                             FROM public.recorder_events AS event
                            WHERE event.run_id = run.id
                              AND event.event_hash_version = 1
                       )
                   AND (
                       NOT (
                           COALESCE(
                               run.ingested_by_principal_refs::jsonb, '[]'::jsonb
                           ) ? '{_LEGACY_PRINCIPAL}'
                       )
                       OR NOT (
                           COALESCE(
                               run.ingested_by_auth_methods::jsonb, '[]'::jsonb
                           ) ? '{_LEGACY_AUTH_METHOD}'
                       )
                   )
                 ORDER BY run.id
                 LIMIT :batch_size
                 FOR UPDATE OF run SKIP LOCKED
            )
            UPDATE public.recorder_runs AS run
               SET ingested_by_principal_refs = (
                       SELECT jsonb_agg(value ORDER BY value)::json
                         FROM (
                             SELECT value
                               FROM jsonb_array_elements_text(
                                   COALESCE(
                                       run.ingested_by_principal_refs::jsonb,
                                       '[]'::jsonb
                                   )
                               ) AS existing(value)
                             UNION SELECT '{_LEGACY_PRINCIPAL}'
                         ) AS principals
                   ),
                   ingested_by_auth_methods = (
                       SELECT jsonb_agg(value ORDER BY value)::json
                         FROM (
                             SELECT value
                               FROM jsonb_array_elements_text(
                                   COALESCE(
                                       run.ingested_by_auth_methods::jsonb,
                                       '[]'::jsonb
                                   )
                               ) AS existing(value)
                             UNION SELECT '{_LEGACY_AUTH_METHOD}'
                         ) AS methods
                   )
              FROM target
             WHERE run.id = target.id
            RETURNING run.id"""
    )
    bind = op.get_bind()
    while bind.execute(statement, {"batch_size": _BATCH_SIZE}).first() is not None:
        pass
    remaining = bind.execute(
        sa.text(
            f"""SELECT EXISTS (
                    SELECT 1
                      FROM public.recorder_runs AS run
                     WHERE EXISTS (
                               SELECT 1
                                 FROM public.recorder_events AS event
                                WHERE event.run_id = run.id
                                  AND event.event_hash_version = 1
                           )
                       AND (
                           NOT (
                               COALESCE(
                                   run.ingested_by_principal_refs::jsonb,
                                   '[]'::jsonb
                               ) ? '{_LEGACY_PRINCIPAL}'
                           )
                           OR NOT (
                               COALESCE(
                                   run.ingested_by_auth_methods::jsonb,
                                   '[]'::jsonb
                               ) ? '{_LEGACY_AUTH_METHOD}'
                           )
                       )
                )"""
        )
    ).scalar_one()
    if remaining:
        raise RuntimeError(
            "0042a could not drain every legacy Recorder run; let locked "
            "transactions finish and rerun the online migration"
        )


def _create_or_repair_indexes() -> None:
    bind = op.get_bind()
    for index_name, table, columns in _INDEXES:
        state = bind.execute(
            sa.text(
                """SELECT index.indisvalid,
                          index.indisunique,
                          index.indpred IS NULL AS no_predicate,
                          access_method.amname AS access_method,
                          base.relname AS table_name,
                          array_agg(attribute.attname ORDER BY key.ordinality)
                              AS columns
                   FROM pg_index AS index
                   JOIN pg_class AS relation ON relation.oid = index.indexrelid
                   JOIN pg_namespace AS namespace
                     ON namespace.oid = relation.relnamespace
                   JOIN pg_am AS access_method
                     ON access_method.oid = relation.relam
                   JOIN pg_class AS base ON base.oid = index.indrelid
                   CROSS JOIN LATERAL unnest(index.indkey)
                       WITH ORDINALITY AS key(attnum, ordinality)
                   LEFT JOIN pg_attribute AS attribute
                     ON attribute.attrelid = index.indrelid
                    AND attribute.attnum = key.attnum
                   WHERE namespace.nspname = 'public'
                     AND relation.relname = :index_name
                   GROUP BY index.indisvalid, index.indisunique,
                            (index.indpred IS NULL), access_method.amname,
                            base.relname"""
            ),
            {"index_name": index_name},
        ).mappings().one_or_none()
        if state is not None and (
            bool(state["indisunique"])
            or not bool(state["no_predicate"])
            or str(state["access_method"]) != "btree"
            or str(state["table_name"]) != table
            or list(state["columns"] or []) != list(columns)
        ):
            raise RuntimeError(f"{index_name} exists with an unexpected definition")
        if state is not None and not bool(state["indisvalid"]):
            op.execute(f"DROP INDEX CONCURRENTLY IF EXISTS public.{index_name}")
            state = None
        if state is None:
            op.create_index(
                index_name,
                table,
                list(columns),
                postgresql_concurrently=True,
            )


def upgrade() -> None:
    dialect = op.get_bind().dialect.name
    if dialect == "postgresql":
        if context.is_offline_mode():
            raise RuntimeError(
                "0042a_recorder_backfill requires an online "
                "PostgreSQL connection so bounded pages and concurrent indexes "
                "commit and resume safely. Generate reviewed offline expand DDL "
                "through 0042_recorder_integrity, then run 0042a online."
            )
        with op.get_context().autocommit_block():
            op.execute(
                sa.text(
                    "SELECT set_config('app.current_namespace', '__admin__', false)"
                )
            )
            op.execute(
                sa.text("SELECT set_config('agentmem.barrier_group', '', false)")
            )
            _drain_run_summaries()
            _create_or_repair_indexes()
        return

    _expand_module()._mark_historical_runs_legacy()
    for index_name, table, columns in _INDEXES:
        op.create_index(index_name, table, list(columns))


def downgrade() -> None:
    dialect = op.get_bind().dialect.name
    if dialect == "postgresql":
        if context.is_offline_mode():
            raise RuntimeError("0042a downgrade requires an online PostgreSQL connection")
        with op.get_context().autocommit_block():
            for index_name, _table, _columns in reversed(_INDEXES):
                op.execute(
                    f"DROP INDEX CONCURRENTLY IF EXISTS public.{index_name}"
                )
        return
    for index_name, table, _columns in reversed(_INDEXES):
        op.drop_index(index_name, table_name=table)
