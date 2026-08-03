"""Expand the core audit log for a database-owned append boundary.

Revision ID: 0039_audit_append_boundary
Revises: 0038_gate_policy_routing

The restart-safe online data/index/contract phase is
``0039a_audit_append_contract``.
"""

import hashlib
import json
from datetime import UTC

import sqlalchemy as sa
from alembic import context, op

revision = "0039_audit_append_boundary"
down_revision = "0038_gate_policy_routing"
branch_labels = None
depends_on = None
GENESIS_HASH = "0" * 64
_POSITION_ROOT_BATCH = 50
_POSITION_DEPTH_BATCH = 100
_CHAIN_POSITION_INDEX = "uq_event_log_namespace_chain_position"


def _legacy_row_hash(row: dict) -> str:
    created_at = row["created_at"]
    if created_at.tzinfo is not None:
        created_at = created_at.astimezone(UTC).replace(tzinfo=None)
    created_at_utc = created_at.strftime("%Y-%m-%dT%H:%M:%S.%f")
    fields = [
        str(row["prev_hash"]),
        str(row["id"]),
        str(row["namespace"]),
        str(row["agent_id"]),
        str(row["op"]),
        str(row["memory_id"]) if row["memory_id"] is not None else "null",
        str(row["content_hash"]) if row["content_hash"] is not None else "null",
        created_at_utc,
    ]
    canonical = "|".join(fields)
    version = int(row["hash_version"])
    if version >= 2:
        payload = row["payload"] or {}
        if isinstance(payload, str):
            payload = json.loads(payload)
        canonical_payload = json.dumps(
            payload,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
            default=str,
        )
        canonical = f"v{version}|{canonical}|{canonical_payload}"
    return hashlib.sha256(canonical.encode()).hexdigest()


def _assert_existing_hashes_are_verifiable() -> None:
    connection = op.get_bind()
    invalid = connection.execute(
        sa.text(
            """SELECT COUNT(*)
               FROM event_log
               WHERE prev_hash IS NULL
                  OR row_hash IS NULL
                  OR length(prev_hash) <> 64
                  OR length(row_hash) <> 64
                  OR hash_version NOT IN (1, 2)"""
        )
    ).scalar_one()
    if invalid:
        raise RuntimeError(
            "Audit append hardening refused: event_log contains missing, malformed, "
            "or unknown-version hashes. Run a full independent chain verification "
            "and reconcile the affected history before migrating."
        )
    if connection.dialect.name == "postgresql":
        malformed = connection.execute(
            sa.text(
                """SELECT COUNT(*)
                   FROM event_log
                   WHERE prev_hash !~ '^[0-9a-f]{64}$'
                      OR row_hash !~ '^[0-9a-f]{64}$'"""
            )
        ).scalar_one()
        if malformed:
            raise RuntimeError(
                "Audit append hardening refused: hashes must be lowercase hexadecimal"
            )


def _backfill_chain_positions_set_based() -> None:
    """Portable local-profile backfill; production uses committed pages."""
    op.execute(
        f"""WITH RECURSIVE chain(id, namespace, row_hash, chain_position) AS (
            SELECT event.id, event.namespace, event.row_hash, 1
              FROM event_log AS event
             WHERE event.prev_hash = '{GENESIS_HASH}'
            UNION ALL
            SELECT child.id, child.namespace, child.row_hash,
                   parent.chain_position + 1
              FROM chain AS parent
              JOIN event_log AS child
                ON child.namespace = parent.namespace
               AND child.prev_hash = parent.row_hash
        )
        UPDATE event_log
           SET chain_position = (
               SELECT chain.chain_position
                 FROM chain
                WHERE chain.id = event_log.id
           )
         WHERE id IN (SELECT id FROM chain)"""
    )


