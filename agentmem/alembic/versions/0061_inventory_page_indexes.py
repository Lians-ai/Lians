"""Build workload-credential and metering inventory page indexes online.

Revision ID: 0061_inventory_page_indexes
Revises: 0060_lineage_graph_indexes
"""

from __future__ import annotations

from typing import Any

import sqlalchemy as sa
from alembic import context, op

revision = "0061_inventory_page_indexes"
down_revision = "0060_lineage_graph_indexes"
branch_labels = None
depends_on = None

_SPECS = {
    "ix_api_keys_workload_inventory_page": {
        "table": "api_keys",
        "keys": (
            "namespace",
            "provisioning_source",
            "barrier_group",
            "created_at",
            "id",
        ),
        "sql": """CREATE INDEX CONCURRENTLY ix_api_keys_workload_inventory_page
                  ON public.api_keys
                  (namespace, provisioning_source, barrier_group, created_at, id)""",
    },
    "ix_metering_event_admin_all_page": {
        "table": "metering_events",
        "keys": ("updated_at", "id"),
        "sql": """CREATE INDEX CONCURRENTLY ix_metering_event_admin_all_page
                  ON public.metering_events (updated_at, id)""",
    },
    "ix_metering_event_admin_namespace_page": {
        "table": "metering_events",
        "keys": ("namespace", "updated_at", "id"),
        "sql": """CREATE INDEX CONCURRENTLY ix_metering_event_admin_namespace_page
                  ON public.metering_events (namespace, updated_at, id)""",
    },
    "ix_metering_event_admin_status_page": {
        "table": "metering_events",
        "keys": ("status", "updated_at", "id"),
        "sql": """CREATE INDEX CONCURRENTLY ix_metering_event_admin_status_page
                  ON public.metering_events (status, updated_at, id)""",
    },
}


def _index_state(index_name: str) -> dict[str, Any] | None:
    row = op.get_bind().execute(
        sa.text(
            """SELECT index.indisvalid,
                      index.indisunique,
                      access_method.amname AS access_method,
                      base.relname AS table_name,
                      pg_get_expr(index.indpred, index.indrelid, true) AS predicate,
                      array_agg(
                          pg_get_indexdef(index.indexrelid, key.ordinality, true)
                          ORDER BY key.ordinality
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
               GROUP BY index.indisvalid, index.indisunique, access_method.amname,
                        base.relname, index.indpred, index.indrelid"""
        ),
        {"index_name": index_name},
    ).mappings().one_or_none()
    return dict(row) if row is not None else None


def _matches(index_name: str, state: dict[str, Any]) -> bool:
    spec = _SPECS[index_name]
    return bool(
        state.get("indisvalid")
        and not state.get("indisunique")
        and state.get("access_method") == "btree"
        and state.get("table_name") == spec["table"]
        and state.get("predicate") is None
        and tuple(str(value) for value in state.get("keys") or ()) == spec["keys"]
    )


def _repair_or_create(index_name: str) -> None:
    state = _index_state(index_name)
    if state is not None:
        if _matches(index_name, state):
            return
        if state.get("indisvalid"):
            raise RuntimeError(f"{index_name} exists with an unexpected definition")
        op.execute(f"DROP INDEX CONCURRENTLY IF EXISTS public.{index_name}")
    op.execute(_SPECS[index_name]["sql"])


def upgrade() -> None:
    if context.is_offline_mode():
        raise RuntimeError(
            "0061_inventory_page_indexes requires an online connection for "
            "resumable concurrent index builds"
        )
    if op.get_bind().dialect.name == "postgresql":
        with op.get_context().autocommit_block():
            for index_name in _SPECS:
                _repair_or_create(index_name)
        return
    op.create_index(
        "ix_api_keys_workload_inventory_page",
        "api_keys",
        ["namespace", "provisioning_source", "barrier_group", "created_at", "id"],
    )
    op.create_index(
        "ix_metering_event_admin_all_page",
        "metering_events",
        ["updated_at", "id"],
    )
    op.create_index(
        "ix_metering_event_admin_namespace_page",
        "metering_events",
        ["namespace", "updated_at", "id"],
    )
    op.create_index(
        "ix_metering_event_admin_status_page",
        "metering_events",
        ["status", "updated_at", "id"],
    )


def downgrade() -> None:
    if context.is_offline_mode():
        raise RuntimeError(
            "0061_inventory_page_indexes downgrade requires an online connection"
        )
    if op.get_bind().dialect.name == "postgresql":
        with op.get_context().autocommit_block():
            for index_name in reversed(tuple(_SPECS)):
                op.execute(f"DROP INDEX CONCURRENTLY IF EXISTS public.{index_name}")
        return
    op.drop_index("ix_metering_event_admin_status_page", table_name="metering_events")
    op.drop_index(
        "ix_metering_event_admin_namespace_page", table_name="metering_events"
    )
    op.drop_index("ix_metering_event_admin_all_page", table_name="metering_events")
    op.drop_index("ix_api_keys_workload_inventory_page", table_name="api_keys")
