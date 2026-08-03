"""Backfill and contract the OTLP tenant/barrier boundary online.

Revision ID: 0054a_otel_barrier_contract
Revises: 0054_otel_barrier

Legacy and rolling-writer spans are moved from the shared ValidMind scope to
``__legacy_restricted__`` in restart-safe committed pages.  A constraint fences
old writers before the final drain, then scope-aware indexes are built
concurrently and the legacy namespace-wide deduplication index is retired.
"""

from __future__ import annotations

from typing import Any

import sqlalchemy as sa
from alembic import context, op

revision = "0054a_otel_barrier_contract"
down_revision = "0054_otel_barrier"
branch_labels = None
depends_on = None

_BATCH_SIZE = 1_000
_LEGACY_BARRIER = "__legacy_restricted__"
_TRUST_CONSTRAINT = "ck_otel_barrier_scope_trusted"
_OLD_UNIQUE_INDEX = "uq_otel_span_ns_trace_span"
_NEW_UNIQUE_INDEX = "uq_otel_span_scope_trace_span"
_SCOPE_INDEX = "ix_otel_span_scope_received"
_SCOPE_MODEL_INDEX = "ix_otel_span_scope_model_received"


def _claim_postgresql_page() -> int:
    return int(
        op.get_bind().execute(
            sa.text(
                """WITH page AS MATERIALIZED (
                       SELECT span.id
                       FROM public.otel_spans AS span
                       WHERE span.barrier_scope_trusted IS NOT TRUE
                       ORDER BY span.id
                       LIMIT :batch_size
                       FOR UPDATE SKIP LOCKED
                   ), classified AS (
                       UPDATE public.otel_spans AS span
                       SET barrier_group = :legacy_barrier,
                           barrier_scope_trusted = TRUE
                       FROM page
                       WHERE span.id = page.id
                       RETURNING span.id
                   )
                   SELECT COUNT(*) FROM classified"""
            ),
            {
                "batch_size": _BATCH_SIZE,
                "legacy_barrier": _LEGACY_BARRIER,
            },
        ).scalar_one()
    )


def _drain_postgresql() -> None:
    while _claim_postgresql_page():
        pass


def _claim_sqlite_page() -> int:
    result = op.get_bind().execute(
        sa.text(
            """UPDATE otel_spans
               SET barrier_group = :legacy_barrier,
                   barrier_scope_trusted = 1
               WHERE id IN (
                   SELECT id FROM otel_spans
                   WHERE barrier_scope_trusted IS NOT 1
                   ORDER BY id
                   LIMIT :batch_size
               )"""
        ),
        {
            "batch_size": _BATCH_SIZE,
            "legacy_barrier": _LEGACY_BARRIER,
        },
    )
    return max(0, int(result.rowcount or 0))


def _constraint_definition(name: str) -> str | None:
    return op.get_bind().execute(
        sa.text(
            """SELECT pg_get_constraintdef(constraint_record.oid, true)
               FROM pg_constraint AS constraint_record
               JOIN pg_class AS relation
                 ON relation.oid = constraint_record.conrelid
               JOIN pg_namespace AS schema ON schema.oid = relation.relnamespace
               WHERE schema.nspname = 'public'
                 AND relation.relname = 'otel_spans'
                 AND constraint_record.conname = :name"""
        ),
        {"name": name},
    ).scalar_one_or_none()


def _ensure_trust_constraint() -> None:
    definition = _constraint_definition(_TRUST_CONSTRAINT)
    if definition is None:
        op.execute(
            f"""ALTER TABLE public.otel_spans
            ADD CONSTRAINT {_TRUST_CONSTRAINT}
            CHECK (barrier_scope_trusted IS TRUE) NOT VALID"""
        )
        return
    normalized = "".join(str(definition).lower().split())
    if "barrier_scope_trustedistrue" not in normalized:
        raise RuntimeError(
            f"{_TRUST_CONSTRAINT} exists with an unexpected definition"
        )


def _index_state(index_name: str) -> dict[str, Any] | None:
    row = op.get_bind().execute(
        sa.text(
            """SELECT index.indisvalid,
                      index.indisunique,
                      index.indpred IS NULL AS no_predicate,
                      pg_get_expr(index.indpred, index.indrelid, true)
                          AS predicate,
                      access_method.amname AS access_method,
                      base.relname AS table_name,
                      array_agg(
                          pg_get_indexdef(
                              index.indexrelid,
                              key.ordinality,
                              true
                          ) ORDER BY key.ordinality
                      ) AS keys
               FROM pg_index AS index
               JOIN pg_class AS relation ON relation.oid = index.indexrelid
               JOIN pg_namespace AS schema ON schema.oid = relation.relnamespace
               JOIN pg_am AS access_method ON access_method.oid = relation.relam
               JOIN pg_class AS base ON base.oid = index.indrelid
               CROSS JOIN LATERAL generate_series(
                   1, index.indnkeyatts
               ) AS key(ordinality)
               WHERE schema.nspname = 'public'
                 AND relation.relname = :index_name
               GROUP BY index.indisvalid, index.indisunique, index.indpred,
                        index.indexrelid, index.indrelid, access_method.amname,
                        base.relname"""
        ),
        {"index_name": index_name},
    ).mappings().one_or_none()
    return dict(row) if row is not None else None


