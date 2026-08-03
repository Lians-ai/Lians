"""Contract authentication-table RLS and SCIM membership capacity.

Revision ID: 0056b_auth_lookup_contract
Revises: 0056a_admission_index

This contract revision requires a drained authentication writer/reader fence:
older binaries query api_keys and identity_bindings directly before setting a
tenant context. The 0.5 runtime uses the exact functions installed by 0056.
"""

from __future__ import annotations

from alembic import op

revision = "0056b_auth_lookup_contract"
down_revision = "0056a_admission_index"
branch_labels = None
depends_on = None

_USER_GROUP_LIMIT = 1_000
_CAPACITY_FUNCTION = "public.lians_scim_enforce_user_group_capacity()"
_CAPACITY_TRIGGER = "trg_scim_user_group_capacity"
_API_KEY_SIGNATURE = "public.lians_auth_lookup_api_key(text)"
_IDENTITY_SIGNATURE = "public.lians_auth_lookup_identity_binding(uuid,text)"


def _assert_postgresql_preconditions() -> None:
    op.execute(
        f"""DO $$
        DECLARE
            over_capacity boolean;
            unsafe_function text;
            unsafe_role record;
        BEGIN
            SELECT rolcanlogin, rolsuper, rolbypassrls
              INTO unsafe_role
              FROM pg_catalog.pg_roles
             WHERE rolname = 'lians_runtime';
            IF NOT FOUND
               OR unsafe_role.rolcanlogin
               OR unsafe_role.rolsuper
               OR unsafe_role.rolbypassrls THEN
                RAISE EXCEPTION
                    '0056b requires safe NOLOGIN NOSUPERUSER NOBYPASSRLS lians_runtime';
            END IF;

            SELECT EXISTS (
                SELECT 1
                  FROM public.scim_group_members
                 GROUP BY tenant_config_id, user_id
                HAVING count(*) > {_USER_GROUP_LIMIT}
            ) INTO over_capacity;
            IF over_capacity THEN
                RAISE EXCEPTION
                    '0056b refused: existing SCIM User Group membership exceeds {_USER_GROUP_LIMIT}';
            END IF;

            SELECT function.proname
              INTO unsafe_function
              FROM pg_catalog.pg_proc AS function
              JOIN pg_catalog.pg_namespace AS schema
                ON schema.oid = function.pronamespace
              JOIN pg_catalog.pg_class AS relation
                ON relation.relname = CASE function.proname
                    WHEN 'lians_auth_lookup_api_key' THEN 'api_keys'
                    ELSE 'identity_bindings'
                END
              JOIN pg_catalog.pg_namespace AS relation_schema
                ON relation_schema.oid = relation.relnamespace
             WHERE schema.nspname = 'public'
               AND relation_schema.nspname = 'public'
               AND function.proname IN (
                   'lians_auth_lookup_api_key',
                   'lians_auth_lookup_identity_binding'
               )
               AND (
                   NOT function.prosecdef
                   OR function.proowner <> relation.relowner
                   OR NOT coalesce(function.proconfig, ARRAY[]::text[])
                       @> ARRAY['row_security=off']::text[]
                   OR NOT array_to_string(
                       coalesce(function.proconfig, ARRAY[]::text[]), ','
                   ) LIKE '%search_path=pg_catalog, public%'
               )
             ORDER BY function.proname
             LIMIT 1;
            IF unsafe_function IS NOT NULL THEN
                RAISE EXCEPTION
                    '0056b refused unsafe authentication function %',
                    unsafe_function;
            END IF;

            IF to_regprocedure('{_API_KEY_SIGNATURE}') IS NULL
               OR to_regprocedure('{_IDENTITY_SIGNATURE}') IS NULL THEN
                RAISE EXCEPTION
                    '0056b requires both exact authentication lookup functions';
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
                 WHERE function.oid IN (
                     to_regprocedure('{_API_KEY_SIGNATURE}'),
                     to_regprocedure('{_IDENTITY_SIGNATURE}')
                 )
                   AND privilege.grantee = 0
                   AND privilege.privilege_type = 'EXECUTE'
            ) THEN
                RAISE EXCEPTION
                    '0056b authentication lookup functions remain executable by PUBLIC';
            END IF;
            IF NOT has_function_privilege(
                'lians_runtime', '{_API_KEY_SIGNATURE}', 'EXECUTE'
            ) OR NOT has_function_privilege(
                'lians_runtime', '{_IDENTITY_SIGNATURE}', 'EXECUTE'
            ) THEN
                RAISE EXCEPTION
                    '0056b runtime authentication function grants are incomplete';
            END IF;
        END;
        $$"""
    )


