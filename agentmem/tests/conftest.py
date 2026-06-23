"""
Test fixtures: in-memory SQLite-equivalent via async SQLAlchemy.
We use an in-process PG via pytest-postgresql or a real local PG for integration tests.
For unit tests we mock the DB session with an in-memory approach.
"""
import os
import subprocess
import time
from pathlib import Path

import pytest
import pytest_asyncio
from unittest.mock import AsyncMock, MagicMock
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession
from sqlalchemy.pool import StaticPool

from src.lian.db import Base
from src.lian.config import get_settings, Settings
import src.lian.kms as _kms

_COMPOSE_DIR = Path(__file__).parent.parent
_DB_URL = "postgresql+asyncpg://agentmem:agentmem@localhost:5432/agentmem"


def pytest_configure(config):
    """
    Auto-provision a pgvector Postgres container when Docker is available so
    test_pgvector.py tests run without any manual setup.  Called before test
    collection, so the module-level pytestmark skip-condition in test_pgvector.py
    sees TEST_DATABASE_URL already set.
    """
    if os.environ.get("TEST_DATABASE_URL"):
        return  # already provided externally

    # Fast-fail: check Docker daemon reachability (3-second timeout)
    try:
        r = subprocess.run(["docker", "info"], capture_output=True, timeout=3)
        if r.returncode != 0:
            return
    except Exception:
        return

    compose_file = _COMPOSE_DIR / "docker-compose.yml"
    if not compose_file.exists():
        return

    # Bring up only the postgres service (idempotent if already running)
    subprocess.run(
        ["docker", "compose", "-f", str(compose_file), "up", "-d", "postgres"],
        capture_output=True,
        timeout=60,
        cwd=str(_COMPOSE_DIR),
    )

    # Wait up to 30 s for Postgres to accept connections
    deadline = time.monotonic() + 30
    while time.monotonic() < deadline:
        r = subprocess.run(
            ["docker", "compose", "-f", str(compose_file),
             "exec", "-T", "postgres", "pg_isready", "-U", "agentmem"],
            capture_output=True,
            timeout=5,
            cwd=str(_COMPOSE_DIR),
        )
        if r.returncode == 0:
            break
        time.sleep(1)
    else:
        return  # timed out â€” skip gracefully

    # Run migrations (no-op when already at head)
    env = {**os.environ, "DATABASE_URL": _DB_URL}
    subprocess.run(
        ["python", "-m", "alembic", "upgrade", "head"],
        capture_output=True,
        timeout=60,
        cwd=str(_COMPOSE_DIR),
        env=env,
    )

    os.environ["TEST_DATABASE_URL"] = _DB_URL


# Override settings for tests
@pytest.fixture(autouse=True)
def test_settings(monkeypatch):
    monkeypatch.setenv("EMBEDDING_PROVIDER", "local")
    monkeypatch.setenv("MASTER_ENCRYPTION_KEY", "")
    monkeypatch.setenv("KMS_PROVIDER", "env")
    monkeypatch.setenv("AGENTMEM_ALLOW_UNENCRYPTED", "true")
    monkeypatch.setenv("RLS_BARRIERS_ENABLED", "false")  # SQLite has no RLS
    get_settings.cache_clear()
    _kms._reset_cache()
    yield
    get_settings.cache_clear()
    _kms._reset_cache()


@pytest_asyncio.fixture
async def db():
    """SQLite in-memory async session for unit tests (no pgvector)."""
    from sqlalchemy import event as sa_event, text

    engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )

    # Drop PG-only indexes before table creation so SQLite doesn't choke
    from src.lian.models import Base as AppBase
    import sqlalchemy as sa
    pg_indexes = [
        idx for table in AppBase.metadata.tables.values()
        for idx in table.indexes
        if idx.dialect_kwargs.get("postgresql_using") is not None
    ]
    for idx in pg_indexes:
        idx.table.indexes.discard(idx)

    async with engine.begin() as conn:
        await conn.run_sync(AppBase.metadata.create_all)

    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    async with session_factory() as session:
        yield session

    await engine.dispose()
