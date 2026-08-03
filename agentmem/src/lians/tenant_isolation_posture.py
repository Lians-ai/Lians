"""Dynamic PostgreSQL RLS posture checks for every tenant-bearing table.

The inventory is derived from the live catalog rather than a hand-maintained
table list, so a migration that adds a ``namespace`` or ``barrier_group``
column cannot silently omit its corresponding policy.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncSession


# These two relations are reached pre-authentication only through exact,
# PUBLIC-revoked SECURITY DEFINER functions. Direct runtime table access is now
# RLS constrained. They deliberately omit FORCE because the reviewed function
# owner must bypass owner RLS before a tenant GUC exists; production readiness
# separately proves the runtime login is neither owner nor BYPASSRLS and cannot
# assume the owner. No table is excepted from ENABLE RLS or policy checks.
TENANT_RLS_EXCEPTIONS: dict[str, str] = {
    "api_keys": "exact API-key SECURITY DEFINER lookup requires owner bypass",
    "identity_bindings": "exact OIDC binding SECURITY DEFINER lookup requires owner bypass",
}

# Namespace-only governance relations can still require an explicit
# unbarriered compliance session. Keep this exception-to-the-column-rule
# declarative so live-catalog startup checks cannot regress the special fence.
RESTRICTED_UNBARRIERED_RELATIONS = frozenset({"validmind_model_links"})


def _backend_name(db: AsyncSession | AsyncConnection) -> str:
    bind = db.get_bind() if hasattr(db, "get_bind") else db
    return bind.dialect.name


async def tenant_isolation_posture_status(
    db: AsyncSession | AsyncConnection,
) -> dict[str, Any]:
    """Verify ENABLE/FORCE RLS and applicable policies from PostgreSQL catalogs."""

    backend = _backend_name(db)
    if backend != "postgresql":
        return {
            "backend": backend,
            "enforced": False,
            "checks": {"postgresql_backend": False},
            "relations": [],
            "violations": ["postgresql_backend_required"],
            "exceptions": {},
        }

    rows = (
        await db.execute(
            text(
                """WITH tenant_relations AS (
                       SELECT
                           relation.oid,
                           relation.relname AS table_name,
                           relation.relrowsecurity AS rls_enabled,
                           relation.relforcerowsecurity AS rls_forced,
                           EXISTS (
                               SELECT 1
                               FROM pg_attribute AS attribute
                               WHERE attribute.attrelid = relation.oid
                                 AND attribute.attname = 'namespace'
                                 AND attribute.attnum > 0
                                 AND NOT attribute.attisdropped
                           ) AS has_namespace,
                           EXISTS (
                               SELECT 1
                               FROM pg_attribute AS attribute
                               WHERE attribute.attrelid = relation.oid
                                 AND attribute.attname = 'barrier_group'
                                 AND attribute.attnum > 0
                                 AND NOT attribute.attisdropped
                           ) AS has_barrier
                       FROM pg_class AS relation
                       JOIN pg_namespace AS schema
                         ON schema.oid = relation.relnamespace
                       WHERE schema.nspname = 'public'
                         AND relation.relkind IN ('r', 'p')
                   ), policy_posture AS (
                       SELECT
                           policy.polrelid,
                           COALESCE(
                               bool_or(
                                   policy.polpermissive
                                   AND position(
                                       'app.current_namespace' IN concat(
                                           pg_get_expr(
                                               policy.polqual,
                                               policy.polrelid,
                                               true
                                           ),
                                           ' ',
                                           pg_get_expr(
                                               policy.polwithcheck,
                                               policy.polrelid,
                                               true
                                           )
                                       )
                                   ) > 0
                               ),
                               false
                           ) AS namespace_policy,
                           COALESCE(
                               bool_or(
                                   NOT policy.polpermissive
                                   AND position(
                                       'agentmem.barrier_group' IN concat(
                                           pg_get_expr(
                                               policy.polqual,
                                               policy.polrelid,
                                               true
                                           ),
                                           ' ',
                                           pg_get_expr(
                                               policy.polwithcheck,
                                               policy.polrelid,
                                               true
                                           )
                                       )
                                   ) > 0
                               ),
                               false
                           ) AS barrier_policy
                       FROM pg_policy AS policy
                       GROUP BY policy.polrelid
                   )
                   SELECT
                       tenant_relations.table_name,
                       tenant_relations.rls_enabled,
                       tenant_relations.rls_forced,
                       tenant_relations.has_namespace,
                       tenant_relations.has_barrier,
                       COALESCE(policy_posture.namespace_policy, false)
                           AS namespace_policy,
                       COALESCE(policy_posture.barrier_policy, false)
                           AS barrier_policy
                   FROM tenant_relations
                   LEFT JOIN policy_posture
                     ON policy_posture.polrelid = tenant_relations.oid
                   WHERE tenant_relations.has_namespace
                      OR tenant_relations.has_barrier
                   ORDER BY tenant_relations.table_name"""
            )
        )
    ).mappings().all()

    auth_lookup_rows = (
        await db.execute(
            text(
                """WITH expected(signature, function_name, table_name) AS (
                       VALUES
                           (
                               'public.lians_auth_lookup_api_key(text)',
                               'lians_auth_lookup_api_key',
                               'api_keys'
                           ),
                           (
                               'public.lians_auth_lookup_identity_binding(uuid,text)',
                               'lians_auth_lookup_identity_binding',
                               'identity_bindings'
                           )
                   )
                   SELECT expected.function_name,
                          expected.table_name,
                          function.oid IS NOT NULL AS function_exists,
                          COALESCE(function.prosecdef, false) AS security_definer,
                          COALESCE(
                              function.proowner = relation.relowner,
                              false
                          ) AS owner_matches_table,
                          COALESCE(
                              function.proconfig @> ARRAY['row_security=off']::text[],
                              false
                          ) AS row_security_off,
                          COALESCE(
                              array_to_string(function.proconfig, ',')
                                  LIKE '%search_path=pg_catalog, public%',
                              false
                          ) AS fixed_search_path,
                          COALESCE(
                              has_function_privilege(
                                  current_user,
                                  function.oid,
                                  'EXECUTE'
                              ),
                              false
                          ) AS runtime_can_execute,
                          NOT EXISTS (
                              SELECT 1
                                FROM aclexplode(
                                    coalesce(
                                        function.proacl,
                                        acldefault('f', function.proowner)
                                    )
                                ) AS privilege
                               WHERE privilege.grantee = 0
                                 AND privilege.privilege_type = 'EXECUTE'
                          ) AS public_cannot_execute,
                          CASE expected.function_name
                              WHEN 'lians_auth_lookup_identity_binding' THEN
                                  COALESCE(
                                      position(
                                          'scim_reconciliation_complete'
                                          IN function.prosrc
                                      ) > 0,
                                      false
                                  )
                              ELSE true
                          END AS contract_definition
                     FROM expected
                     LEFT JOIN pg_catalog.pg_proc AS function
                       ON function.oid = to_regprocedure(expected.signature)
                     LEFT JOIN pg_catalog.pg_class AS relation
                       ON relation.relname = expected.table_name
                      AND relation.relnamespace = (
                          SELECT oid
                            FROM pg_catalog.pg_namespace
                           WHERE nspname = 'public'
                      )
                    ORDER BY expected.function_name"""
            )
        )
    ).mappings().all()

    relations: list[dict[str, Any]] = []
    violations: list[str] = []
    applied_exceptions: dict[str, str] = {}
    for raw in rows:
        row = dict(raw)
        table_name = str(row["table_name"])
        exception_reason = TENANT_RLS_EXCEPTIONS.get(table_name)
        row["exception"] = exception_reason is not None
        if exception_reason is not None:
            applied_exceptions[table_name] = exception_reason
        if not bool(row["rls_enabled"]):
            violations.append(f"{table_name}:rls_not_enabled")
        if not bool(row["rls_forced"]) and exception_reason is None:
            violations.append(f"{table_name}:rls_not_forced")
        if bool(row["has_namespace"]) and not bool(row["namespace_policy"]):
            violations.append(f"{table_name}:namespace_policy_missing")
        if bool(row["has_barrier"]) and not bool(row["barrier_policy"]):
            violations.append(f"{table_name}:barrier_policy_missing")
        if (
            table_name in RESTRICTED_UNBARRIERED_RELATIONS
            and not bool(row["barrier_policy"])
        ):
            violations.append(f"{table_name}:unbarriered_policy_missing")
        relations.append(row)

    auth_lookups: list[dict[str, Any]] = []
    for raw in auth_lookup_rows:
        row = dict(raw)
        function_name = str(row["function_name"])
        for check in (
            "function_exists",
            "security_definer",
            "owner_matches_table",
            "row_security_off",
            "fixed_search_path",
            "runtime_can_execute",
            "public_cannot_execute",
            "contract_definition",
        ):
            if not bool(row[check]):
                violations.append(f"{function_name}:{check}")
        auth_lookups.append(row)

    checks = {
        "postgresql_backend": True,
        "tenant_relations_discovered": bool(relations),
        "tenant_rls_inventory_clean": not violations,
    }
    return {
        "backend": backend,
        "enforced": all(checks.values()),
        "checks": checks,
        "relations": relations,
        "auth_lookup_functions": auth_lookups,
        "violations": sorted(violations),
        "exceptions": applied_exceptions,
    }


__all__ = [
    "RESTRICTED_UNBARRIERED_RELATIONS",
    "TENANT_RLS_EXCEPTIONS",
    "tenant_isolation_posture_status",
]