def _repair_or_create_index(
    index_name: str,
    *,
    unique: bool,
    expected_keys: tuple[str, ...],
    create_sql: str,
    predicate_required: bool = False,
) -> None:
    state = _index_state(index_name)
    if state is not None:
        keys = tuple(str(value) for value in state.get("keys") or ())
        key_match = keys == expected_keys
        if index_name == _NEW_UNIQUE_INDEX and len(keys) == 4:
            expression = "".join(keys[1].lower().split())
            key_match = (
                keys[0] == "namespace"
                and expression.startswith("coalesce(barrier_group,")
                and (
                    "''::charactervarying" in expression
                    or "''::text" in expression
                )
                and keys[2:] == ("trace_id", "span_id")
            )
        predicate = str(state.get("predicate") or "").lower()
        predicate_match = (
            "model_id is not null" in predicate
            if predicate_required
            else bool(state.get("no_predicate"))
        )
        if (
            bool(state.get("indisunique")) != unique
            or str(state.get("access_method")) != "btree"
            or str(state.get("table_name")) != "otel_spans"
            or not key_match
            or not predicate_match
        ):
            raise RuntimeError(f"{index_name} exists with an unexpected definition")
        if bool(state.get("indisvalid")):
            return
        op.execute(f"DROP INDEX CONCURRENTLY IF EXISTS public.{index_name}")
    op.execute(create_sql)


def _build_postgresql_indexes() -> None:
    _repair_or_create_index(
        _SCOPE_INDEX,
        unique=False,
        expected_keys=("namespace", "barrier_group", "received_at", "id"),
        create_sql=f"""CREATE INDEX CONCURRENTLY {_SCOPE_INDEX}
        ON public.otel_spans (namespace, barrier_group, received_at, id)""",
    )
    _repair_or_create_index(
        _SCOPE_MODEL_INDEX,
        unique=False,
        expected_keys=(
            "namespace",
            "barrier_group",
            "model_id",
            "received_at",
            "id",
        ),
        create_sql=f"""CREATE INDEX CONCURRENTLY {_SCOPE_MODEL_INDEX}
        ON public.otel_spans (
            namespace, barrier_group, model_id, received_at, id
        ) WHERE model_id IS NOT NULL""",
        predicate_required=True,
    )
    _repair_or_create_index(
        _NEW_UNIQUE_INDEX,
        unique=True,
        expected_keys=(),
        create_sql=f"""CREATE UNIQUE INDEX CONCURRENTLY {_NEW_UNIQUE_INDEX}
        ON public.otel_spans (
            namespace, COALESCE(barrier_group, ''), trace_id, span_id
        )""",
    )
    op.execute(f"DROP INDEX CONCURRENTLY IF EXISTS public.{_OLD_UNIQUE_INDEX}")