def _postgresql_position_page() -> sa.TextClause:
    """Return one bounded frontier traversal over otherwise linear chains."""
    return sa.text(
        f"""WITH RECURSIVE roots AS MATERIALIZED (
                SELECT child.id, child.namespace, child.row_hash,
                       CASE
                           WHEN child.prev_hash = '{GENESIS_HASH}' THEN 1::bigint
                           ELSE parent.chain_position + 1
                       END AS chain_position,
                       1 AS depth
                  FROM public.event_log AS child
                  LEFT JOIN public.event_log AS parent
                    ON parent.namespace = child.namespace
                   AND parent.row_hash = child.prev_hash
                 WHERE child.chain_position IS NULL
                   AND (
                       child.prev_hash = '{GENESIS_HASH}'
                       OR parent.chain_position IS NOT NULL
                   )
                 ORDER BY child.namespace, child.id
                 LIMIT {_POSITION_ROOT_BATCH}
            ), chain AS (
                SELECT * FROM roots
                UNION ALL
                SELECT child.id, child.namespace, child.row_hash,
                       parent.chain_position + 1,
                       parent.depth + 1
                  FROM chain AS parent
                  JOIN public.event_log AS child
                    ON child.namespace = parent.namespace
                   AND child.prev_hash = parent.row_hash
                 WHERE child.chain_position IS NULL
                   AND parent.depth < {_POSITION_DEPTH_BATCH}
            ), target AS MATERIALIZED (
                SELECT id, chain_position
                  FROM chain
                 ORDER BY namespace, chain_position
                 LIMIT {_POSITION_ROOT_BATCH * _POSITION_DEPTH_BATCH}
            )
            UPDATE public.event_log AS event
               SET chain_position = target.chain_position
              FROM target
             WHERE event.id = target.id
               AND event.chain_position IS NULL
            RETURNING event.id"""
    )


def _drain_postgresql_positions() -> None:
    """Drain bounded pages; under autocommit every page is independently durable."""
    bind = op.get_bind()
    statement = _postgresql_position_page()
    while bind.execute(statement).first() is not None:
        pass


def _assert_no_unpositioned_events() -> None:
    remaining = op.get_bind().execute(
        sa.text(
            """SELECT EXISTS (
                   SELECT 1 FROM public.event_log
                   WHERE chain_position IS NULL
               )"""
        )
    ).scalar_one()
    if remaining:
        raise RuntimeError(
            "Audit append hardening could not position every row; history is "
            "orphaned, cyclic, concurrently locked, or disconnected"
        )


def _postgresql_backfill_online() -> None:
    with op.get_context().autocommit_block():
        op.execute(
            sa.text("SELECT set_config('app.current_namespace', '__admin__', false)")
        )
        op.execute(
            sa.text("SELECT set_config('agentmem.barrier_group', '', false)")
        )
        _drain_postgresql_positions()
        _assert_no_unpositioned_events()


def _postgresql_chain_position_index_online() -> None:
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
            {"index_name": _CHAIN_POSITION_INDEX},
        ).mappings().one_or_none()
        if state is not None and (
            not bool(state["indisunique"])
            or not bool(state["no_predicate"])
            or str(state["access_method"]) != "btree"
            or list(state["columns"] or []) != ["namespace", "chain_position"]
        ):
            raise RuntimeError(
                f"{_CHAIN_POSITION_INDEX} exists with an unexpected definition"
            )
        if state is not None and not bool(state["indisvalid"]):
            op.execute(
                f"DROP INDEX CONCURRENTLY IF EXISTS public.{_CHAIN_POSITION_INDEX}"
            )
            state = None
        if state is None:
            op.create_index(
                _CHAIN_POSITION_INDEX,
                "event_log",
                ["namespace", "chain_position"],
                unique=True,
                postgresql_concurrently=True,
            )


