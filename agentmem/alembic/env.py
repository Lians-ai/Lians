"""
Alembic environment — async PostgreSQL via asyncpg.

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
from pathlib import Path
from logging.config import fileConfig

from sqlalchemy import pool
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import create_async_engine
from alembic import context

# Ensure `src.agentmem` is importable regardless of working directory.
# prepend_sys_path = . in alembic.ini handles the common case (running from
# agentmem/), but adding it explicitly here too makes env.py importable from
# other tools (pytest-alembic, CI scripts, etc.).
_pkg_root = str(Path(__file__).resolve().parents[1])
if _pkg_root not in sys.path:
    sys.path.insert(0, _pkg_root)

# Import all model classes — this registers them with Base.metadata so that
# autogenerate can diff the live DB against the current model definitions.
from src.agentmem.models import Base  # noqa: E402
from src.agentmem.config import get_settings  # noqa: E402
from src.agentmem.db import parse_db_url  # noqa: E402

config = context.config
target_metadata = Base.metadata

if config.config_file_name is not None:
    fileConfig(config.config_file_name)


def _db_url() -> str:
    """Read from the app config so credentials are never in alembic.ini."""
    return get_settings().database_url


def run_migrations_offline() -> None:
    """Emit SQL to stdout — useful for reviewing DDL before applying."""
    context.configure(
        url=_db_url(),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        compare_type=True,
        compare_server_default=True,
    )
    with context.begin_transaction():
        context.run_migrations()


def _do_run_migrations(connection: Connection) -> None:
    context.configure(
        connection=connection,
        target_metadata=target_metadata,
        compare_type=True,
        compare_server_default=True,
    )
    with context.begin_transaction():
        context.run_migrations()


async def _run_async_migrations() -> None:
    url, connect_args = parse_db_url(_db_url())
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
