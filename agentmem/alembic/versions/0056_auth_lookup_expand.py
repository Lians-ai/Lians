"""Add exact pre-authentication lookup functions.

Revision ID: 0056_auth_lookup_expand
Revises: 0055_retention_cursor

This expand phase is safe while 0.4.2 callers still perform indexed table
lookups.  The companion contract revision enables RLS only after those callers
have been drained.
"""

from __future__ import annotations

from alembic import op

revision = "0056_auth_lookup_expand"
down_revision = "0055_retention_cursor"
branch_labels = None
depends_on = None

_API_KEY_SIGNATURE = "public.lians_auth_lookup_api_key(text)"
_IDENTITY_SIGNATURE = "public.lians_auth_lookup_identity_binding(uuid,text)"


def _install_postgresql_functions() -> None:
    # The definer must be the application-table owner. Once the contract phase
    # enables (but deliberately does not FORCE) RLS on these two bootstrap
    # relations, owner bypass is what permits only these reviewed exact lookups
    # to run before an authenticated namespace exists.
    op.execute(
        """DO $$
        DECLARE
            unsafe_role record;
            owner_mismatch text;
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
                    '0056 requires safe NOLOGIN NOSUPERUSER NOBYPASSRLS lians_runtime';
            END IF;

            SELECT relation.relname
              INTO owner_mismatch
              FROM pg_catalog.pg_class AS relation
              JOIN pg_catalog.pg_namespace AS schema
                ON schema.oid = relation.relnamespace
             WHERE schema.nspname = 'public'
               AND relation.relname IN ('api_keys', 'identity_bindings')
               AND relation.relowner <> (
                   SELECT oid
                   FROM pg_catalog.pg_roles
                   WHERE rolname = current_user
               )
             ORDER BY relation.relname
             LIMIT 1;
            IF owner_mismatch IS NOT NULL THEN
                RAISE EXCEPTION
                    '0056 migrator must own authentication table %',
                    owner_mismatch;
            END IF;
        END;
        $$"""
    )
    op.execute(
        """CREATE OR REPLACE FUNCTION public.lians_auth_lookup_api_key(
            p_hashed_key text
        ) RETURNS TABLE (
            id uuid,
            namespace text,
            scopes jsonb,
            role text,
            barrier_group text,
            provisioning_source text,
            last_used_at timestamptz,
            authenticated_at timestamptz
        )
        LANGUAGE plpgsql
        STABLE
        SECURITY DEFINER
        SET search_path = pg_catalog, public
        SET row_security = off
        AS $$
        BEGIN
            IF NOT pg_catalog.pg_has_role(
                session_user, 'lians_runtime', 'MEMBER'
            ) AND session_user <> current_user THEN
                RAISE EXCEPTION 'authentication lookup is not authorized'
                    USING ERRCODE = '42501';
            END IF;
            IF p_hashed_key IS NULL
               OR p_hashed_key !~ '^[0-9a-f]{64}$' THEN
                RETURN;
            END IF;
            RETURN QUERY
            SELECT credential.id,
                   credential.namespace::text,
                   credential.scopes::jsonb,
                   credential.role::text,
                   credential.barrier_group::text,
                   credential.provisioning_source::text,
                   credential.last_used_at,
                   statement_timestamp()
              FROM public.api_keys AS credential
             WHERE credential.hashed_key = p_hashed_key
               AND credential.revoked_at IS NULL
               AND (
                   credential.expires_at IS NULL
                   OR credential.expires_at > statement_timestamp()
               );
        END;
        $$"""
    )
    op.execute(
        """CREATE OR REPLACE FUNCTION
        public.lians_auth_lookup_identity_binding(
            p_provider_id uuid,
            p_external_subject text
        ) RETURNS TABLE (
            id uuid,
            namespace text,
            scopes jsonb,
            role text,
            barrier_group text,
            authorized_party text,
            principal_type text
        )
        LANGUAGE plpgsql
        STABLE
        SECURITY DEFINER
        SET search_path = pg_catalog, public
        SET row_security = off
        AS $$
        BEGIN
            IF NOT pg_catalog.pg_has_role(
                session_user, 'lians_runtime', 'MEMBER'
            ) AND session_user <> current_user THEN
                RAISE EXCEPTION 'authentication lookup is not authorized'
                    USING ERRCODE = '42501';
            END IF;
            IF p_provider_id IS NULL
               OR p_external_subject IS NULL
               OR length(p_external_subject) NOT BETWEEN 1 AND 512 THEN
                RETURN;
            END IF;
            RETURN QUERY
            SELECT binding.id,
                   binding.namespace::text,
                   binding.scopes::jsonb,
                   binding.role::text,
                   binding.barrier_group::text,
                   binding.authorized_party::text,
                   binding.principal_type::text
              FROM public.identity_bindings AS binding
             WHERE binding.provider_id = p_provider_id
               AND binding.external_subject = p_external_subject
               AND binding.enabled IS TRUE
               AND binding.revoked_at IS NULL;
        END;
        $$"""
    )
    for signature in (_API_KEY_SIGNATURE, _IDENTITY_SIGNATURE):
        op.execute(f"REVOKE ALL ON FUNCTION {signature} FROM PUBLIC")
        op.execute(f"GRANT EXECUTE ON FUNCTION {signature} TO lians_runtime")


def upgrade() -> None:
    if op.get_bind().dialect.name == "postgresql":
        _install_postgresql_functions()


def downgrade() -> None:
    if op.get_bind().dialect.name == "postgresql":
        for signature in (_IDENTITY_SIGNATURE, _API_KEY_SIGNATURE):
            op.execute(f"REVOKE ALL ON FUNCTION {signature} FROM lians_runtime")
            op.execute(f"DROP FUNCTION IF EXISTS {signature}")
