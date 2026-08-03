"""Fail-closed transport and role checks for externally run migrations."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy.pool import NullPool

from .config import get_settings
from .connection_security import validate_production_data_transports
from .db import parse_db_url


async def _assert_migration_role() -> None:
    settings = get_settings()
    url, connect_args = parse_db_url(settings.database_url)
    if not url.startswith("postgresql+asyncpg://"):
        raise RuntimeError("migration preflight requires PostgreSQL")
    connect_args = {
        **connect_args,
        "server_settings": {
            "statement_timeout": str(settings.migration_statement_timeout_ms),
            "lock_timeout": str(settings.migration_lock_timeout_ms),
            "idle_in_transaction_session_timeout": str(
                settings.migration_idle_transaction_timeout_ms
            ),
            "application_name": "lians-migration-preflight",
        },
    }
    engine = create_async_engine(url, connect_args=connect_args, poolclass=NullPool)
    try:
        async with engine.connect() as connection:
            posture = (
                await connection.execute(
                    text(
                        """
                        SELECT
                            migration.rolsuper AS migration_superuser,
                            migration.rolbypassrls AS migration_bypasses_rls,
                            COALESCE(
                                pg_has_role(current_user, runtime.rolname, 'MEMBER'),
                                false
                            ) AS migration_has_runtime_capability,
                            runtime.rolname IS NOT NULL AS runtime_role_exists,
                            COALESCE(runtime.rolcanlogin, true) AS runtime_role_can_login,
                            COALESCE(runtime.rolsuper, true) AS runtime_role_superuser,
                            COALESCE(runtime.rolbypassrls, true)
                                AS runtime_role_bypasses_rls,
                            (
                                EXISTS (
                                    SELECT 1 FROM pg_database database
                                    WHERE database.datname = current_database()
                                      AND database.datdba = runtime.oid
                                )
                                OR EXISTS (
                                    SELECT 1
                                    FROM pg_namespace namespace
                                    WHERE namespace.nspname NOT IN (
                                        'pg_catalog', 'information_schema'
                                    )
                                      AND namespace.nspname !~ '^pg_toast'
                                      AND namespace.nspowner = runtime.oid
                                )
                                OR EXISTS (
                                    SELECT 1
                                    FROM pg_class relation
                                    JOIN pg_namespace namespace
                                      ON namespace.oid = relation.relnamespace
                                    WHERE namespace.nspname NOT IN (
                                        'pg_catalog', 'information_schema'
                                    )
                                      AND namespace.nspname !~ '^pg_toast'
                                      AND relation.relowner = runtime.oid
                                )
                                OR EXISTS (
                                    SELECT 1
                                    FROM pg_proc procedure
                                    JOIN pg_namespace namespace
                                      ON namespace.oid = procedure.pronamespace
                                    WHERE namespace.nspname NOT IN (
                                        'pg_catalog', 'information_schema'
                                    )
                                      AND namespace.nspname !~ '^pg_toast'
                                      AND procedure.proowner = runtime.oid
                                )
                                OR EXISTS (
                                    SELECT 1
                                    FROM pg_type data_type
                                    JOIN pg_namespace namespace
                                      ON namespace.oid = data_type.typnamespace
                                    WHERE namespace.nspname NOT IN (
                                        'pg_catalog', 'information_schema'
                                    )
                                      AND namespace.nspname !~ '^pg_toast'
                                      AND data_type.typowner = runtime.oid
                                )
                            ) AS runtime_role_owns_application_object
                        FROM pg_roles migration
                        LEFT JOIN pg_roles runtime
                          ON runtime.rolname = 'lians_runtime'
                        WHERE migration.rolname = current_user
                        """
                    )
                )
            ).mappings().one_or_none()
            unsafe = (
                posture is None
                or posture["migration_superuser"]
                or posture["migration_bypasses_rls"]
                or posture["migration_has_runtime_capability"]
                or not posture["runtime_role_exists"]
                or posture["runtime_role_can_login"]
                or posture["runtime_role_superuser"]
                or posture["runtime_role_bypasses_rls"]
                or posture["runtime_role_owns_application_object"]
            )
            if unsafe:
                raise RuntimeError("migration and runtime database roles are not separated")
    finally:
        await engine.dispose()


def _assert_transport() -> None:
    settings = get_settings()
    failures = validate_production_data_transports(
        SimpleNamespace(
            database_url=settings.database_url,
            redis_url="rediss://migration-preflight.invalid:6379/0",
            production_allow_local_data_service_sockets=False,
            database_pool_size=1,
            database_max_overflow=0,
            database_pool_timeout_seconds=5,
        )
    )
    if failures:
        raise RuntimeError("migration database transport is not production-safe")


def main() -> None:
    """Validate without printing credentials, usernames, hosts, or role details."""
    try:
        _assert_transport()
        asyncio.run(_assert_migration_role())
    except RuntimeError as exc:
        raise SystemExit(str(exc)) from exc
    print("migration transport and database-role separation verified")


if __name__ == "__main__":
    main()