def _install_postgresql_capacity_boundary() -> None:
    op.execute(
        f"""CREATE OR REPLACE FUNCTION
        public.lians_scim_enforce_user_group_capacity()
        RETURNS trigger
        LANGUAGE plpgsql
        SECURITY DEFINER
        SET search_path = pg_catalog, public
        AS $$
        DECLARE
            membership_total bigint;
        BEGIN
            IF TG_OP = 'UPDATE' THEN
                IF OLD.user_id = NEW.user_id
                   AND OLD.tenant_config_id = NEW.tenant_config_id
                   AND OLD.namespace = NEW.namespace THEN
                    RETURN NEW;
                END IF;
            END IF;

            PERFORM 1
              FROM public.scim_tenant_configs AS config
             WHERE config.id = NEW.tenant_config_id
               AND config.namespace = NEW.namespace
             FOR UPDATE;
            IF NOT FOUND THEN
                RAISE EXCEPTION 'SCIM membership tenant boundary is unavailable'
                    USING ERRCODE = '23503';
            END IF;

            SELECT count(*)
              INTO membership_total
              FROM public.scim_group_members AS membership
             WHERE membership.tenant_config_id = NEW.tenant_config_id
               AND membership.user_id = NEW.user_id;
            IF membership_total >= {_USER_GROUP_LIMIT} THEN
                RAISE EXCEPTION
                    'SCIM User Group membership capacity exceeded'
                    USING ERRCODE = '23514',
                          CONSTRAINT = 'ck_scim_user_group_capacity';
            END IF;
            RETURN NEW;
        END;
        $$"""
    )
    op.execute(f"REVOKE ALL ON FUNCTION {_CAPACITY_FUNCTION} FROM PUBLIC")
    op.execute(
        f"DROP TRIGGER IF EXISTS {_CAPACITY_TRIGGER} "
        "ON public.scim_group_members"
    )
    op.execute(
        f"""CREATE TRIGGER {_CAPACITY_TRIGGER}
        BEFORE INSERT OR UPDATE ON public.scim_group_members
        FOR EACH ROW EXECUTE FUNCTION {_CAPACITY_FUNCTION}"""
    )


