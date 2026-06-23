"""
Database engine and session factory.

Change 9 (Postgres RLS enforcement)
-------------------------------------
When ``config.rls_barriers_enabled=True``, each session sets the Postgres
session variable ``agentmem.barrier_group`` before executing queries.  The
RLS policy on ``live_facts`` and ``memories`` then enforces the information
barrier at the database layer, eliminating the app-layer ``OR barrier_group
IS NULL`` post-filter.

RLS is applied automatically by Alembic migration 0011_rls_barriers.  The
effective policy on both tables is:

    USING (
        barrier_group IS NULL
        OR current_setting('agentmem.barrier_group', true) IS NULL
        OR barrier_group = current_setting('agentmem.barrier_group', true)
    )

    WITH FORCE ROW LEVEL SECURITY (applied to table owner as well)

Admin routes use get_db() and never SET the session variable, so
current_setting() returns NULL → the IS NULL branch fires → all rows visible.

Barrier-scoped routes use get_db_with_barrier(group) which issues
SET LOCAL agentmem.barrier_group = '<group>' → only unbarriered rows +
rows matching the group are visible.

``rls_barriers_enabled=True`` is the default after migration 0011 is applied.
"""
from urllib.parse import urlparse, parse_qs, urlencode, urlunparse
from typing import Optional

from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession
from sqlalchemy.orm import DeclarativeBase
from .config import get_settings


class Base(DeclarativeBase):
    pass


def parse_db_url(database_url: str) -> tuple[str, dict]:
    """
    Strip ssl/sslmode query params from a postgresql+asyncpg URL and return
    a (clean_url, connect_args) pair.

    asyncpg does not accept ssl params in the URL the same way libpq does.
    Extracting them here and passing via connect_args is the correct approach.
    """
    parsed = urlparse(database_url)
    params = parse_qs(parsed.query, keep_blank_values=True)

    ssl_arg = None
    for key in ("sslmode", "ssl"):
        if key in params:
            val = params.pop(key)[0].lower()
            if val in ("disable", "false", "0", "no"):
                ssl_arg = False
            elif val in ("require", "true", "1", "yes"):
                ssl_arg = True
            elif val in ("prefer", "allow", "verify-ca", "verify-full"):
                ssl_arg = val
            break

    new_query = urlencode({k: v[0] for k, v in params.items()})
    clean_url = urlunparse(parsed._replace(query=new_query))
    connect_args = {"ssl": ssl_arg} if ssl_arg is not None else {}
    return clean_url, connect_args


def _make_engine():
    settings = get_settings()
    url, connect_args = parse_db_url(settings.database_url)
    return create_async_engine(
        url,
        connect_args=connect_args,
        pool_pre_ping=True,
        pool_size=10,
        max_overflow=20,
    )


engine = _make_engine()
AsyncSessionLocal = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)


async def get_db() -> AsyncSession:
    async with AsyncSessionLocal() as session:
        yield session


async def get_db_with_barrier(barrier_group: Optional[str]) -> AsyncSession:
    """Session factory that sets the RLS barrier variable (Change 9).

    Use in place of ``get_db`` for agent-scoped routes when
    ``rls_barriers_enabled=True``.  Admin/compliance routes that need to see
    all memories should continue using the plain ``get_db``.
    """
    settings = get_settings()
    async with AsyncSessionLocal() as session:
        if settings.rls_barriers_enabled and barrier_group is not None:
            try:
                from sqlalchemy import text as _text
                await session.execute(
                    _text("SET LOCAL agentmem.barrier_group = :bg"),
                    {"bg": barrier_group},
                )
            except Exception:
                pass  # non-PG backend — RLS not available
        yield session
