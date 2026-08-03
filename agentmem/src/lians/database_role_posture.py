"""Fail-closed PostgreSQL runtime-role posture shared by startup and readiness."""

from __future__ import annotations

from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from .tenant_isolation_posture import tenant_isolation_posture_status


async def database_role_posture_status(db: AsyncSession) -> dict[str, Any]:
    """Inspect the active login and fixed capability role without exposing secrets."""

    backend = db.get_bind().dialect.name
    if backend != "postgresql":
        tenant_isolation = await tenant_isolation_posture_status(db)
        return {
            "backend": backend,
            "enforced": False,
            "role": None,
            "checks": {"postgresql_backend": False},
            "attributes": {},
            "tenant_isolation": tenant_isolation,
        }

    row = (
        await db.execute(
            text(
                """WITH active_role AS (
                       SELECT oid, rolname, rolcanlogin, rolinherit, rolsuper,
                              rolcreatedb, rolcreaterole, rolreplication,
                              rolbypassrls
                       FROM pg_roles
                       WHERE rolname = current_user
                   ), capability AS (
                       SELECT oid, rolname, rolcanlogin, rolinherit, rolsuper,
                              rolcreatedb, rolcreaterole, rolreplication,
                              rolbypassrls
                       FROM pg_roles
                       WHERE rolname = 'lians_runtime'
                   ), application_owners AS (
                       SELECT database.datdba AS owner_oid
                       FROM pg_database AS database
                       WHERE database.datname = current_database()
                       UNION
                       SELECT namespace.nspowner
                       FROM pg_namespace AS namespace
                       WHERE namespace.nspname NOT IN (
                           'pg_catalog', 'information_schema'
                       )
                         AND namespace.nspname !~ '^pg_toast'
                       UNION
                       SELECT relation.relowner
                       FROM pg_class AS relation
                       JOIN pg_namespace AS namespace
                         ON namespace.oid = relation.relnamespace
                       WHERE namespace.nspname NOT IN (
                           'pg_catalog', 'information_schema'
                       )
                         AND namespace.nspname !~ '^pg_toast'
                       UNION
                       SELECT procedure.proowner
                       FROM pg_proc AS procedure
                       JOIN pg_namespace AS namespace
                         ON namespace.oid = procedure.pronamespace
                       WHERE namespace.nspname NOT IN (
                           'pg_catalog', 'information_schema'
                       )
                         AND namespace.nspname !~ '^pg_toast'
                       UNION
                       SELECT data_type.typowner
                       FROM pg_type AS data_type
                       JOIN pg_namespace AS namespace
                         ON namespace.oid = data_type.typnamespace
                       WHERE namespace.nspname NOT IN (
                           'pg_catalog', 'information_schema'
                       )
                         AND namespace.nspname !~ '^pg_toast'
                   )
                   SELECT
                       active_role.rolname,
                       session_user AS session_role,
                       active_role.rolcanlogin,
                       active_role.rolinherit,
                       active_role.rolsuper,
                       active_role.rolcreatedb,
                       active_role.rolcreaterole,
                       active_role.rolreplication,
                       active_role.rolbypassrls,
                       capability.oid IS NOT NULL AS capability_exists,
                       COALESCE(capability.rolcanlogin, true) AS capability_login,
                       COALESCE(capability.rolsuper, true) AS capability_super,
                       COALESCE(capability.rolcreatedb, true) AS capability_createdb,
                       COALESCE(capability.rolcreaterole, true)
                           AS capability_createrole,
                       COALESCE(capability.rolreplication, true)
                           AS capability_replication,
                       COALESCE(capability.rolbypassrls, true)
                           AS capability_bypass,
                       COALESCE(
                           pg_has_role(active_role.oid, capability.oid, 'MEMBER'),
                           false
                       ) AS capability_member,
                       COALESCE(
                           pg_has_role(active_role.oid, capability.oid, 'USAGE'),
                           false
                       ) AS capability_usage,
                       EXISTS (
                           SELECT 1 FROM application_owners AS owner
                           WHERE owner.owner_oid = active_role.oid
                       ) AS runtime_owns_application_object,
                       EXISTS (
                           SELECT 1 FROM application_owners AS owner
                           WHERE owner.owner_oid = capability.oid
                       ) AS capability_owns_application_object,
                       EXISTS (
                           SELECT 1 FROM application_owners AS owner
                           WHERE owner.owner_oid <> active_role.oid
                             AND owner.owner_oid <> capability.oid
                             AND pg_has_role(
                                 active_role.oid, owner.owner_oid, 'MEMBER'
                             )
                       ) AS runtime_can_assume_application_owner,
                       EXISTS (
                           SELECT 1 FROM application_owners AS owner
                           WHERE owner.owner_oid <> capability.oid
                             AND pg_has_role(
                                 capability.oid, owner.owner_oid, 'MEMBER'
                             )
                       ) AS capability_can_assume_application_owner
                   FROM active_role
                   LEFT JOIN capability ON true"""
            )
        )
    ).mappings().one_or_none()
    attributes = dict(row or {})
    checks = {
        "postgresql_backend": True,
        "runtime_role_exists": bool(attributes),
        "runtime_is_login": bool(attributes.get("rolcanlogin", False)),
        "runtime_session_role_unchanged": bool(attributes)
        and attributes.get("rolname") == attributes.get("session_role"),
        "runtime_not_capability_role": bool(attributes)
        and attributes.get("rolname") != "lians_runtime",
        "runtime_not_superuser": not bool(attributes.get("rolsuper", True)),
        "runtime_not_bypassrls": not bool(attributes.get("rolbypassrls", True)),
        "runtime_cannot_create_databases": not bool(
            attributes.get("rolcreatedb", True)
        ),
        "runtime_cannot_create_roles": not bool(
            attributes.get("rolcreaterole", True)
        ),
        "runtime_cannot_replicate": not bool(
            attributes.get("rolreplication", True)
        ),
        "capability_role_exists": bool(attributes.get("capability_exists", False)),
        "capability_role_no_login": not bool(
            attributes.get("capability_login", True)
        ),
        "capability_role_not_superuser": not bool(
            attributes.get("capability_super", True)
        ),
        "capability_role_cannot_create_databases": not bool(
            attributes.get("capability_createdb", True)
        ),
        "capability_role_cannot_create_roles": not bool(
            attributes.get("capability_createrole", True)
        ),
        "capability_role_cannot_replicate": not bool(
            attributes.get("capability_replication", True)
        ),
        "capability_role_not_bypassrls": not bool(
            attributes.get("capability_bypass", True)
        ),
        "runtime_is_capability_member": bool(
            attributes.get("capability_member", False)
        ),
        "runtime_inherits_capability": bool(
            attributes.get("capability_usage", False)
        ),
        "runtime_owns_no_application_objects": not bool(
            attributes.get("runtime_owns_application_object", True)
        ),
        "capability_owns_no_application_objects": not bool(
            attributes.get("capability_owns_application_object", True)
        ),
        "runtime_cannot_assume_application_owner": not bool(
            attributes.get("runtime_can_assume_application_owner", True)
        ),
        "capability_cannot_assume_application_owner": not bool(
            attributes.get("capability_can_assume_application_owner", True)
        ),
    }
    tenant_isolation = await tenant_isolation_posture_status(db)
    checks["tenant_isolation_enforced"] = bool(tenant_isolation["enforced"])
    return {
        "backend": backend,
        "enforced": all(checks.values()),
        "role": attributes.get("rolname"),
        "checks": checks,
        "attributes": attributes,
        "tenant_isolation": tenant_isolation,
    }


def failed_database_role_checks(status: dict[str, Any]) -> list[str]:
    """Return stable, non-secret failure identifiers for logs/readiness."""

    checks = status.get("checks") or {}
    return sorted(name for name, passed in checks.items() if not passed)


__all__ = ["database_role_posture_status", "failed_database_role_checks"]
