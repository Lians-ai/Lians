"""Copy legacy memory idempotency claims in committed keyset pages.

Revision ID: 0046a_idempotency_backfill
Revises: 0046_operation_idempotency

Revision 0046 keeps the raw compatibility table and installs its synchronous
mirror before this revision starts. A durable cursor lets large historical
tables resume without retaining raw keys in Python or replaying completed pages.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path
from types import ModuleType

import sqlalchemy as sa
from alembic import context, op

revision = "0046a_idempotency_backfill"
down_revision = "0046_operation_idempotency"
branch_labels = None
depends_on = None

_BATCH_SIZE = 2_000
_PROGRESS_TABLE = "lians_migration_0046_idempotency_progress"
_LEGACY_OPERATION = "memory.create"
_LEGACY_DIGEST = "0" * 64


def _expand_module() -> ModuleType:
    path = Path(__file__).with_name("0046_operation_idempotency.py")
    spec = importlib.util.spec_from_file_location("lians_migration_0046_expand", path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Cannot load idempotency expand implementation from {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _ensure_progress_table() -> None:
    if sa.inspect(op.get_bind()).has_table(_PROGRESS_TABLE):
        return
    op.create_table(
        _PROGRESS_TABLE,
        sa.Column("singleton", sa.Boolean(), primary_key=True),
        sa.Column("last_key", sa.Text(), nullable=True),
        sa.Column("last_namespace", sa.Text(), nullable=True),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.CheckConstraint("singleton", name="ck_0046_progress_singleton"),
    )
    op.execute(
        f"INSERT INTO public.{_PROGRESS_TABLE} (singleton) VALUES (true)"
    )
    op.execute(f"REVOKE ALL ON public.{_PROGRESS_TABLE} FROM PUBLIC")
    op.execute(f"REVOKE ALL ON public.{_PROGRESS_TABLE} FROM lians_runtime")


def _hash_sql(key_expression: str, namespace_expression: str) -> str:
    return f"""encode(
        public.digest(
            convert_to('lians/operation-idempotency-key/v1', 'UTF8') ||
            decode('00', 'hex') ||
            convert_to({namespace_expression}, 'UTF8') ||
            decode('00', 'hex') ||
            convert_to('{_LEGACY_OPERATION}', 'UTF8') ||
            decode('00', 'hex') ||
            convert_to({key_expression}, 'UTF8'),
            'sha256'
        ),
        'hex'
    )"""


def _copy_page() -> tuple[int, bool]:
    key_hash = _hash_sql("legacy.key", "legacy.namespace")
    row = op.get_bind().execute(
        sa.text(
            f"""WITH cursor AS MATERIALIZED (
                    SELECT last_key, last_namespace
                      FROM public.{_PROGRESS_TABLE}
                     WHERE singleton = true
                     FOR UPDATE
                ), page AS MATERIALIZED (
                    SELECT legacy.key, legacy.namespace,
                           legacy.memory_id, legacy.created_at,
                           {key_hash} AS key_hash
                      FROM public.idempotency_keys AS legacy
                      CROSS JOIN cursor
                     WHERE cursor.last_key IS NULL
                        OR legacy.key > cursor.last_key
                        OR (
                            legacy.key = cursor.last_key
                            AND legacy.namespace > cursor.last_namespace
                        )
                     ORDER BY legacy.key, legacy.namespace
                     LIMIT :batch_size
                ), inserted AS (
                    INSERT INTO public.operation_idempotency (
                        namespace, operation, key_hash, request_digest,
                        legacy_unverified_request, resource_kind, resource_ids,
                        response_status, created_at
                    )
                    SELECT page.namespace, '{_LEGACY_OPERATION}', page.key_hash,
                           '{_LEGACY_DIGEST}', TRUE, 'memory',
                           jsonb_build_array(page.memory_id::text)::json,
                           200, page.created_at
                      FROM page
                    ON CONFLICT (namespace, operation, key_hash) DO NOTHING
                    RETURNING namespace, operation, key_hash, request_digest,
                              legacy_unverified_request, resource_kind,
                              resource_ids
                ), mismatch AS MATERIALIZED (
                    SELECT 1
                      FROM page
                      LEFT JOIN public.operation_idempotency AS claim
                       ON claim.namespace = page.namespace
                       AND claim.operation = '{_LEGACY_OPERATION}'
                       AND claim.key_hash = page.key_hash
                      LEFT JOIN inserted
                        ON inserted.namespace = page.namespace
                       AND inserted.operation = '{_LEGACY_OPERATION}'
                       AND inserted.key_hash = page.key_hash
                     WHERE COALESCE(claim.key_hash, inserted.key_hash) IS NULL
                        OR COALESCE(
                               claim.legacy_unverified_request,
                               inserted.legacy_unverified_request
                           ) IS DISTINCT FROM TRUE
                        OR COALESCE(
                               claim.request_digest, inserted.request_digest
                           ) <> '{_LEGACY_DIGEST}'
                        OR COALESCE(
                               claim.resource_kind, inserted.resource_kind
                           ) <> 'memory'
                        OR COALESCE(
                               claim.resource_ids, inserted.resource_ids
                           )::jsonb <>
                           jsonb_build_array(page.memory_id::text)
                     LIMIT 1
                ), advanced AS (
                    UPDATE public.{_PROGRESS_TABLE} AS progress
                       SET last_key = tail.key,
                           last_namespace = tail.namespace,
                           updated_at = clock_timestamp()
                      FROM (
                          SELECT key, namespace
                            FROM page
                           ORDER BY key DESC, namespace DESC
                           LIMIT 1
                      ) AS tail
                     WHERE progress.singleton = true
                       AND NOT EXISTS (SELECT 1 FROM mismatch)
                    RETURNING progress.singleton
                )
                SELECT (SELECT COUNT(*) FROM page) AS page_count,
                       EXISTS (SELECT 1 FROM mismatch) AS has_mismatch"""
        ),
        {"batch_size": _BATCH_SIZE},
    ).one()
    return int(row.page_count), bool(row.has_mismatch)


def _assert_global_reconciliation() -> None:
    key_hash = _hash_sql("legacy.key", "legacy.namespace")
    mismatch = op.get_bind().execute(
        sa.text(
            f"""SELECT EXISTS (
                    SELECT 1
                      FROM public.idempotency_keys AS legacy
                      LEFT JOIN public.operation_idempotency AS claim
                        ON claim.namespace = legacy.namespace
                       AND claim.operation = '{_LEGACY_OPERATION}'
                       AND claim.key_hash = {key_hash}
                     WHERE claim.key_hash IS NULL
                        OR claim.legacy_unverified_request IS DISTINCT FROM TRUE
                        OR claim.request_digest <> '{_LEGACY_DIGEST}'
                        OR claim.resource_kind <> 'memory'
                        OR claim.resource_ids::jsonb <>
                           jsonb_build_array(legacy.memory_id::text)
                )"""
        )
    ).scalar_one()
    if mismatch:
        raise RuntimeError(
            "0046a found conflicting legacy and current idempotency claims; "
            "preserve both rows and reconcile before retrying"
        )


def _postgresql_backfill_online() -> None:
    _ensure_progress_table()
    with op.get_context().autocommit_block():
        op.execute(
            sa.text("SELECT set_config('app.current_namespace', '__admin__', false)")
        )
        op.execute(
            sa.text("SELECT set_config('agentmem.barrier_group', '', false)")
        )
        while True:
            page_count, mismatch = _copy_page()
            if mismatch:
                raise RuntimeError(
                    "0046a found a conflicting idempotency claim in the current "
                    "page; no cursor advancement was committed"
                )
            if page_count == 0:
                break
        _assert_global_reconciliation()
    op.drop_table(_PROGRESS_TABLE)


def upgrade() -> None:
    dialect = op.get_bind().dialect.name
    if dialect == "postgresql":
        if context.is_offline_mode():
            raise RuntimeError(
                "0046a_idempotency_backfill requires an online "
                "PostgreSQL connection so bounded keyset pages commit and resume "
                "safely. Generate reviewed offline expand DDL through "
                "0046_operation_idempotency, then run 0046a online."
            )
        _postgresql_backfill_online()
        return
    module = _expand_module()
    table = sa.Table("operation_idempotency", sa.MetaData(), autoload_with=op.get_bind())
    module._copy_legacy_claims_bounded(table)


def downgrade() -> None:
    # The compatibility table remains authoritative for old readers. Copied
    # hashes are intentionally retained; revision 0046 already refuses a lossy
    # downgrade while any completion claim exists.
    pass
