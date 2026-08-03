"""Bind DecisionRecord v3 hashes to the recorder's authorization snapshot.

Revision ID: 0057_decision_auth_snapshot
Revises: 0056b_auth_lookup_contract

The three new columns use legacy-compatible defaults: v1/v2 writers can keep
their old INSERT shape, but those versions are constrained to an empty snapshot.
Only v3 can claim principal type, role, and effective scopes.  PostgreSQL uses
an immutable validator in the CHECK constraint; SQLite enforces the same
content boundary with an INSERT trigger because SQLite forbids subqueries in
CHECK constraints.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import context, op

revision = "0057_decision_auth_snapshot"
down_revision = "0056b_auth_lookup_contract"
branch_labels = None
depends_on = None

_LEGACY_PRINCIPAL = "lians:principal:v1:legacy-unverified"
_LEGACY_AUTH_METHOD = "legacy_unverified"
_SCOPE_LIMIT = 50
_SCOPE_VALIDATOR = "public.lians_decision_authorization_scopes_valid(jsonb)"

_OLD_HASH_VERSION_CHECK = "record_hash_version IN (1, 2)"
_NEW_HASH_VERSION_CHECK = "record_hash_version IN (1, 2, 3)"

_OLD_PROVENANCE_CHECK = f"""(
    record_hash_version = 1
    AND record_integrity_status = 'legacy_unverified'
    AND recorded_by_principal_ref = '{_LEGACY_PRINCIPAL}'
    AND recorded_by_auth_method = '{_LEGACY_AUTH_METHOD}'
    AND recorded_by_credential_ref IS NULL
) OR (
    record_hash_version = 2
    AND record_integrity_status = 'verified'
    AND recorded_by_principal_ref LIKE 'lians:principal:v1:%'
    AND recorded_by_principal_ref <> '{_LEGACY_PRINCIPAL}'
    AND length(recorded_by_principal_ref) > 20
    AND recorded_by_auth_method <> '{_LEGACY_AUTH_METHOD}'
    AND length(recorded_by_auth_method) > 0
    AND recorded_by_credential_ref LIKE 'lians:credential:v1:sha256:%'
    AND length(recorded_by_credential_ref) = 91
)"""

_AUTHENTICATED_PROVENANCE_CHECK = f"""
    record_integrity_status = 'verified'
    AND recorded_by_principal_ref LIKE 'lians:principal:v1:%'
    AND recorded_by_principal_ref <> '{_LEGACY_PRINCIPAL}'
    AND length(recorded_by_principal_ref) > 20
    AND recorded_by_auth_method <> '{_LEGACY_AUTH_METHOD}'
    AND length(recorded_by_auth_method) > 0
    AND recorded_by_credential_ref LIKE 'lians:credential:v1:sha256:%'
    AND length(recorded_by_credential_ref) = 91