def _verify_positioned_chain_streaming() -> None:
    """Verify canonical hashes in bounded, restart-safe keyset batches."""
    connection = op.get_bind()
    disconnected = connection.execute(
        sa.text("SELECT COUNT(*) FROM event_log WHERE chain_position IS NULL")
    ).scalar_one()
    if disconnected:
        raise RuntimeError(
            "Audit append hardening refused orphaned, cyclic, or disconnected rows"
        )

    projection = """SELECT id, namespace, agent_id, op, memory_id, content_hash,
                           payload, created_at, prev_hash, row_hash, hash_version,
                           chain_position
                      FROM event_log"""
    first_page = sa.text(
        projection
        + " ORDER BY namespace, chain_position LIMIT :batch_size"
    )
    next_page = sa.text(
        projection
        + """ WHERE namespace > :after_namespace
                  OR (namespace = :after_namespace
                      AND chain_position > :after_position)
               ORDER BY namespace, chain_position
               LIMIT :batch_size"""
    )
    prior_namespace: str | None = None
    prior_hash = GENESIS_HASH
    prior_position = 0
    after_namespace: str | None = None
    after_position = 0
    batch_size = 1000
    while True:
        if after_namespace is None:
            result = connection.execute(first_page, {"batch_size": batch_size})
        else:
            result = connection.execute(
                next_page,
                {
                    "after_namespace": after_namespace,
                    "after_position": after_position,
                    "batch_size": batch_size,
                },
            )
        try:
            rows = result.mappings().all()
        finally:
            result.close()
        if not rows:
            break
        for row in rows:
            namespace = str(row["namespace"])
            if namespace != prior_namespace:
                prior_namespace = namespace
                prior_hash = GENESIS_HASH
                prior_position = 0
            position = int(row["chain_position"])
            if position != prior_position + 1 or str(row["prev_hash"]) != prior_hash:
                raise RuntimeError(
                    "Audit append hardening refused a non-linear namespace chain"
                )
            if str(row["row_hash"]) != _legacy_row_hash(row):
                raise RuntimeError(
                    "Audit append hardening refused a row whose stored hash does not "
                    "match its canonical fields"
                )
            prior_position = position
            prior_hash = str(row["row_hash"])
        after_namespace = str(rows[-1]["namespace"])
        after_position = int(rows[-1]["chain_position"])


