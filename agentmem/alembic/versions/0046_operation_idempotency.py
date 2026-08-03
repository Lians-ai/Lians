"""Add immutable transactional operation idempotency.

Revision ID: 0046_operation_idempotency
Revises: 0045_evidence_scope_identity
"""

from __future__ import annotations

import hashlib

import sqlalchemy as sa
from alembic import op

revision = "0046_operation_idempotency"
down_revision = "0045_evidence_scope_identity"
branch_labels = None
depends_on = None

_LEGACY_DIGEST = "0" * 64
_LEGACY_OPERATION = "memory.create"
_POSTGRES_CHECK_CONSTRAINTS = (
    (
        "ck_operation_idempotency_resource_ids",
        (
            "jsonb_typeof(resource_ids::jsonb) = 'array' "
            "AND jsonb_array_length(resource_ids::jsonb) BETWEEN 1 AND 100"
        ),
    ),
    (
        "ck_operation_idempotency_key_hash_hex",
        "key_hash ~ '^[0-9a-f]{64}$'",
    ),
    (
        "ck_operation_idempotency_request_digest_hex",
        "request_digest ~ '^[0-9a-f]{64}$'",
    ),
)


def _key_hash(namespace: str, operation: str, key: str) -> str:
    material = (
        b"lians/operation-idempotency-key/v1\0"
        + namespace.encode("utf-8")
        + b"\0"
        + operation.encode("utf-8")
        + b"\0"
        + key.encode("utf-8")
    )
    return hashlib.sha256(material).hexdigest()


def _copy_legacy_claims_bounded(table: sa.Table) -> None:
    """Portable local-profile copy; PostgreSQL is owned by online 0046a."""
    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        raise RuntimeError(
            "PostgreSQL legacy claims must be copied by the resumable "
            "0046a_idempotency_backfill revision"
        )

    # Local/portable profiles may not expose SHA-256 in SQL. Keyset pages keep
    # both driver and Python memory bounded while preserving deterministic order.
    last_key: str | None = None
    last_namespace: str | None = None
    while True:
        if last_key is None:
            rows = bind.execute(
                sa.text(
                    """SELECT key, namespace, memory_id, created_at
                         FROM idempotency_keys
                        ORDER BY key, namespace
                        LIMIT 1000"""
                )
            ).mappings().all()
        else:
            rows = bind.execute(
                sa.text(
                    """SELECT key, namespace, memory_id, created_at
                         FROM idempotency_keys
                        WHERE key > :last_key
                           OR (key = :last_key AND namespace > :last_namespace)
                        ORDER BY key, namespace
                        LIMIT 1000"""
                ),
                {"last_key": last_key, "last_namespace": last_namespace},
            ).mappings().all()
        if not rows:
            return
        batch = [
            {
                "namespace": row["namespace"],
                "operation": _LEGACY_OPERATION,
                "key_hash": _key_hash(
                    row["namespace"], _LEGACY_OPERATION, row["key"]
                ),
                "request_digest": _LEGACY_DIGEST,
                "legacy_unverified_request": True,
                "resource_kind": "memory",
                "resource_ids": [str(row["memory_id"])],
                "response_status": 200,
                "created_at": row["created_at"],
            }
            for row in rows
        ]
        op.bulk_insert(table, batch)
        last_key = str(rows[-1]["key"])
        last_namespace = str(rows[-1]["namespace"])