def _install_postgresql_auth_rls() -> None:
    for table in ("api_keys", "identity_bindings"):
        op.execute(f"REVOKE ALL ON TABLE public.{table} FROM PUBLIC")
        op.execute(f"ALTER TABLE public.{table} ENABLE ROW LEVEL SECURITY")
        # SECURITY DEFINER lookup functions are owned by the table owner. The
        # runtime login is proven to be a distinct non-owner without BYPASSRLS,
        # so direct table access remains authoritative even though FORCE cannot
        # be used on these two owner-bypass bootstrap relations.
        op.execute(f"ALTER TABLE public.{table} NO FORCE ROW LEVEL SECURITY")
        op.execute(
            f"DROP POLICY IF EXISTS rls_{table}_namespace ON public.{table}"
        )
        op.execute(
            f"""CREATE POLICY rls_{table}_namespace ON public.{table}
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
            f"DROP POLICY IF EXISTS rls_{table}_barrier ON public.{table}"
        )
        op.execute(
            f"""CREATE POLICY rls_{table}_barrier ON public.{table}
            AS RESTRICTIVE
            USING (
                current_setting('app.current_namespace', true) = '__admin__'
                OR current_setting('agentmem.barrier_group', true) = ''
                OR barrier_group = current_setting(
                    'agentmem.barrier_group', true
                )
            )
            WITH CHECK (
                current_setting('app.current_namespace', true) = '__admin__'
                OR current_setting('agentmem.barrier_group', true) = ''
                OR barrier_group = current_setting(
                    'agentmem.barrier_group', true
                )
            )"""
        )


def _assert_postgresql_postflight() -> None:
    op.execute(
        """DO $$
        DECLARE
            violation text;
        BEGIN
            SELECT relation.relname
              INTO violation
              FROM pg_catalog.pg_class AS relation
              JOIN pg_catalog.pg_namespace AS schema
                ON schema.oid = relation.relnamespace
             WHERE schema.nspname = 'public'
               AND relation.relname IN ('api_keys', 'identity_bindings')
               AND (
                   NOT relation.relrowsecurity
                   OR relation.relforcerowsecurity
                   OR NOT EXISTS (
                       SELECT 1
                         FROM pg_catalog.pg_policy AS policy
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
                   OR NOT EXISTS (
                       SELECT 1
                         FROM pg_catalog.pg_policy AS policy
                        WHERE policy.polrelid = relation.oid
                          AND NOT policy.polpermissive
                          AND position(
                              'agentmem.barrier_group' IN concat(
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
                RAISE EXCEPTION
                    '0056b authentication RLS postflight failed for %',
                    violation;
            END IF;
        END;
        $$"""
    )


def _install_sqlite_capacity_boundary() -> None:
    op.execute(
        f"""CREATE TRIGGER trg_scim_user_group_capacity_insert
        BEFORE INSERT ON scim_group_members
        WHEN (
            SELECT count(*) FROM scim_group_members
            WHERE tenant_config_id = NEW.tenant_config_id
              AND user_id = NEW.user_id
        ) >= {_USER_GROUP_LIMIT}
        BEGIN
            SELECT RAISE(ABORT, 'SCIM User Group membership capacity exceeded');
        END"""
    )
    op.execute(
        f"""CREATE TRIGGER trg_scim_user_group_capacity_update
        BEFORE UPDATE OF user_id, tenant_config_id, namespace
        ON scim_group_members
        WHEN (
            OLD.user_id IS NOT NEW.user_id
            OR OLD.tenant_config_id IS NOT NEW.tenant_config_id
            OR OLD.namespace IS NOT NEW.namespace
        ) AND (
            SELECT count(*) FROM scim_group_members
            WHERE tenant_config_id = NEW.tenant_config_id
              AND user_id = NEW.user_id
        ) >= {_USER_GROUP_LIMIT}
        BEGIN
            SELECT RAISE(ABORT, 'SCIM User Group membership capacity exceeded');
        END"""
    )


def upgrade() -> None:
    if op.get_bind().dialect.name == "postgresql":
        _assert_postgresql_preconditions()
        _install_postgresql_capacity_boundary()
        _install_postgresql_auth_rls()
        _assert_postgresql_postflight()
    else:
        _install_sqlite_capacity_boundary()


def downgrade() -> None:
    if op.get_bind().dialect.name == "postgresql":
        for table in ("identity_bindings", "api_keys"):
            op.execute(
                f"DROP POLICY IF EXISTS rls_{table}_barrier ON public.{table}"
            )
            op.execute(
                f"DROP POLICY IF EXISTS rls_{table}_namespace ON public.{table}"
            )
            op.execute(f"ALTER TABLE public.{table} DISABLE ROW LEVEL SECURITY")
        op.execute(
            f"DROP TRIGGER IF EXISTS {_CAPACITY_TRIGGER} "
            "ON public.scim_group_members"
        )
        op.execute(f"DROP FUNCTION IF EXISTS {_CAPACITY_FUNCTION}")
    else:
        op.execute("DROP TRIGGER IF EXISTS trg_scim_user_group_capacity_update")
        op.execute("DROP TRIGGER IF EXISTS trg_scim_user_group_capacity_insert")