def _install_postgres_boundary() -> None:
    if context.is_offline_mode():
        op.execute(
            """DO $$
            BEGIN
                IF NOT EXISTS (
                    SELECT 1 FROM pg_catalog.pg_roles
                     WHERE rolname = 'lians_runtime'
                       AND NOT rolcanlogin
                       AND NOT rolsuper
                       AND NOT rolbypassrls
                ) THEN
                    RAISE EXCEPTION
                        'lians_runtime must be NOLOGIN, NOSUPERUSER, NOBYPASSRLS';
                END IF;
            END;
            $$"""
        )
    else:
        capability_role = op.get_bind().execute(
            sa.text(
                """SELECT rolcanlogin, rolsuper, rolbypassrls
                   FROM pg_roles
                   WHERE rolname = 'lians_runtime'"""
            )
        ).mappings().one_or_none()
        if capability_role is None:
            raise RuntimeError(
                "Audit append hardening requires the pre-provisioned PostgreSQL "
                "capability role lians_runtime"
            )
        if (
            bool(capability_role["rolcanlogin"])
            or bool(capability_role["rolsuper"])
            or bool(capability_role["rolbypassrls"])
        ):
            raise RuntimeError(
                "PostgreSQL role lians_runtime must be NOLOGIN, NOSUPERUSER, "
                "and NOBYPASSRLS"
            )

    op.execute("CREATE EXTENSION IF NOT EXISTS pgcrypto WITH SCHEMA public")
    op.execute(
        """CREATE OR REPLACE FUNCTION public.lians_event_row_hash_v3(
            p_prev_hash text,
            p_chain_position bigint,
            p_id uuid,
            p_namespace text,
            p_agent_id text,
            p_operation text,
            p_memory_id uuid,
            p_content_hash text,
            p_created_at timestamptz,
            p_payload jsonb
        ) RETURNS text
        LANGUAGE sql
        IMMUTABLE
        PARALLEL SAFE
        SET search_path = pg_catalog, public
        AS $$
            SELECT encode(
                public.digest(
                    convert_to(
                        jsonb_build_object(
                            'agent_id', p_agent_id,
                            'chain_position', p_chain_position,
                            'content_hash', p_content_hash,
                            'created_at', to_char(
                                p_created_at AT TIME ZONE 'UTC',
                                'YYYY-MM-DD"T"HH24:MI:SS.US'
                            ),
                            'hash_version', 3,
                            'id', p_id::text,
                            'memory_id', CASE
                                WHEN p_memory_id IS NULL THEN NULL
                                ELSE p_memory_id::text
                            END,
                            'namespace', p_namespace,
                            'operation', p_operation,
                            'payload', COALESCE(p_payload, '{}'::jsonb),
                            'prev_hash', p_prev_hash
                        )::text,
                        'UTF8'
                    ),
                    'sha256'
                ),
                'hex'
            )
        $$"""
    )
    op.execute(
        """CREATE OR REPLACE FUNCTION public.lians_append_event_v3(
            p_id uuid,
            p_namespace text,
            p_agent_id text,
            p_operation text,
            p_memory_id uuid,
            p_content_hash text,
            p_payload jsonb
        ) RETURNS SETOF public.event_log
        LANGUAGE plpgsql
        SECURITY DEFINER
        SET search_path = pg_catalog, public
        AS $$
        DECLARE
            v_append_owner name;
            v_context_namespace text;
        BEGIN
            SELECT pg_get_userbyid(proc.proowner)
              INTO v_append_owner
              FROM pg_proc AS proc
             WHERE proc.oid = (
                 'public.lians_append_event_v3(uuid,text,text,text,uuid,text,jsonb)'
             )::regprocedure;
            IF session_user <> v_append_owner
               AND NOT pg_has_role(session_user, 'lians_runtime', 'MEMBER') THEN
                RAISE EXCEPTION
                    'Audit append requires membership in the lians_runtime capability role';
            END IF;
            v_context_namespace := current_setting('app.current_namespace', true);
            IF session_user <> v_append_owner
               AND v_context_namespace IS DISTINCT FROM p_namespace
               AND v_context_namespace IS DISTINCT FROM '__admin__' THEN
                RAISE EXCEPTION 'Audit append namespace does not match session context';
            END IF;
            IF p_id IS NULL
               OR p_namespace IS NULL OR btrim(p_namespace) = ''
               OR p_agent_id IS NULL OR btrim(p_agent_id) = ''
               OR p_operation IS NULL OR btrim(p_operation) = '' THEN
                RAISE EXCEPTION 'Audit append requires id, namespace, agent, and operation';
            END IF;
            -- The trigger below is the single append primitive for both this
            -- 0.5 wrapper and rolling 0.4.2 direct INSERTs. Placeholder
            -- integrity values are overwritten before constraints are checked.
            RETURN QUERY
                INSERT INTO public.event_log (
                    id, namespace, agent_id, op, memory_id, content_hash,
                    payload, created_at, prev_hash, row_hash, hash_version,
                    chain_position
                ) VALUES (
                    p_id, p_namespace, p_agent_id, p_operation, p_memory_id,
                    p_content_hash, COALESCE(p_payload, '{}'::jsonb),
                    clock_timestamp(), repeat('0', 64), repeat('0', 64), 3, 0
                )
                RETURNING *;
        END;
        $$"""
    )
    op.execute(
        """CREATE OR REPLACE FUNCTION public.lians_event_log_insert_guard()
        RETURNS trigger
        LANGUAGE plpgsql
        SECURITY DEFINER
        SET search_path = pg_catalog, public
        AS $$
        DECLARE
            v_append_owner name;
            v_context_namespace text;
            v_expected_prev text;
            v_expected_position bigint;
        BEGIN
            SELECT pg_get_userbyid(proc.proowner)
              INTO v_append_owner
              FROM pg_proc AS proc
             WHERE proc.oid = (
                 'public.lians_append_event_v3(uuid,text,text,text,uuid,text,jsonb)'
             )::regprocedure;
            IF session_user <> v_append_owner
               AND NOT pg_has_role(session_user, 'lians_runtime', 'MEMBER') THEN
                RAISE EXCEPTION
                    'Audit append requires membership in the lians_runtime capability role';
            END IF;
            v_context_namespace := current_setting('app.current_namespace', true);
            IF session_user <> v_append_owner
               AND v_context_namespace IS DISTINCT FROM NEW.namespace
               AND v_context_namespace IS DISTINCT FROM '__admin__' THEN
                RAISE EXCEPTION 'Audit append namespace does not match session context';
            END IF;
            IF NEW.id IS NULL
               OR NEW.namespace IS NULL OR btrim(NEW.namespace) = ''
               OR NEW.agent_id IS NULL OR btrim(NEW.agent_id) = ''
               OR NEW.op IS NULL OR btrim(NEW.op) = '' THEN
                RAISE EXCEPTION 'Audit append requires id, namespace, agent, and operation';
            END IF;

            -- Compatibility never trusts a legacy writer's predecessor, hash,
            -- version, chain position, or wall clock. The database serializes
            -- and deterministically replaces all of them with canonical v3.
            PERFORM pg_advisory_xact_lock(hashtextextended(NEW.namespace, 0));
            NEW.created_at := clock_timestamp();
            NEW.payload := COALESCE(NEW.payload, '{}'::jsonb);
            INSERT INTO public.audit_chain_heads (
                namespace, event_id, row_hash, chain_position, updated_at
            ) VALUES (
                NEW.namespace, NULL, repeat('0', 64), 0, NEW.created_at
            ) ON CONFLICT (namespace) DO NOTHING;
            SELECT head.row_hash, head.chain_position + 1
              INTO v_expected_prev, v_expected_position
              FROM public.audit_chain_heads AS head
             WHERE head.namespace = NEW.namespace
             FOR UPDATE;
            IF v_expected_prev IS NULL THEN
                RAISE EXCEPTION 'Audit namespace head is missing';
            END IF;
            IF v_expected_prev !~ '^[0-9a-f]{64}$' THEN
                RAISE EXCEPTION 'Audit chain tip is missing or malformed';
            END IF;
            NEW.prev_hash := v_expected_prev;
            NEW.chain_position := v_expected_position;
            NEW.hash_version := 3;
            NEW.row_hash := public.lians_event_row_hash_v3(
                NEW.prev_hash, NEW.chain_position, NEW.id, NEW.namespace,
                NEW.agent_id, NEW.op, NEW.memory_id, NEW.content_hash,
                NEW.created_at, NEW.payload::jsonb
            );
            RETURN NEW;
        END;
        $$"""
    )
    op.execute(
        """CREATE OR REPLACE FUNCTION public.lians_event_log_advance_head()
        RETURNS trigger
        LANGUAGE plpgsql
        SECURITY DEFINER
        SET search_path = pg_catalog, public
        AS $$
        BEGIN
            UPDATE public.audit_chain_heads
               SET event_id = NEW.id,
                   row_hash = NEW.row_hash,
                   chain_position = NEW.chain_position,
                   updated_at = NEW.created_at
             WHERE namespace = NEW.namespace
               AND row_hash = NEW.prev_hash
               AND chain_position = NEW.chain_position - 1;
            IF NOT FOUND THEN
                RAISE EXCEPTION 'Audit chain head changed outside the append boundary';
            END IF;
            RETURN NEW;
        END;
        $$"""
    )
    op.execute(
        """CREATE OR REPLACE FUNCTION public.lians_event_log_reject_mutation()
        RETURNS trigger
        LANGUAGE plpgsql
        SET search_path = pg_catalog, public
        AS $$
        BEGIN
            RAISE EXCEPTION 'event_log is append-only; % is forbidden', TG_OP;
        END;
        $$"""
    )
    op.execute(
        """CREATE TRIGGER trg_event_log_insert_boundary
        BEFORE INSERT ON event_log
        FOR EACH ROW EXECUTE FUNCTION public.lians_event_log_insert_guard()"""
    )
    op.execute(
        """CREATE TRIGGER trg_event_log_advance_head
        AFTER INSERT ON event_log
        FOR EACH ROW EXECUTE FUNCTION public.lians_event_log_advance_head()"""
    )
    op.execute(
        """CREATE TRIGGER trg_event_log_reject_mutation
        BEFORE UPDATE OR DELETE ON event_log
        FOR EACH ROW EXECUTE FUNCTION public.lians_event_log_reject_mutation()"""
    )
    op.execute(
        """CREATE TRIGGER trg_event_log_reject_truncate
        BEFORE TRUNCATE ON event_log
        FOR EACH STATEMENT EXECUTE FUNCTION public.lians_event_log_reject_mutation()"""
    )

    for table in (
        "event_log",
        "subject_keys",
        "agent_barrier_groups",
        "namespace_policies",
    ):
        op.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY")

    # The API login is deliberately not the schema owner. Grant ordinary
    # application DML through the fixed NOLOGIN capability role, and arrange
    # for later migrator-owned relations/sequences to inherit the same runtime
    # access. Immutable relations retain their trigger guards; event_log is
    # narrowed below to SELECT + INSERT. INSERT remains available only for the
    # 0.4.2 rolling window and is fully mediated by the database trigger.
    op.execute("GRANT USAGE ON SCHEMA public TO lians_runtime")
    op.execute(
        "GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public "
        "TO lians_runtime"
    )
    op.execute(
        "GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO lians_runtime"
    )
    op.execute(
        "ALTER DEFAULT PRIVILEGES IN SCHEMA public "
        "GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO lians_runtime"
    )
    op.execute(
        "ALTER DEFAULT PRIVILEGES IN SCHEMA public "
        "GRANT USAGE, SELECT ON SEQUENCES TO lians_runtime"
    )
    op.execute("REVOKE TRUNCATE ON ALL TABLES IN SCHEMA public FROM lians_runtime")
    op.execute("REVOKE ALL ON audit_chain_heads FROM PUBLIC")
    op.execute("REVOKE ALL ON audit_chain_heads FROM lians_runtime")
    op.execute("REVOKE INSERT, UPDATE, DELETE, TRUNCATE ON event_log FROM PUBLIC")
    op.execute(
        "REVOKE UPDATE, DELETE, TRUNCATE ON event_log FROM lians_runtime"
    )
    op.execute("GRANT SELECT, INSERT ON event_log TO lians_runtime")
    for function in (
        "public.lians_event_log_insert_guard()",
        "public.lians_event_log_advance_head()",
        "public.lians_event_log_reject_mutation()",
    ):
        op.execute(f"REVOKE ALL ON FUNCTION {function} FROM PUBLIC")
    op.execute(
        """REVOKE ALL ON FUNCTION public.lians_append_event_v3(
            uuid,text,text,text,uuid,text,jsonb
        ) FROM PUBLIC"""
    )
    op.execute(
        """REVOKE ALL ON FUNCTION public.lians_event_row_hash_v3(
            text,bigint,uuid,text,text,text,uuid,text,timestamptz,jsonb
        ) FROM PUBLIC"""
    )
    op.execute(
        """GRANT EXECUTE ON FUNCTION public.lians_append_event_v3(
            uuid,text,text,text,uuid,text,jsonb
        ) TO lians_runtime"""
    )
    op.execute(
        """GRANT EXECUTE ON FUNCTION public.lians_event_row_hash_v3(
            text,bigint,uuid,text,text,text,uuid,text,timestamptz,jsonb
        ) TO lians_runtime"""
    )