def upgrade() -> None:
    op.create_table(
        "operation_idempotency",
        sa.Column("namespace", sa.String(), primary_key=True),
        sa.Column("operation", sa.String(length=100), primary_key=True),
        sa.Column("key_hash", sa.String(length=64), primary_key=True),
        sa.Column("request_digest", sa.String(length=64), nullable=False),
        sa.Column(
            "legacy_unverified_request",
            sa.Boolean(),
            nullable=False,
            server_default=sa.false(),
        ),
        sa.Column("resource_kind", sa.String(length=64), nullable=False),
        sa.Column("resource_ids", sa.JSON(), nullable=False),
        sa.Column("response_status", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "length(operation) BETWEEN 1 AND 100",
            name="ck_operation_idempotency_operation_length",
        ),
        sa.CheckConstraint(
            "length(key_hash) = 64 AND key_hash = lower(key_hash)",
            name="ck_operation_idempotency_key_hash",
        ),
        sa.CheckConstraint(
            "length(request_digest) = 64 AND request_digest = lower(request_digest)",
            name="ck_operation_idempotency_request_digest",
        ),
        sa.CheckConstraint(
            f"(legacy_unverified_request AND request_digest = '{_LEGACY_DIGEST}') "
            f"OR (NOT legacy_unverified_request AND request_digest <> '{_LEGACY_DIGEST}')",
            name="ck_operation_idempotency_legacy_digest",
        ),
        sa.CheckConstraint(
            "length(resource_kind) BETWEEN 1 AND 64",
            name="ck_operation_idempotency_resource_kind",
        ),
        sa.CheckConstraint(
            "response_status BETWEEN 100 AND 599",
            name="ck_operation_idempotency_response_status",
        ),
    )
    op.create_index(
        "ix_operation_idempotency_created_at",
        "operation_idempotency",
        ["created_at"],
    )

    # Preserve the old exactly-once surface. Raw keys remain temporarily in
    # their original table so rolling 0.4.2 readers can replay new admitted
    # memory writes; 0046a copies history in bounded committed pages.
    bind = op.get_bind()
    dialect = bind.dialect.name
    if dialect == "postgresql":
        op.execute("SELECT set_config('app.current_namespace', '__admin__', true)")
        op.execute("SELECT set_config('agentmem.barrier_group', '', true)")
        # The table is still empty. Establish its final checks before 0046a
        # begins independently committed historical pages.
        for name, expression in _POSTGRES_CHECK_CONSTRAINTS:
            op.execute(
                "ALTER TABLE public.operation_idempotency "
                f"ADD CONSTRAINT {name} CHECK ({expression}) NOT VALID"
            )
    if dialect == "postgresql":
        op.execute(
            "ALTER TABLE public.operation_idempotency "
            + ", ".join(
                f"VALIDATE CONSTRAINT {name}"
                for name, _expression in _POSTGRES_CHECK_CONSTRAINTS
            )
        )
        _install_postgres_guards()
    elif dialect == "sqlite":
        _install_sqlite_guards()