def _assert_postgresql_rls_posture() -> None:
    # Keep this database-side postflight aligned with the runtime catalog check.
    # The allowlist is limited to the two pre-authentication bootstrap indexes.
    op.execute(
        """DO $$
        DECLARE
            violation text;
        BEGIN
            SELECT relation.relname || chr(58) || 'namespace_rls'
              INTO violation
              FROM pg_class AS relation
              JOIN pg_namespace AS schema
                ON schema.oid = relation.relnamespace
             WHERE schema.nspname = 'public'
               AND relation.relkind IN ('r', 'p')
               AND relation.relname NOT IN ('api_keys', 'identity_bindings')
               AND EXISTS (
                   SELECT 1 FROM pg_attribute AS attribute
                    WHERE attribute.attrelid = relation.oid
                      AND attribute.attname = 'namespace'
                      AND attribute.attnum > 0
                      AND NOT attribute.attisdropped
               )
               AND (
                   NOT relation.relrowsecurity
                   OR NOT relation.relforcerowsecurity
                   OR NOT EXISTS (
                       SELECT 1 FROM pg_policy AS policy
                        WHERE policy.polrelid = relation.oid
                          AND policy.polpermissive
                          AND position(
                              'app.current_namespace' IN concat(
                                  pg_get_expr(
                                      policy.polqual, policy.polrelid, true
                                  ),
                                  ' ',
                                  pg_get_expr(
                                      policy.polwithcheck,
                                      policy.polrelid,
                                      true
                                  )
                              )
                          ) > 0
                   )
               )
             ORDER BY relation.relname
             LIMIT 1;
            IF violation IS NOT NULL THEN
                RAISE EXCEPTION 'tenant-isolation postflight failed: %', violation;
            END IF;

            SELECT relation.relname || chr(58) || 'barrier_rls'
              INTO violation
              FROM pg_class AS relation
              JOIN pg_namespace AS schema
                ON schema.oid = relation.relnamespace
             WHERE schema.nspname = 'public'
               AND relation.relkind IN ('r', 'p')
               AND relation.relname NOT IN ('api_keys', 'identity_bindings')
               AND EXISTS (
                   SELECT 1 FROM pg_attribute AS attribute
                    WHERE attribute.attrelid = relation.oid
                      AND attribute.attname = 'barrier_group'
                      AND attribute.attnum > 0
                      AND NOT attribute.attisdropped
               )
               AND NOT EXISTS (
                   SELECT 1 FROM pg_policy AS policy
                    WHERE policy.polrelid = relation.oid
                      AND NOT policy.polpermissive
                      AND position(
                          'agentmem.barrier_group' IN concat(
                              pg_get_expr(
                                  policy.polqual, policy.polrelid, true
                              ),
                              ' ',
                              pg_get_expr(
                                  policy.polwithcheck, policy.polrelid, true
                              )
                          )
                      ) > 0
               )
             ORDER BY relation.relname
             LIMIT 1;
            IF violation IS NOT NULL THEN
                RAISE EXCEPTION 'tenant-isolation postflight failed: %', violation;
            END IF;

            SELECT relation.relname || chr(58) || 'unbarriered_rls'
              INTO violation
              FROM pg_class AS relation
              JOIN pg_namespace AS schema
                ON schema.oid = relation.relnamespace
             WHERE schema.nspname = 'public'
               AND relation.relname = 'validmind_model_links'
               AND NOT EXISTS (
                   SELECT 1 FROM pg_policy AS policy
                    WHERE policy.polrelid = relation.oid
                      AND NOT policy.polpermissive
                      AND position(
                          'agentmem.barrier_group' IN concat(
                              pg_get_expr(
                                  policy.polqual, policy.polrelid, true
                              ),
                              ' ',
                              pg_get_expr(
                                  policy.polwithcheck, policy.polrelid, true
                              )
                          )
                      ) > 0
               )
             LIMIT 1;
            IF violation IS NOT NULL THEN
                RAISE EXCEPTION 'tenant-isolation postflight failed: %', violation;
            END IF;
        END
        $$"""
    )


def _postgresql_upgrade() -> None:
    with op.get_context().autocommit_block():
        op.execute(
            sa.text("SELECT set_config('app.current_namespace', '__admin__', false)")
        )
        op.execute(
            sa.text("SELECT set_config('agentmem.barrier_group', '', false)")
        )
        _drain_postgresql()

    # NOT VALID still constrains every new row. Once committed, a rolling old
    # writer can no longer create an unclassified span; the second drain closes
    # the small pre-constraint race without a snapshot/high-water assumption.
    _ensure_trust_constraint()
    with op.get_context().autocommit_block():
        op.execute(
            sa.text("SELECT set_config('app.current_namespace', '__admin__', false)")
        )
        op.execute(
            sa.text("SELECT set_config('agentmem.barrier_group', '', false)")
        )
        _drain_postgresql()
        remaining = op.get_bind().execute(
            sa.text(
                """SELECT EXISTS (
                       SELECT 1 FROM public.otel_spans
                       WHERE barrier_scope_trusted IS NOT TRUE
                   )"""
            )
        ).scalar_one()
        if remaining:
            raise RuntimeError(
                "0054a could not drain every unclassified OTLP row; let locked "
                "transactions finish and rerun the online migration"
            )
        _build_postgresql_indexes()

    op.execute(
        f"ALTER TABLE public.otel_spans VALIDATE CONSTRAINT {_TRUST_CONSTRAINT}"
    )
    op.alter_column(
        "otel_spans",
        "barrier_scope_trusted",
        existing_type=sa.Boolean(),
        nullable=False,
        server_default=None,
    )
    _assert_postgresql_rls_posture()