def _install_sqlite_rolling_position_projection() -> None:
    """Let a local 0.4.2 writer omit the new ordering column."""
    op.execute(
        """CREATE TRIGGER trg_event_log_legacy_chain_position
        AFTER INSERT ON event_log
        WHEN NEW.chain_position IS NULL
        BEGIN
            UPDATE event_log
               SET chain_position = (
                   SELECT COALESCE(MAX(prior.chain_position), 0) + 1
                     FROM event_log AS prior
                    WHERE prior.namespace = NEW.namespace
                      AND prior.id <> NEW.id
               )
             WHERE id = NEW.id;
        END"""
    )


def upgrade() -> None:
    """Expand only; the restart-safe online contract is revision 0039a."""
    bind = op.get_bind()
    dialect = bind.dialect.name
    if dialect == "postgresql":
        op.execute("SELECT set_config('app.current_namespace', '__admin__', true)")
        op.execute("SELECT set_config('agentmem.barrier_group', '', true)")
    if context.is_offline_mode():
        if dialect == "postgresql":
            op.execute(
                """DO $$
                BEGIN
                    IF EXISTS (
                        SELECT 1 FROM public.event_log
                         WHERE prev_hash IS NULL OR row_hash IS NULL
                            OR length(prev_hash) <> 64 OR length(row_hash) <> 64
                            OR hash_version NOT IN (1, 2)
                            OR prev_hash !~ '^[0-9a-f]{64}$'
                            OR row_hash !~ '^[0-9a-f]{64}$'
                    ) THEN
                        RAISE EXCEPTION
                            'Audit append hardening refused malformed hashes';
                    END IF;
                END;
                $$"""
            )
    else:
        _assert_existing_hashes_are_verifiable()
    op.add_column(
        "event_log", sa.Column("chain_position", sa.BigInteger(), nullable=True)
    )


