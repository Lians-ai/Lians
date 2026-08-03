"""Fail-closed production transport configuration tests."""

from types import SimpleNamespace

import lians.db as db_module
from lians.connection_security import validate_production_data_transports
from lians.db import parse_db_url


def _settings(**overrides):
    values = {
        "database_url": (
            "postgresql+asyncpg://app:secret@db.internal/lians"
            "?sslmode=verify-full"
        ),
        "redis_url": "rediss://cache.internal:6380/0",
        "production_allow_local_data_service_sockets": False,
        "database_pool_size": 10,
        "database_max_overflow": 5,
        "database_pool_timeout_seconds": 30.0,
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def test_verified_network_transports_pass():
    assert validate_production_data_transports(_settings()) == []


def test_plaintext_and_nonverifying_network_modes_fail():
    failures = validate_production_data_transports(
        _settings(
            database_url="postgresql+asyncpg://app:secret@db.internal/lians?sslmode=require",
            redis_url="redis://cache.internal:6379/0",
        )
    )

    assert any("verify-full" in failure for failure in failures)
    assert any("rediss" in failure for failure in failures)


def test_tls_downgrade_query_parameters_fail():
    failures = validate_production_data_transports(
        _settings(
            redis_url=(
                "rediss://cache.internal:6380/0?ssl_cert_reqs=none"
                "&ssl_check_hostname=false"
            )
        )
    )

    assert any("certificate verification" in failure for failure in failures)
    assert any("hostname verification" in failure for failure in failures)


def test_ambiguous_asyncpg_ssl_alias_is_rejected():
    failures = validate_production_data_transports(
        _settings(
            database_url=(
                "postgresql+asyncpg://app:secret@db.internal/lians?ssl=verify-full"
            )
        )
    )

    assert any("sslmode=verify-full" in failure for failure in failures)
    assert any("ambiguous ssl" in failure for failure in failures)


def test_absolute_local_sockets_require_explicit_exception():
    socket_settings = _settings(
        database_url="postgresql+asyncpg:///lians?host=/run/postgresql",
        redis_url="unix:///run/redis/redis.sock",
    )
    assert len(validate_production_data_transports(socket_settings)) == 2

    socket_settings.production_allow_local_data_service_sockets = True
    assert validate_production_data_transports(socket_settings) == []


def test_pool_bounds_fail_closed():
    failures = validate_production_data_transports(
        _settings(
            database_pool_size=0,
            database_max_overflow=-1,
            database_pool_timeout_seconds=0,
        )
    )

    assert len([failure for failure in failures if "DATABASE_" in failure]) == 3


def test_database_url_parser_preserves_non_postgres_dialects_exactly():
    sqlite_url = "sqlite+aiosqlite:///:memory:"

    assert parse_db_url(sqlite_url) == (sqlite_url, {})


def test_local_engine_omits_postgres_queue_pool_arguments(monkeypatch):
    settings = SimpleNamespace(
        database_url="sqlite+aiosqlite:///:memory:",
        database_pool_size=10,
        database_max_overflow=20,
        database_pool_timeout_seconds=30.0,
        database_statement_timeout_ms=30_000,
        database_lock_timeout_ms=5_000,
        database_idle_transaction_timeout_ms=60_000,
    )
    captured = {}

    def fake_create_async_engine(url, **kwargs):
        captured.update(url=url, **kwargs)
        return object()

    monkeypatch.setattr(db_module, "get_settings", lambda: settings)
    monkeypatch.setattr(db_module, "create_async_engine", fake_create_async_engine)

    db_module._make_engine()

    assert captured == {
        "url": "sqlite+aiosqlite:///:memory:",
        "connect_args": {},
        "pool_pre_ping": True,
    }