def _sqlite_upgrade() -> None:
    with op.get_context().autocommit_block():
        while _claim_sqlite_page():
            pass
    remaining = int(
        op.get_bind().execute(
            sa.text(
                """SELECT COUNT(*) FROM otel_spans
                   WHERE barrier_scope_trusted IS NOT 1"""
            )
        ).scalar_one()
    )
    if remaining:
        raise RuntimeError(f"0054a left {remaining} unclassified SQLite OTLP rows")

    # Avoid rebuilding a potentially large local trace table. These guards are
    # SQLite's equivalent of the PostgreSQL TRUE check + NOT NULL contract.
    op.execute(
        """CREATE TRIGGER trg_otel_barrier_trusted_insert
        BEFORE INSERT ON otel_spans
        WHEN NEW.barrier_scope_trusted IS NOT 1
        BEGIN
            SELECT RAISE(ABORT, 'OTLP barrier provenance must be explicit');
        END"""
    )
    op.execute(
        """CREATE TRIGGER trg_otel_barrier_trusted_update
        BEFORE UPDATE OF barrier_scope_trusted ON otel_spans
        WHEN NEW.barrier_scope_trusted IS NOT 1
        BEGIN
            SELECT RAISE(ABORT, 'OTLP barrier provenance cannot be cleared');
        END"""
    )
    op.execute(
        f"""CREATE UNIQUE INDEX {_NEW_UNIQUE_INDEX}
        ON otel_spans (
            namespace, COALESCE(barrier_group, ''), trace_id, span_id
        )"""
    )
    op.create_index(
        _SCOPE_INDEX,
        "otel_spans",
        ["namespace", "barrier_group", "received_at", "id"],
    )
    op.create_index(
        _SCOPE_MODEL_INDEX,
        "otel_spans",
        ["namespace", "barrier_group", "model_id", "received_at", "id"],
        sqlite_where=sa.text("model_id IS NOT NULL"),
    )
    op.drop_index(_OLD_UNIQUE_INDEX, table_name="otel_spans")


def upgrade() -> None:
    if context.is_offline_mode():
        raise RuntimeError(
            "0054a_otel_barrier_contract requires an online connection so "
            "bounded legacy pages and PostgreSQL concurrent indexes commit "
            "independently. Generate reviewed offline DDL only through "
            "0054_otel_barrier, then run 0054a online after old writers drain."
        )
    dialect = op.get_bind().dialect.name
    if dialect == "postgresql":
        _postgresql_upgrade()
    elif dialect == "sqlite":
        _sqlite_upgrade()
    else:
        raise RuntimeError(f"OTLP barrier backfill is unsupported on {dialect}")


def _assert_downgrade_safe() -> None:
    qualified = (
        "public.otel_spans"
        if op.get_bind().dialect.name == "postgresql"
        else "otel_spans"
    )
    scoped = op.get_bind().execute(
        sa.text(
            f"""SELECT EXISTS (
                    SELECT 1 FROM {qualified}
                    WHERE barrier_group IS NOT NULL
                )"""
        )
    ).scalar_one()
    if scoped:
        raise RuntimeError(
            "0054a downgrade refused: removing scope-aware uniqueness or "
            "provenance would merge protected OTLP scopes; use a forward fix "
            "or restore a pre-0054 backup"
        )


def downgrade() -> None:
    if context.is_offline_mode():
        raise RuntimeError("0054a downgrade requires an online connection")
    _assert_downgrade_safe()
    dialect = op.get_bind().dialect.name
    if dialect == "postgresql":
        with op.get_context().autocommit_block():
            _repair_or_create_index(
                _OLD_UNIQUE_INDEX,
                unique=True,
                expected_keys=("namespace", "trace_id", "span_id"),
                create_sql=f"""CREATE UNIQUE INDEX CONCURRENTLY {_OLD_UNIQUE_INDEX}
                ON public.otel_spans (namespace, trace_id, span_id)""",
            )
            for index_name in (
                _NEW_UNIQUE_INDEX,
                _SCOPE_MODEL_INDEX,
                _SCOPE_INDEX,
            ):
                op.execute(
                    f"DROP INDEX CONCURRENTLY IF EXISTS public.{index_name}"
                )
        op.alter_column(
            "otel_spans",
            "barrier_scope_trusted",
            existing_type=sa.Boolean(),
            nullable=True,
            server_default=None,
        )
        op.drop_constraint(_TRUST_CONSTRAINT, "otel_spans", type_="check")
        return

    op.execute("DROP TRIGGER IF EXISTS trg_otel_barrier_trusted_update")
    op.execute("DROP TRIGGER IF EXISTS trg_otel_barrier_trusted_insert")
    op.create_index(
        _OLD_UNIQUE_INDEX,
        "otel_spans",
        ["namespace", "trace_id", "span_id"],
        unique=True,
    )
    op.drop_index(_NEW_UNIQUE_INDEX, table_name="otel_spans")
    op.drop_index(_SCOPE_MODEL_INDEX, table_name="otel_spans")
    op.drop_index(_SCOPE_INDEX, table_name="otel_spans")