def _contract_upgrade() -> None:
    """Backfill/index online, then atomically install the append contract."""
    bind = op.get_bind()
    dialect = bind.dialect.name
    if dialect == "postgresql":
        _postgresql_backfill_online()
        _postgresql_chain_position_index_online()
        # Fence only the final tail that arrived during the concurrent build.
        # The table lock prevents an old writer from adding another NULL after
        # the last bounded page and before the NOT NULL/trigger contract lands.
        op.execute("LOCK TABLE public.event_log IN SHARE ROW EXCLUSIVE MODE")
        _drain_postgresql_positions()
        _assert_no_unpositioned_events()
    else:
        _backfill_chain_positions_set_based()
    _verify_positioned_chain_streaming()
    op.create_table(
        "audit_chain_heads",
        sa.Column("namespace", sa.String(), primary_key=True),
        sa.Column(
            "event_id",
            sa.Uuid(),
            sa.ForeignKey("event_log.id", ondelete="RESTRICT"),
            nullable=True,
        ),
        sa.Column("row_hash", sa.String(64), nullable=False),
        sa.Column("chain_position", sa.BigInteger(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "length(row_hash) = 64", name="ck_audit_chain_head_hash_length"
        ),
        sa.CheckConstraint(
            "chain_position >= 0", name="ck_audit_chain_head_position"
        ),
    )
    op.execute(
        """INSERT INTO audit_chain_heads (
               namespace, event_id, row_hash, chain_position, updated_at
           )
           SELECT event.namespace, event.id, event.row_hash,
                  event.chain_position, event.created_at
           FROM event_log AS event
           LEFT JOIN event_log AS child
             ON child.namespace = event.namespace
            AND child.prev_hash = event.row_hash
           WHERE child.id IS NULL"""
    )
    op.alter_column(
        "event_log",
        "hash_version",
        existing_type=sa.Integer(),
        existing_nullable=False,
        server_default="3",
    )
    if dialect == "postgresql":
        check_constraints = (
            (
                "ck_event_log_hash_lengths",
                (
                    "prev_hash IS NOT NULL AND row_hash IS NOT NULL "
                    "AND length(prev_hash) = 64 AND length(row_hash) = 64"
                ),
            ),
            ("ck_event_log_hash_version", "hash_version IN (1, 2, 3)"),
            (
                "ck_event_log_chain_position_present",
                "chain_position IS NOT NULL",
            ),
        )
        for name, expression in check_constraints:
            op.execute(
                f"ALTER TABLE public.event_log ADD CONSTRAINT {name} "
                f"CHECK ({expression}) NOT VALID"
            )
        op.execute(
            "ALTER TABLE public.event_log "
            + ", ".join(
                f"VALIDATE CONSTRAINT {name}"
                for name, _expression in check_constraints
            )
        )
        op.alter_column(
            "event_log", "prev_hash", existing_type=sa.String(64), nullable=False
        )
        op.alter_column(
            "event_log", "row_hash", existing_type=sa.String(64), nullable=False
        )
        op.alter_column(
            "event_log",
            "chain_position",
            existing_type=sa.BigInteger(),
            nullable=False,
        )
        op.drop_constraint(
            "ck_event_log_chain_position_present",
            "event_log",
            type_="check",
        )
        _install_postgres_boundary()
        op.execute(
            "ALTER TABLE public.event_log ADD CONSTRAINT "
            "uq_event_log_namespace_chain_position UNIQUE USING INDEX "
            "uq_event_log_namespace_chain_position"
        )
    else:
        with op.batch_alter_table("event_log") as batch:
            batch.create_check_constraint(
                "ck_event_log_hash_lengths",
                "prev_hash IS NOT NULL AND row_hash IS NOT NULL "
                "AND length(prev_hash) = 64 AND length(row_hash) = 64",
            )
            batch.create_check_constraint(
                "ck_event_log_hash_version",
                "hash_version IN (1, 2, 3)",
            )
        op.create_index(
            "uq_event_log_namespace_chain_position",
            "event_log",
            ["namespace", "chain_position"],
            unique=True,
        )
        if dialect == "sqlite":
            _install_sqlite_rolling_position_projection()


def _contract_downgrade() -> None:
    if op.get_bind().dialect.name == "postgresql":
        for trigger in (
            "trg_event_log_reject_truncate",
            "trg_event_log_reject_mutation",
            "trg_event_log_advance_head",
            "trg_event_log_insert_boundary",
        ):
            op.execute(f"DROP TRIGGER IF EXISTS {trigger} ON event_log")
        op.execute("DROP FUNCTION IF EXISTS public.lians_event_log_reject_mutation()")
        op.execute("DROP FUNCTION IF EXISTS public.lians_event_log_advance_head()")
        op.execute("DROP FUNCTION IF EXISTS public.lians_event_log_insert_guard()")
        op.execute(
            """DROP FUNCTION IF EXISTS public.lians_append_event_v3(
                uuid,text,text,text,uuid,text,jsonb
            )"""
        )
        op.execute(
            """DROP FUNCTION IF EXISTS public.lians_event_row_hash_v3(
                text,bigint,uuid,text,text,text,uuid,text,timestamptz,jsonb
            )"""
        )
        op.execute(
            "ALTER DEFAULT PRIVILEGES IN SCHEMA public "
            "REVOKE SELECT, INSERT, UPDATE, DELETE ON TABLES FROM lians_runtime"
        )
        op.execute(
            "ALTER DEFAULT PRIVILEGES IN SCHEMA public "
            "REVOKE USAGE, SELECT ON SEQUENCES FROM lians_runtime"
        )
        for table in (
            "event_log",
            "subject_keys",
            "agent_barrier_groups",
            "namespace_policies",
        ):
            op.execute(f"ALTER TABLE {table} NO FORCE ROW LEVEL SECURITY")

    elif op.get_bind().dialect.name == "sqlite":
        op.execute("DROP TRIGGER IF EXISTS trg_event_log_legacy_chain_position")

    op.drop_table("audit_chain_heads")
    op.drop_constraint("ck_event_log_hash_version", "event_log", type_="check")
    op.drop_constraint("ck_event_log_hash_lengths", "event_log", type_="check")
    op.alter_column(
        "event_log",
        "hash_version",
        existing_type=sa.Integer(),
        existing_nullable=False,
        server_default="1",
    )
    op.alter_column(
        "event_log", "row_hash", existing_type=sa.String(64), nullable=True
    )
    op.alter_column(
        "event_log", "prev_hash", existing_type=sa.String(64), nullable=True
    )
    if op.get_bind().dialect.name == "postgresql":
        op.drop_constraint(
            "uq_event_log_namespace_chain_position", "event_log", type_="unique"
        )
    else:
        op.drop_index(
            "uq_event_log_namespace_chain_position", table_name="event_log"
        )


def downgrade() -> None:
    op.drop_column("event_log", "chain_position")
