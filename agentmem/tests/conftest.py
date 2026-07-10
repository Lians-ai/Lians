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

from src.lians.db import Base
from src.lians.config import get_settings, Settings
import src.lians.kms as _kms

# ---------------------------------------------------------------------------
# Determinism guard: a developer's local `agentmem/.env` (e.g. a Docker-stack
# env with a real MASTER_ENCRYPTION_KEY, cache/rate-limit/WORM settings) must
# never leak into the test run — it changes crypto, caching, and audit-chain
# behavior, so the suite fails on machines where it passes everywhere else.
# Point pydantic-settings at a nonexistent env file and scrub the same
# variables from the process environment before any Settings() is built.
# Tests that need specific values set them explicitly (monkeypatch/fixtures).
# ---------------------------------------------------------------------------
Settings.model_config["env_file"] = "__lians_tests_ignore_dotenv__"
for _var in (
    "MASTER_ENCRYPTION_KEY", "KMS_PROVIDER", "EMBEDDING_PROVIDER",
    "RATE_LIMIT_PER_MINUTE", "RECALL_CACHE_ENABLED", "WORM_MODE",
    "ADMISSION_MODE", "SIEM_URL", "AIRGAP_MODE", "DATABASE_URL", "REDIS_URL",
):
    os.environ.pop(_var, None)
get_settings.cache_clear()

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

    # Bring up only the postgres service (idempotent if already running).
    # A half-started Docker daemon can hang any of these calls — treat every
    # failure (including TimeoutExpired) as "no Docker" and skip provisioning
    # rather than crashing collection with an INTERNALERROR.
    try:
        subprocess.run(
            ["docker", "compose", "-f", str(compose_file), "up", "-d", "postgres"],
            capture_output=True,
            timeout=60,
            cwd=str(_COMPOSE_DIR),
        )
    except Exception:
        return

    # Wait up to 30 s for Postgres to accept connections
    deadline = time.monotonic() + 30
    while time.monotonic() < deadline:
        try:
            r = subprocess.run(
                ["docker", "compose", "-f", str(compose_file),
                 "exec", "-T", "postgres", "pg_isready", "-U", "agentmem"],
                capture_output=True,
                timeout=5,
                cwd=str(_COMPOSE_DIR),
            )
        except Exception:
            return
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
    # LocalLiansClient (used by eval-harness tests) setdefaults this to "false"
    # in os.environ; pin it so later tests see the server default regardless of
    # test order.
    monkeypatch.setenv("RECALL_CACHE_ENABLED", "true")
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
    from src.lians.models import Base as AppBase
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
