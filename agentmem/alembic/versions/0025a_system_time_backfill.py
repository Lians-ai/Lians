"""Resumably backfill system-time validity before contracting the column.

Revision ID: 0025a_system_time_backfill
Revises: 0025_system_time_validity

The expand revision establishes nullable columns and a default for new writers.
This revision advances existing rows in bounded committed pages, so a timeout or
operator cancellation resumes from remaining NULL rows instead of replaying one
database-wide transaction. The final index is built concurrently and repairs an
invalid index left by an interrupted PostgreSQL build.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import context, op

revision = "0025a_system_time_backfill"
down_revision = "0025_system_time_validity"
branch_labels = None
depends_on = None

_BATCH_SIZE = 5_000
_INDEX = "ix_memories_ns_agent_system"
_HELPER_INDEXES = (
    (
        "ix_0025a_event_memory_close_lookup",
        """CREATE INDEX CONCURRENTLY ix_0025a_event_memory_close_lookup
        ON public.event_log (namespace, memory_id, created_at)
        WHERE op IN ('supersede', 'conflict_resolved')""",
    ),
    (
        "ix_0025a_event_payload_close_lookup",
        """CREATE INDEX CONCURRENTLY ix_0025a_event_payload_close_lookup
        ON public.event_log (
            namespace,
            ((payload ->> 'memory_invalidated')),
            created_at
        ) WHERE op IN ('supersede', 'conflict_resolved')""",
    ),
)


def _drain_postgresql(statement: sa.TextClause) -> None:
    bind = op.get_bind()
    while True:
        rows = bind.execute(statement, {"batch_size": _BATCH_SIZE}).fetchall()
        if not rows:
            return


def _index_valid(index_name: str) -> bool | None:
    return op.get_bind().execute(
        sa.text(
            """SELECT index.indisvalid
               FROM pg_index AS index
               JOIN pg_class AS relation ON relation.oid = index.indexrelid
               JOIN pg_namespace AS namespace
                 ON namespace.oid = relation.relnamespace
               WHERE namespace.nspname = 'public'
                 AND relation.relname = :index_name"""
        ),
        {"index_name": index_name},
    ).scalar_one_or_none()


def _create_or_repair_helper_indexes() -> None:
    for index_name, create_sql in _HELPER_INDEXES:
        valid = _index_valid(index_name)
        if valid is False:
            op.execute(f"DROP INDEX CONCURRENTLY IF EXISTS public.{index_name}")
            valid = None
        if valid is None:
            op.execute(create_sql)


def _drop_helper_indexes() -> None:
    for index_name, _create_sql in _HELPER_INDEXES:
        op.execute(f"DROP INDEX CONCURRENTLY IF EXISTS public.{index_name}")


def _postgresql_backfill_online() -> None:
    with op.get_context().autocommit_block():
        # Core memory/audit tables are FORCE RLS. Keep the dedicated migration
        # connection in the explicit all-tenant, unbarriered operator scope
        # across independently committed pages.
        op.execute(
            sa.text("SELECT set_config('app.current_namespace', '__admin__', false)")
        )
        op.execute(
            sa.text("SELECT set_config('agentmem.barrier_group', '', false)")
        )
        _create_or_repair_helper_indexes()
        _drain_postgresql(
            sa.text(
                """WITH target AS (
                       SELECT id
                       FROM memories
                       WHERE system_valid_from IS NULL
                       ORDER BY id
                       LIMIT :batch_size
                       FOR UPDATE SKIP LOCKED
                   )
                   UPDATE memories AS memory
                   SET system_valid_from = memory.ingestion_time
                   FROM target
                   WHERE memory.id = target.id
                   RETURNING memory.id"""
            )
        )
        _drain_postgresql(
            sa.text(
                """WITH target AS (
                       SELECT id, namespace, ingestion_time
                       FROM memories
                       WHERE valid_to IS NOT NULL
                         AND system_valid_to IS NULL
                       ORDER BY id
                       LIMIT :batch_size
                       FOR UPDATE SKIP LOCKED
                   ), closing_event AS (
                       SELECT target.id,
                              target.ingestion_time,
                              (
                                  SELECT MIN(event.created_at)
                                  FROM event_log AS event
                                  WHERE event.namespace = target.namespace
                                    AND event.memory_id = target.id
                                    AND event.op IN (
                                      'supersede', 'conflict_resolved'
                                    )
                              ) AS direct_time,
                              (
                                  SELECT MIN(event.created_at)
                                  FROM event_log AS event
                                  WHERE event.namespace = target.namespace
                                    AND event.payload ->> 'memory_invalidated' =
                                       CAST(target.id AS TEXT)
                                    AND event.op IN (
                                      'supersede', 'conflict_resolved'
                                    )
                              ) AS payload_time
                       FROM target
                   )
                   UPDATE memories AS memory
                   SET system_valid_to = COALESCE(
                       LEAST(
                           closing_event.direct_time,
                           closing_event.payload_time
                       ),
                       closing_event.direct_time,
                       closing_event.payload_time,
                       closing_event.ingestion_time
                   )
                   FROM closing_event
                   WHERE memory.id = closing_event.id
                   RETURNING memory.id"""
            )
        )
        remaining = op.get_bind().execute(
            sa.text(
                """SELECT EXISTS (
                       SELECT 1
                       FROM public.memories
                       WHERE system_valid_from IS NULL
                          OR (valid_to IS NOT NULL AND system_valid_to IS NULL)
                   )"""
            )
        ).scalar_one()
        if remaining:
            raise RuntimeError(
                "0025a_system_time_backfill could not drain every legacy row; "
                "a row is locked or has no usable ingestion_time. Resolve the row "
                "and rerun the online migration."
            )
        _drop_helper_indexes()


def _postgresql_index_online() -> None:
    bind = op.get_bind()
    with op.get_context().autocommit_block():
        valid = bind.execute(
            sa.text(
                """SELECT index.indisvalid
                   FROM pg_index AS index
                   JOIN pg_class AS relation ON relation.oid = index.indexrelid
                   JOIN pg_namespace AS namespace
                     ON namespace.oid = relation.relnamespace
                   WHERE namespace.nspname = 'public'
                     AND relation.relname = :index_name"""
            ),
            {"index_name": _INDEX},
        ).scalar_one_or_none()
        if valid is False:
            op.execute(f"DROP INDEX CONCURRENTLY IF EXISTS public.{_INDEX}")
            valid = None
        if valid is None:
            op.create_index(
                _INDEX,
                "memories",
                [
                    "namespace",
                    "agent_id",
                    "system_valid_from",
                    "system_valid_to",
                ],
                postgresql_concurrently=True,
            )


def upgrade() -> None:
    dialect = op.get_bind().dialect.name
    if dialect == "postgresql":
        if context.is_offline_mode():
            raise RuntimeError(
                "0025a_system_time_backfill requires an online PostgreSQL "
                "connection so each bounded page and concurrent index operation "
                "commits independently. Generate reviewed offline DDL only through "
                "0025_system_time_validity, then run 0025a online."
            )
        _postgresql_backfill_online()
        _postgresql_index_online()
        op.execute(
            """ALTER TABLE public.memories
            ADD CONSTRAINT ck_0025a_system_valid_from_present
            CHECK (system_valid_from IS NOT NULL) NOT VALID"""
        )
        op.execute(
            """ALTER TABLE public.memories
            VALIDATE CONSTRAINT ck_0025a_system_valid_from_present"""
        )
        op.alter_column(
            "memories",
            "system_valid_from",
            existing_type=sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        )
        op.drop_constraint(
            "ck_0025a_system_valid_from_present",
            "memories",
            type_="check",
        )
        return

    op.execute(
        "UPDATE memories SET system_valid_from = ingestion_time "
        "WHERE system_valid_from IS NULL"
    )
    op.execute(
        "UPDATE memories SET system_valid_to = ingestion_time "
        "WHERE valid_to IS NOT NULL AND system_valid_to IS NULL"
    )
    with op.batch_alter_table("memories") as batch:
        batch.alter_column(
            "system_valid_from",
            existing_type=sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        )
    op.create_index(
        _INDEX,
        "memories",
        ["namespace", "agent_id", "system_valid_from", "system_valid_to"],
    )


def downgrade() -> None:
    op.drop_index(_INDEX, table_name="memories")
    if op.get_bind().dialect.name == "sqlite":
        with op.batch_alter_table("memories") as batch:
            batch.alter_column(
                "system_valid_from",
                existing_type=sa.DateTime(timezone=True),
                nullable=True,
                server_default=sa.func.now(),
            )
    else:
        op.alter_column(
            "memories",
            "system_valid_from",
            existing_type=sa.DateTime(timezone=True),
            nullable=True,
            server_default=sa.func.now(),
        )