def _install_postgres_guards() -> None:
    op.execute("ALTER TABLE public.operation_idempotency ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE public.operation_idempotency FORCE ROW LEVEL SECURITY")
    op.execute(
        """CREATE POLICY rls_operation_idempotency_namespace
        ON public.operation_idempotency
        USING (
            namespace = current_setting('app.current_namespace', true)
            OR current_setting('app.current_namespace', true) = '__admin__'
        )
        WITH CHECK (
            namespace = current_setting('app.current_namespace', true)
            OR current_setting('app.current_namespace', true) = '__admin__'
        )"""
    )
    # Keep the predecessor table as an expand-phase compatibility surface. It
    # gains the same tenant isolation and an immutable transactional mirror into
    # the hashed ledger; it is removed only by a future contract release.
    op.execute("ALTER TABLE public.idempotency_keys ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE public.idempotency_keys FORCE ROW LEVEL SECURITY")
    op.execute(
        """CREATE POLICY rls_idempotency_keys_namespace
        ON public.idempotency_keys
        USING (
            namespace = current_setting('app.current_namespace', true)
            OR current_setting('app.current_namespace', true) = '__admin__'
        )
        WITH CHECK (
            namespace = current_setting('app.current_namespace', true)
            OR current_setting('app.current_namespace', true) = '__admin__'
        )"""
    )
    op.execute(
        f"""CREATE FUNCTION public.lians_mirror_legacy_idempotency()
        RETURNS trigger
        LANGUAGE plpgsql
        SECURITY DEFINER
        SET search_path = pg_catalog, public
        AS $$
        DECLARE
            v_context_namespace text;
            v_key_hash text;
        BEGIN
            IF NOT pg_has_role(session_user, 'lians_runtime', 'MEMBER')
               AND session_user <> current_user THEN
                RAISE EXCEPTION
                    'Legacy idempotency writes require the runtime capability';
            END IF;
            v_context_namespace := current_setting('app.current_namespace', true);
            IF v_context_namespace IS DISTINCT FROM NEW.namespace
               AND v_context_namespace IS DISTINCT FROM '__admin__' THEN
                RAISE EXCEPTION
                    'Legacy idempotency namespace does not match session context';
            END IF;
            IF NEW.key IS NULL
               OR octet_length(NEW.key) NOT BETWEEN 1 AND 255
               OR NEW.key !~ '^[!-~]+$'
               OR NEW.namespace IS NULL OR btrim(NEW.namespace) = ''
               OR NEW.memory_id IS NULL THEN
                RAISE EXCEPTION
                    'Legacy idempotency claim is malformed or unbounded';
            END IF;
            v_key_hash := encode(
                public.digest(
                    convert_to(
                        'lians/operation-idempotency-key/v1', 'UTF8'
                    ) || decode('00', 'hex') ||
                    convert_to(NEW.namespace, 'UTF8') || decode('00', 'hex') ||
                    convert_to('{_LEGACY_OPERATION}', 'UTF8') ||
                    decode('00', 'hex') || convert_to(NEW.key, 'UTF8'),
                    'sha256'
                ),
                'hex'
            );
            -- Match the 0.5 application's signed first-64-bit advisory lock.
            -- This BEFORE trigger acquires it before the legacy PK row exists,
            -- avoiding a lock-order cycle with the 0.5 dual-write.
            PERFORM pg_advisory_xact_lock(
                (('x' || substr(v_key_hash, 1, 16))::bit(64)::bigint)
            );
            INSERT INTO public.operation_idempotency (
                namespace, operation, key_hash, request_digest,
                legacy_unverified_request, resource_kind, resource_ids,
                response_status, created_at
            ) VALUES (
                NEW.namespace, '{_LEGACY_OPERATION}', v_key_hash,
                '{_LEGACY_DIGEST}', TRUE, 'memory',
                jsonb_build_array(NEW.memory_id::text)::json, 200, NEW.created_at
            ) ON CONFLICT (namespace, operation, key_hash) DO NOTHING;

            IF NOT EXISTS (
                SELECT 1
                  FROM public.operation_idempotency AS claim
                 WHERE claim.namespace = NEW.namespace
                   AND claim.operation = '{_LEGACY_OPERATION}'
                   AND claim.key_hash = v_key_hash
                   AND claim.resource_kind = 'memory'
                   AND claim.resource_ids::jsonb =
                       jsonb_build_array(NEW.memory_id::text)
            ) THEN
                RAISE EXCEPTION
                    'Legacy and current idempotency claims disagree';
            END IF;
            RETURN NEW;
        END;
        $$"""
    )
    op.execute(
        "REVOKE ALL ON FUNCTION public.lians_mirror_legacy_idempotency() FROM PUBLIC"
    )
    op.execute(
        """CREATE TRIGGER trg_idempotency_keys_mirror
        BEFORE INSERT ON public.idempotency_keys
        FOR EACH ROW EXECUTE FUNCTION public.lians_mirror_legacy_idempotency()"""
    )
    op.execute(
        """CREATE FUNCTION public.lians_operation_idempotency_reject_mutation()
        RETURNS trigger
        LANGUAGE plpgsql
        SET search_path = pg_catalog, public
        AS $$
        BEGIN
            RAISE EXCEPTION 'operation_idempotency is an immutable completion ledger';
        END;
        $$"""
    )
    op.execute(
        "REVOKE ALL ON FUNCTION public.lians_operation_idempotency_reject_mutation() "
        "FROM PUBLIC"
    )
    op.execute(
        """CREATE TRIGGER trg_operation_idempotency_append_only
        BEFORE UPDATE OR DELETE ON public.operation_idempotency
        FOR EACH ROW EXECUTE FUNCTION public.lians_operation_idempotency_reject_mutation()"""
    )
    op.execute(
        """CREATE TRIGGER trg_operation_idempotency_no_truncate
        BEFORE TRUNCATE ON public.operation_idempotency
        FOR EACH STATEMENT EXECUTE FUNCTION public.lians_operation_idempotency_reject_mutation()"""
    )
    op.execute(
        "GRANT SELECT, INSERT ON public.operation_idempotency TO lians_runtime"
    )
    op.execute(
        "REVOKE UPDATE, DELETE, TRUNCATE ON public.operation_idempotency "
        "FROM PUBLIC, lians_runtime"
    )
    op.execute("GRANT SELECT, INSERT ON public.idempotency_keys TO lians_runtime")
    op.execute(
        "REVOKE UPDATE, DELETE, TRUNCATE ON public.idempotency_keys "
        "FROM PUBLIC, lians_runtime"
    )


