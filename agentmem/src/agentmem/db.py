from urllib.parse import urlparse, parse_qs, urlencode, urlunparse

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