"""

_POSTGRES_PROVENANCE_CHECK = f"""(
    record_hash_version = 1
    AND record_integrity_status = 'legacy_unverified'
    AND recorded_by_principal_ref = '{_LEGACY_PRINCIPAL}'
    AND recorded_by_auth_method = '{_LEGACY_AUTH_METHOD}'
    AND recorded_by_credential_ref IS NULL
    AND recorded_by_principal_type IS NULL
    AND recorded_by_role IS NULL
    AND recorded_by_scopes::jsonb = '[]'::jsonb
) OR (
    record_hash_version = 2
    AND {_AUTHENTICATED_PROVENANCE_CHECK}
    AND recorded_by_principal_type IS NULL
    AND recorded_by_role IS NULL
    AND recorded_by_scopes::jsonb = '[]'::jsonb
) OR (
    record_hash_version = 3
    AND {_AUTHENTICATED_PROVENANCE_CHECK}
    AND recorded_by_credential_ref IS NOT NULL
    AND recorded_by_principal_type IS NOT NULL
    AND length(recorded_by_principal_type) BETWEEN 1 AND 32
    AND recorded_by_principal_type ~ '^[A-Za-z0-9_.:-]+$'
    AND (
        recorded_by_role IS NULL
        OR recorded_by_role IN ('owner', 'analyst', 'compliance', 'readonly')
    )
    AND public.lians_decision_authorization_scopes_valid(
        recorded_by_scopes::jsonb
    )
)"""

_SQLITE_SCOPE_SHAPE = f"""(
    CASE
        WHEN json_valid(recorded_by_scopes) <> 1 THEN 0
        WHEN json_type(recorded_by_scopes) <> 'array' THEN 0
        WHEN record_hash_version IN (1, 2)
            THEN json_array_length(recorded_by_scopes) = 0
        WHEN record_hash_version = 3
            THEN json_array_length(recorded_by_scopes) BETWEEN 1 AND {_SCOPE_LIMIT}
        ELSE 0
    END
)"""

_SQLITE_PROVENANCE_CHECK = f"""(
    record_hash_version = 1
    AND record_integrity_status = 'legacy_unverified'
    AND recorded_by_principal_ref = '{_LEGACY_PRINCIPAL}'
    AND recorded_by_auth_method = '{_LEGACY_AUTH_METHOD}'
    AND recorded_by_credential_ref IS NULL
    AND recorded_by_principal_type IS NULL
    AND recorded_by_role IS NULL
    AND {_SQLITE_SCOPE_SHAPE}
) OR (
    record_hash_version = 2
    AND {_AUTHENTICATED_PROVENANCE_CHECK}
    AND recorded_by_principal_type IS NULL
    AND recorded_by_role IS NULL
    AND {_SQLITE_SCOPE_SHAPE}
) OR (
    record_hash_version = 3
    AND {_AUTHENTICATED_PROVENANCE_CHECK}
    AND recorded_by_credential_ref IS NOT NULL
    AND recorded_by_principal_type IS NOT NULL
    AND length(recorded_by_principal_type) BETWEEN 1 AND 32
    AND recorded_by_principal_type NOT GLOB '*[^-A-Za-z0-9_.:]*'
    AND (
        recorded_by_role IS NULL
        OR recorded_by_role IN ('owner', 'analyst', 'compliance', 'readonly')
    )
    AND {_SQLITE_SCOPE_SHAPE}
)"""

_HASH_COVERED_COLUMNS_V2 = (
    "id",
    "namespace",
    "agent_id",
    "recorded_by_principal_ref",
    "recorded_by_auth_method",
    "recorded_by_credential_ref",
    "barrier_group",
    "decision_type",
    "outcome",
    "reason_codes",
    "regime",
    "subject_id",
    "session_id",
    "model_id",
    "model_version",
    "policy_version",
    "decided_at",
    "recorded_at",
    "knowledge_as_of",
    "knowledge_recorded_as_of",
    "evidence_memory_ids",
    "input_hash",
    "output_hash",
    "supersedes_id",
    "metadata",
    "record_hash_version",
    "record_integrity_status",
    "record_hash",
)
_HASH_COVERED_COLUMNS_V3 = (
    *_HASH_COVERED_COLUMNS_V2[:6],
    "recorded_by_principal_type",
    "recorded_by_role",
    "recorded_by_scopes",
    *_HASH_COVERED_COLUMNS_V2[6:],
)

_REPLACED_SQLITE_DECISION_TRIGGERS = frozenset(
    {
        "trg_decision_record_immutable",
        "trg_decision_authorization_scope_insert",
    }
)


def _set_postgresql_migration_scope() -> None:
    # decision_records has FORCE RLS. Constraint validation and downgrade
    # preflight must see the full relation through the explicit migrator scope.
    op.execute("SELECT set_config('app.current_namespace', '__admin__', true)")
    op.execute("SELECT set_config('agentmem.barrier_group', '', true)")


def _install_postgresql_scope_validator() -> None:
    op.execute(
        f"""CREATE OR REPLACE FUNCTION
        public.lians_decision_authorization_scopes_valid(scopes jsonb)
        RETURNS boolean
        LANGUAGE plpgsql
        IMMUTABLE
        STRICT
        PARALLEL SAFE
        SET search_path = pg_catalog
        AS $$
        DECLARE
            scope_item jsonb;
            scope_value text;
            seen_scopes text[] := ARRAY[]::text[];
            write_present boolean := false;
        BEGIN
            IF jsonb_typeof(scopes) <> 'array'
               OR jsonb_array_length(scopes) < 1
               OR jsonb_array_length(scopes) > {_SCOPE_LIMIT} THEN
                RETURN false;
            END IF;

            FOR scope_item IN
                SELECT value FROM jsonb_array_elements(scopes)
            LOOP
                IF jsonb_typeof(scope_item) <> 'string' THEN
                    RETURN false;
                END IF;
                scope_value := scope_item #>> '{{}}';
                IF scope_value !~ '^[A-Za-z0-9_.:-]{{1,100}}$'
                   OR scope_value = ANY (seen_scopes) THEN
                    RETURN false;
                END IF;
                seen_scopes := array_append(seen_scopes, scope_value);
                write_present := write_present OR scope_value = 'write';
            END LOOP;
            RETURN write_present;
        END;
        $$"""
    )
    op.execute(f"REVOKE ALL ON FUNCTION {_SCOPE_VALIDATOR} FROM PUBLIC")
    op.execute(f"GRANT EXECUTE ON FUNCTION {_SCOPE_VALIDATOR} TO lians_runtime")


def _install_postgresql_immutable_guard(*, include_auth_snapshot: bool) -> None:
    explicit_auth_guard = ""
    if include_auth_snapshot:
        explicit_auth_guard = """
            IF NEW.recorded_by_principal_type IS DISTINCT FROM
                   OLD.recorded_by_principal_type
               OR NEW.recorded_by_role IS DISTINCT FROM OLD.recorded_by_role
               OR NEW.recorded_by_scopes::jsonb IS DISTINCT FROM
                   OLD.recorded_by_scopes::jsonb THEN
                RAISE EXCEPTION
                    'DecisionRecord authorization snapshot is immutable';
            END IF;
        """
    op.execute(
        f"""CREATE OR REPLACE FUNCTION public.lians_decision_record_immutable_guard()
        RETURNS trigger
        LANGUAGE plpgsql
        SET search_path = pg_catalog, public
        AS $$
        BEGIN
            IF TG_OP IN ('DELETE', 'TRUNCATE') THEN
                RAISE EXCEPTION 'decision records are immutable; record a superseding correction';
            END IF;
            {explicit_auth_guard}
            IF (
                to_jsonb(NEW) - ARRAY[
                    'human_review_status', 'human_reviewer', 'human_reviewed_at',
                    'validmind_inventory_counted'
                ]
            ) IS DISTINCT FROM (
                to_jsonb(OLD) - ARRAY[
                    'human_review_status', 'human_reviewer', 'human_reviewed_at',
                    'validmind_inventory_counted'
                ]
            ) THEN
                RAISE EXCEPTION 'hash-covered decision record fields are immutable';
            END IF;
            IF NEW.human_review_status IS NOT DISTINCT FROM OLD.human_review_status
               AND NEW.human_reviewer IS NOT DISTINCT FROM OLD.human_reviewer
               AND NEW.human_reviewed_at IS NOT DISTINCT FROM OLD.human_reviewed_at THEN
                RAISE EXCEPTION 'decision record updates require a review projection change';
            END IF;
            RETURN NEW;
        END;
        $$"""
    )


def _install_postgresql_gate_guard(*, allow_v3: bool) -> None:
    versions = "IN (2, 3)" if allow_v3 else "= 2"
    v3_snapshot = ""
    if allow_v3:
        v3_snapshot = """
                  AND (
                      decision.record_hash_version = 2
                      OR (
                          length(decision.recorded_by_principal_type)
                              BETWEEN 1 AND 32
                          AND decision.recorded_by_principal_type ~
                              '^[A-Za-z0-9_.:-]+$'
                          AND (
                              decision.recorded_by_role IS NULL
                              OR decision.recorded_by_role IN (
                                  'owner', 'analyst', 'compliance', 'readonly'
                              )
                          )
                          AND public.lians_decision_authorization_scopes_valid(
                              decision.recorded_by_scopes::jsonb
                          )
                      )
                  )
        """
    op.execute(
        f"""CREATE OR REPLACE FUNCTION
        public.lians_gate_require_verified_decision_record()
        RETURNS trigger
        LANGUAGE plpgsql
        SECURITY DEFINER
        SET search_path = pg_catalog, public
        AS $$
        BEGIN
            IF NOT EXISTS (
                SELECT 1
                FROM public.decision_records AS decision
                JOIN public.event_log AS binding
                  ON binding.namespace = decision.namespace
                 AND binding.op = 'decision_recorded'
                 AND binding.agent_id = decision.recorded_by_principal_ref
                 AND binding.content_hash = decision.record_hash
                 AND binding.payload = jsonb_build_object(
                        'schema', 'lians.decision-record-binding.v1',
                        'decision_id', decision.id::text,
                        'record_hash', decision.record_hash
                     )
                WHERE decision.id = NEW.decision_id
                  AND decision.namespace = NEW.namespace
                  AND decision.barrier_group IS NOT DISTINCT FROM NEW.barrier_group
                  AND decision.record_hash_version {versions}
                  AND decision.record_integrity_status = 'verified'
                  AND decision.recorded_by_principal_ref LIKE 'lians:principal:v1:%'
                  AND decision.recorded_by_principal_ref <>
                      '{_LEGACY_PRINCIPAL}'
                  AND length(decision.recorded_by_principal_ref) > 20
                  AND decision.recorded_by_auth_method <>
                      '{_LEGACY_AUTH_METHOD}'
                  AND length(decision.recorded_by_auth_method) > 0
                  AND decision.recorded_by_credential_ref LIKE
                      'lians:credential:v1:sha256:%'
                  AND length(decision.recorded_by_credential_ref) = 91
                  {v3_snapshot}
            ) THEN
                RAISE EXCEPTION
                    'execution permits require an authenticated verified decision record';
            END IF;
            RETURN NEW;
        END;
        $$"""
    )
    op.execute(
        "REVOKE ALL ON FUNCTION public.lians_gate_require_verified_decision_record() FROM PUBLIC"
    )


def _postgresql_replace_constraints(*, hash_version_check: str, provenance_check: str) -> None:
    op.execute(
        "ALTER TABLE public.decision_records "
        "DROP CONSTRAINT ck_decision_record_provenance_state, "
        "DROP CONSTRAINT ck_decision_record_hash_version"
    )
    op.execute(
        "ALTER TABLE public.decision_records ADD CONSTRAINT "
        "ck_decision_record_hash_version "
        f"CHECK ({hash_version_check}) NOT VALID"
    )
    op.execute(
        "ALTER TABLE public.decision_records ADD CONSTRAINT "
        "ck_decision_record_provenance_state "
        f"CHECK ({provenance_check}) NOT VALID"
    )
    op.execute(
        "ALTER TABLE public.decision_records VALIDATE CONSTRAINT ck_decision_record_hash_version"
    )
    op.execute(
        "ALTER TABLE public.decision_records "
        "VALIDATE CONSTRAINT ck_decision_record_provenance_state"
    )


def _assert_postgresql_postflight() -> None:
    op.execute(
        f"""DO $$
        DECLARE
            validator record;
        BEGIN
            IF EXISTS (
                SELECT 1
                  FROM pg_catalog.pg_constraint
                 WHERE conrelid = 'public.decision_records'::regclass
                   AND conname IN (
                       'ck_decision_record_hash_version',
                       'ck_decision_record_provenance_state'
                   )
                   AND NOT convalidated
            ) THEN
                RAISE EXCEPTION '0057 left an unvalidated DecisionRecord constraint';
            END IF;

            SELECT provolatile, prosecdef, proparallel, proconfig
              INTO validator
              FROM pg_catalog.pg_proc
             WHERE oid = to_regprocedure('{_SCOPE_VALIDATOR}');
            IF NOT FOUND
               OR validator.provolatile <> 'i'
               OR validator.prosecdef
               OR validator.proparallel <> 's'
               OR NOT coalesce(validator.proconfig, ARRAY[]::text[])
                   @> ARRAY['search_path=pg_catalog']::text[] THEN
                RAISE EXCEPTION '0057 scope validator posture is invalid';
            END IF;
            IF EXISTS (
                SELECT 1
                  FROM pg_catalog.pg_proc AS function
                  CROSS JOIN LATERAL aclexplode(
                      coalesce(
                          function.proacl,
                          acldefault('f', function.proowner)
                      )
                  ) AS privilege
                 WHERE function.oid = to_regprocedure('{_SCOPE_VALIDATOR}')
                   AND privilege.grantee = 0
                   AND privilege.privilege_type = 'EXECUTE'
            ) OR NOT has_function_privilege(
                'lians_runtime', '{_SCOPE_VALIDATOR}', 'EXECUTE'
            ) THEN
                RAISE EXCEPTION '0057 scope validator grants are invalid';
            END IF;
        END;
        $$"""
    )


def _capture_sqlite_decision_triggers() -> list[tuple[str, str]]:
    rows = op.get_bind().exec_driver_sql(
        """SELECT name, sql
             FROM sqlite_master
            WHERE type = 'trigger'
              AND tbl_name = 'decision_records'
              AND sql IS NOT NULL
            ORDER BY name"""
    )
    return [(str(row[0]), str(row[1])) for row in rows]


def _restore_sqlite_decision_triggers(
    triggers: Sequence[tuple[str, str]],
) -> None:
    bind = op.get_bind()
    for name, sql in triggers:
        if name in _REPLACED_SQLITE_DECISION_TRIGGERS:
            continue
        quoted_name = name.replace('"', '""')
        bind.exec_driver_sql(f'DROP TRIGGER IF EXISTS "{quoted_name}"')
        bind.exec_driver_sql(sql)


def _install_sqlite_immutable_guard(*, include_auth_snapshot: bool) -> None:
    columns = _HASH_COVERED_COLUMNS_V3 if include_auth_snapshot else _HASH_COVERED_COLUMNS_V2
    op.execute("DROP TRIGGER IF EXISTS trg_decision_record_immutable")
    op.execute(
        f"""CREATE TRIGGER trg_decision_record_immutable
        BEFORE UPDATE OF {", ".join(columns)} ON decision_records
        BEGIN
            SELECT RAISE(ABORT, 'hash-covered decision record fields are immutable');
        END"""
    )


def _install_sqlite_scope_guard() -> None:
    op.execute("DROP TRIGGER IF EXISTS trg_decision_authorization_scope_insert")
    op.execute(
        f"""CREATE TRIGGER trg_decision_authorization_scope_insert
        BEFORE INSERT ON decision_records
        WHEN CASE
            WHEN json_valid(NEW.recorded_by_scopes) <> 1 THEN 1
            WHEN json_type(NEW.recorded_by_scopes) <> 'array' THEN 1
            WHEN NEW.record_hash_version IN (1, 2) THEN
                json_array_length(NEW.recorded_by_scopes) <> 0
            WHEN NEW.record_hash_version = 3 THEN
                json_array_length(NEW.recorded_by_scopes) NOT BETWEEN 1 AND {_SCOPE_LIMIT}
                OR EXISTS (
                    SELECT 1
                      FROM json_each(NEW.recorded_by_scopes) AS scope
                     WHERE scope.type <> 'text'
                        OR length(CAST(scope.value AS TEXT)) NOT BETWEEN 1 AND 100
                        OR CAST(scope.value AS TEXT)
                            GLOB '*[^-A-Za-z0-9_.:]*'
                )
                OR (
                    SELECT count(*) FROM json_each(NEW.recorded_by_scopes)
                ) <> (
                    SELECT count(DISTINCT CAST(scope.value AS TEXT))
                      FROM json_each(NEW.recorded_by_scopes) AS scope
                )
                OR NOT EXISTS (
                    SELECT 1
                      FROM json_each(NEW.recorded_by_scopes) AS scope
                     WHERE scope.type = 'text' AND scope.value = 'write'
                )
            ELSE 1
        END
        BEGIN
            SELECT RAISE(
                ABORT,
                'DecisionRecord authorization scope snapshot is invalid'
            );
        END"""
    )


def _install_sqlite_gate_guard(*, allow_v3: bool) -> None:
    versions = "IN (2, 3)" if allow_v3 else "= 2"
    v3_snapshot = ""
    if allow_v3:
        v3_snapshot = f"""
              AND (
                  decision.record_hash_version = 2
                  OR (
                      decision.recorded_by_principal_type IS NOT NULL
                      AND length(decision.recorded_by_principal_type)
                          BETWEEN 1 AND 32
                      AND decision.recorded_by_principal_type
                          NOT GLOB '*[^-A-Za-z0-9_.:]*'
                      AND (
                          decision.recorded_by_role IS NULL
                          OR decision.recorded_by_role IN (
                              'owner', 'analyst', 'compliance', 'readonly'
                          )
                      )
                      AND CASE
                          WHEN json_valid(decision.recorded_by_scopes) <> 1
                              THEN 0
                          WHEN json_type(decision.recorded_by_scopes) <> 'array'
                              THEN 0
                          WHEN json_array_length(decision.recorded_by_scopes)
                              NOT BETWEEN 1 AND {_SCOPE_LIMIT} THEN 0
                          WHEN EXISTS (
                              SELECT 1
                                FROM json_each(
                                    decision.recorded_by_scopes
                                ) AS scope
                               WHERE scope.type <> 'text'
                                  OR length(CAST(scope.value AS TEXT))
                                      NOT BETWEEN 1 AND 100
                                  OR CAST(scope.value AS TEXT)
                                      GLOB '*[^-A-Za-z0-9_.:]*'
                          ) THEN 0
                          WHEN (
                              SELECT count(*)
                                FROM json_each(decision.recorded_by_scopes)
                          ) <> (
                              SELECT count(DISTINCT CAST(scope.value AS TEXT))
                                FROM json_each(
                                    decision.recorded_by_scopes
                                ) AS scope
                          ) THEN 0
                          WHEN NOT EXISTS (
                              SELECT 1
                                FROM json_each(
                                    decision.recorded_by_scopes
                                ) AS scope
                               WHERE scope.type = 'text'
                                 AND scope.value = 'write'
                          ) THEN 0
                          ELSE 1
                      END = 1
                  )
              )
        """
    op.execute("DROP TRIGGER IF EXISTS trg_gate_execution_permit_require_verified_decision")
    op.execute(
        f"""CREATE TRIGGER trg_gate_execution_permit_require_verified_decision
        BEFORE INSERT ON gate_execution_permits
        WHEN NOT EXISTS (
            SELECT 1
            FROM decision_records decision
            JOIN event_log binding
              ON binding.namespace = decision.namespace
             AND binding.op = 'decision_recorded'
             AND binding.agent_id = decision.recorded_by_principal_ref
             AND binding.content_hash = decision.record_hash
             AND json_extract(binding.payload, '$.schema') =
                 'lians.decision-record-binding.v1'
             AND replace(json_extract(binding.payload, '$.decision_id'), '-', '') =
                 replace(CAST(decision.id AS TEXT), '-', '')
             AND json_extract(binding.payload, '$.record_hash') = decision.record_hash
            WHERE decision.id = NEW.decision_id
              AND decision.namespace = NEW.namespace
              AND decision.barrier_group IS NEW.barrier_group
              AND decision.record_hash_version {versions}
              AND decision.record_integrity_status = 'verified'
              AND decision.recorded_by_principal_ref LIKE 'lians:principal:v1:%'
              AND decision.recorded_by_principal_ref <> '{_LEGACY_PRINCIPAL}'
              AND length(decision.recorded_by_principal_ref) > 20
              AND decision.recorded_by_auth_method <> '{_LEGACY_AUTH_METHOD}'
              AND length(decision.recorded_by_auth_method) > 0
              AND decision.recorded_by_credential_ref LIKE
                  'lians:credential:v1:sha256:%'
              AND length(decision.recorded_by_credential_ref) = 91
              {v3_snapshot}
        )
        BEGIN
            SELECT RAISE(
                ABORT,
                'execution permits require an authenticated verified decision record'
            );
        END"""
    )


def _add_postgresql_columns() -> None:
    op.add_column(
        "decision_records",
        sa.Column("recorded_by_principal_type", sa.String(length=32), nullable=True),
    )
    op.add_column(
        "decision_records",
        sa.Column("recorded_by_role", sa.String(length=64), nullable=True),
    )
    op.add_column(
        "decision_records",
        sa.Column(
            "recorded_by_scopes",
            sa.JSON(),
            nullable=False,
            server_default=sa.text("'[]'"),
        ),
    )


def _upgrade_postgresql() -> None:
    _set_postgresql_migration_scope()
    _add_postgresql_columns()
    _install_postgresql_scope_validator()
    _postgresql_replace_constraints(
        hash_version_check=_NEW_HASH_VERSION_CHECK,
        provenance_check=_POSTGRES_PROVENANCE_CHECK,
    )
    _install_postgresql_immutable_guard(include_auth_snapshot=True)
    _install_postgresql_gate_guard(allow_v3=True)
    _assert_postgresql_postflight()


def _upgrade_sqlite() -> None:
    triggers = _capture_sqlite_decision_triggers()
    with op.batch_alter_table("decision_records", recreate="always") as batch:
        batch.add_column(
            sa.Column(
                "recorded_by_principal_type",
                sa.String(length=32),
                nullable=True,
            )
        )
        batch.add_column(sa.Column("recorded_by_role", sa.String(length=64), nullable=True))
        batch.add_column(
            sa.Column(
                "recorded_by_scopes",
                sa.JSON(),
                nullable=False,
                server_default=sa.text("'[]'"),
            )
        )
        batch.drop_constraint("ck_decision_record_provenance_state", type_="check")
        batch.drop_constraint("ck_decision_record_hash_version", type_="check")
        batch.create_check_constraint("ck_decision_record_hash_version", _NEW_HASH_VERSION_CHECK)
        batch.create_check_constraint(
            "ck_decision_record_provenance_state", _SQLITE_PROVENANCE_CHECK
        )
    _restore_sqlite_decision_triggers(triggers)
    _install_sqlite_immutable_guard(include_auth_snapshot=True)
    _install_sqlite_scope_guard()
    _install_sqlite_gate_guard(allow_v3=True)


def upgrade() -> None:
    dialect = op.get_bind().dialect.name
    if dialect == "postgresql":
        _upgrade_postgresql()
    elif dialect == "sqlite":
        if context.is_offline_mode():
            raise RuntimeError(
                "0057_decision_auth_snapshot requires an online SQLite "
                "connection to preserve the installed evidence triggers"
            )
        _upgrade_sqlite()
    else:
        raise RuntimeError(f"Decision authorization snapshots are unsupported on {dialect}")


def _assert_no_v3_records() -> None:
    if op.get_bind().dialect.name == "postgresql":
        op.execute(
            """DO $$
            BEGIN
                IF EXISTS (
                    SELECT 1 FROM public.decision_records
                     WHERE record_hash_version = 3
                ) THEN
                    RAISE EXCEPTION
                        '0057 downgrade refused: DecisionRecord v3 rows exist';
                END IF;
            END;
            $$"""
        )
        return
    count = (
        op.get_bind()
        .execute(sa.text("SELECT count(*) FROM decision_records WHERE record_hash_version = 3"))
        .scalar_one()
    )
    if int(count):
        raise RuntimeError("0057 downgrade refused: DecisionRecord v3 rows exist")


def _downgrade_postgresql() -> None:
    _set_postgresql_migration_scope()
    _assert_no_v3_records()
    _install_postgresql_gate_guard(allow_v3=False)
    _postgresql_replace_constraints(
        hash_version_check=_OLD_HASH_VERSION_CHECK,
        provenance_check=_OLD_PROVENANCE_CHECK,
    )
    _install_postgresql_immutable_guard(include_auth_snapshot=False)
    op.drop_column("decision_records", "recorded_by_scopes")
    op.drop_column("decision_records", "recorded_by_role")
    op.drop_column("decision_records", "recorded_by_principal_type")
    op.execute(f"DROP FUNCTION IF EXISTS {_SCOPE_VALIDATOR}")


def _downgrade_sqlite() -> None:
    _assert_no_v3_records()
    triggers = _capture_sqlite_decision_triggers()
    with op.batch_alter_table("decision_records", recreate="always") as batch:
        batch.drop_constraint("ck_decision_record_provenance_state", type_="check")
        batch.drop_constraint("ck_decision_record_hash_version", type_="check")
        batch.create_check_constraint("ck_decision_record_hash_version", _OLD_HASH_VERSION_CHECK)
        batch.create_check_constraint("ck_decision_record_provenance_state", _OLD_PROVENANCE_CHECK)
        batch.drop_column("recorded_by_scopes")
        batch.drop_column("recorded_by_role")
        batch.drop_column("recorded_by_principal_type")
    _restore_sqlite_decision_triggers(triggers)
    _install_sqlite_immutable_guard(include_auth_snapshot=False)
    _install_sqlite_gate_guard(allow_v3=False)


def downgrade() -> None:
    dialect = op.get_bind().dialect.name
    if dialect == "postgresql":
        _downgrade_postgresql()
    elif dialect == "sqlite":
        if context.is_offline_mode():
            raise RuntimeError(
                "0057_decision_auth_snapshot downgrade requires an online "
                "SQLite connection to preserve the installed evidence triggers"
            )
        _downgrade_sqlite()
    else:
        raise RuntimeError(f"Decision authorization snapshots are unsupported on {dialect}")