def _install_sqlite_guards() -> None:
    op.execute(
        """CREATE TRIGGER trg_operation_idempotency_hashes_hex
        BEFORE INSERT ON operation_idempotency
        WHEN NEW.key_hash GLOB '*[^0-9a-f]*'
          OR NEW.request_digest GLOB '*[^0-9a-f]*'
        BEGIN
            SELECT RAISE(
                ABORT,
                'operation_idempotency hashes must be lowercase hexadecimal'
            );
        END"""
    )
    for operation in ("UPDATE", "DELETE"):
        op.execute(
            f"""CREATE TRIGGER trg_operation_idempotency_no_{operation.lower()}
            BEFORE {operation} ON operation_idempotency
            BEGIN
                SELECT RAISE(
                    ABORT,
                    'operation_idempotency is an immutable completion ledger'
                );
            END"""
        )
        op.execute(
            f"""CREATE TRIGGER trg_idempotency_keys_no_{operation.lower()}
            BEFORE {operation} ON idempotency_keys
            BEGIN
                SELECT RAISE(
                    ABORT,
                    'legacy idempotency compatibility claims are immutable'
                );
            END"""
        )


def downgrade() -> None:
    bind = op.get_bind()
    dialect = bind.dialect.name
    if dialect == "postgresql":
        bind.execute(
            sa.text("SELECT set_config('app.current_namespace', '__admin__', true)")
        )
    completed = bind.execute(
        sa.text("SELECT COUNT(*) FROM operation_idempotency")
    ).scalar_one()
    if completed:
        raise RuntimeError(
            "0046 downgrade refused: hashed completion claims cannot be converted "
            "back into raw Idempotency-Key values"
        )
    if dialect == "postgresql":
        op.execute(
            "DROP TRIGGER IF EXISTS trg_idempotency_keys_mirror "
            "ON public.idempotency_keys"
        )
        op.execute(
            "DROP FUNCTION IF EXISTS public.lians_mirror_legacy_idempotency()"
        )
        op.execute(
            "DROP POLICY IF EXISTS rls_idempotency_keys_namespace "
            "ON public.idempotency_keys"
        )
        op.execute("ALTER TABLE public.idempotency_keys NO FORCE ROW LEVEL SECURITY")
        op.execute("ALTER TABLE public.idempotency_keys DISABLE ROW LEVEL SECURITY")
        op.execute(
            "GRANT SELECT, INSERT, UPDATE, DELETE ON public.idempotency_keys "
            "TO lians_runtime"
        )
        op.execute(
            "DROP FUNCTION IF EXISTS public.lians_operation_idempotency_reject_mutation() CASCADE"
        )
    elif dialect == "sqlite":
        for operation in ("update", "delete"):
            op.execute(
                f"DROP TRIGGER IF EXISTS trg_idempotency_keys_no_{operation}"
            )
    op.drop_table("operation_idempotency")
