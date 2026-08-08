"""
Alembic environment â€” async PostgreSQL via asyncpg.

Run migrations:
    cd agentmem
    alembic upgrade head

Generate a new migration after changing models.py:
    alembic revision --autogenerate -m "describe the change"

Preview SQL without touching the DB:
    alembic upgrade head --sql
"""
import asyncio
import sys
from logging.config import fileConfig
from pathlib import Path

from alembic import context
from sqlalchemy import JSON, pool
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import create_async_engine

# Ensure the production wheel's canonical ``lians`` package identity is
# importable regardless of the migration runner's working directory.
_pkg_root = str(Path(__file__).resolve().parents[1] / "src")
if _pkg_root not in sys.path:
    sys.path.insert(0, _pkg_root)

# Import all model classes â€” this registers them with Base.metadata so that
# autogenerate can diff the live DB against the current model definitions.
from lians import (  # noqa: E402, F401
    control_models,
    enterprise_models,
    evidence_models,
    governance_models,
    identity_models,
    improvement_models,
    integration_models,
    learning_models,
    optimization_models,
    recorder_models,
    release_models,
    runtime_models,
    subject_erasure_models,
)
from lians.config import get_settings  # noqa: E402
from lians.db import parse_db_url  # noqa: E402
from lians.models import Base  # noqa: E402

config = context.config
target_metadata = Base.metadata

if config.config_file_name is not None:
    fileConfig(config.config_file_name)


def _compare_server_default(
    _context: object,
    _inspected_column: object,
    _metadata_column: object,
    _inspected_default: object | None,
    _metadata_default: object | None,
    _rendered_metadata_default: object | None,
) -> bool:
    """Treat server defaults as migration-owned release-schema details.

    Model metadata intentionally carries Python/SQLite fixture defaults that
    are not a lossless description of PostgreSQL's production defaults.  In
    particular, PostgreSQL has no equality operator for ``json``, so the stock
    comparator can crash on a valid schema.  The curated migration contract
    validates production defaults; autogenerate remains responsible for
    shared columns, nullability, types, keys, and model-owned indexes.
    """

    return False


def _compare_type(
    _context: object,
    _inspected_column: object,
    _metadata_column: object,
    inspected_type: object,
    metadata_type: object,
) -> bool | None:
    """Accept the generic JSON model type backed by JSONB in PostgreSQL."""

    if isinstance(inspected_type, (JSON, JSONB)) and isinstance(
        metadata_type, (JSON, JSONB)
    ):
        return False
    return None


def _include_object(
    _object: object,
    _name: str | None,
    _type: str,
    reflected: bool,
    compare_to: object | None,
) -> bool:
    """Exclude database-native objects deliberately owned by migrations."""

    return not (reflected and compare_to is None)


def _db_url() -> str:
    """Read from the app config so credentials are never in alembic.ini."""
    return get_settings().database_url


def run_migrations_offline() -> None:
    """Emit SQL to stdout â€” useful for reviewing DDL before applying."""
    context.configure(
        url=_db_url(),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        compare_type=_compare_type,
        compare_server_default=_compare_server_default,
        include_object=_include_object,
        transaction_per_migration=True,
    )
    with context.begin_transaction():
        context.run_migrations()


def _do_run_migrations(connection: Connection) -> None:
    context.configure(
        connection=connection,
        target_metadata=target_metadata,
        compare_type=_compare_type,
        compare_server_default=_compare_server_default,
        include_object=_include_object,
        transaction_per_migration=True,
    )
    with context.begin_transaction():
        context.run_migrations()


async def _run_async_migrations() -> None:
    url, connect_args = parse_db_url(_db_url())
    settings = get_settings()
    if url.startswith("postgresql+asyncpg://"):
        connect_args = {
            **connect_args,
            "server_settings": {
                "statement_timeout": str(settings.migration_statement_timeout_ms),
                "lock_timeout": str(settings.migration_lock_timeout_ms),
                "idle_in_transaction_session_timeout": str(
                    settings.migration_idle_transaction_timeout_ms
                ),
                "application_name": "lians-migrator",
            },
        }
    connectable = create_async_engine(
        url,
        connect_args=connect_args,
        poolclass=pool.NullPool,
    )
    async with connectable.connect() as connection:
        await connection.run_sync(_do_run_migrations)
    await connectable.dispose()


def run_migrations_online() -> None:
    asyncio.run(_run_async_migrations())


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
