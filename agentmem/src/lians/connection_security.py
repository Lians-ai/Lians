"""Fail-closed production transport validation for stateful dependencies."""

from __future__ import annotations

from pathlib import PurePosixPath
from typing import Any
from urllib.parse import parse_qs, urlsplit


def _one_query_value(query: dict[str, list[str]], *names: str) -> str | None:
    values = [value for name in names for value in query.get(name, [])]
    if len(values) != 1:
        return None
    return values[0].strip().casefold()


def _absolute_socket(value: str | None) -> bool:
    return bool(value and PurePosixPath(value).is_absolute())


def validate_production_data_transports(settings: Any) -> list[str]:
    """Return sanitized configuration failures without echoing credential URLs."""
    failures: list[str] = []
    allow_sockets = bool(settings.production_allow_local_data_service_sockets)

    try:
        database = urlsplit(str(settings.database_url))
        database_query = parse_qs(database.query, keep_blank_values=True)
        database_scheme = database.scheme.casefold()
        database_socket = database_query.get("host", [None])[0]
        database_is_socket = (
            database.hostname is None
            and (not database.netloc or database.netloc.endswith("@"))
            and _absolute_socket(database_socket)
        )
        if database_scheme not in {
            "postgres",
            "postgresql",
            "postgresql+asyncpg",
        }:
            failures.append("DATABASE_URL must use PostgreSQL with the asyncpg driver")
        elif database_is_socket:
            if not allow_sockets:
                failures.append(
                    "local PostgreSQL sockets require "
                    "PRODUCTION_ALLOW_LOCAL_DATA_SERVICE_SOCKETS=true"
                )
        else:
            ssl_values = database_query.get("sslmode", [])
            if len(ssl_values) != 1 or ssl_values[0].strip().casefold() != "verify-full":
                failures.append(
                    "network PostgreSQL requires exactly sslmode=verify-full"
                )
            if "ssl" in database_query:
                failures.append(
                    "network PostgreSQL must not use the ambiguous ssl query parameter"
                )
            if not database.hostname:
                failures.append("network PostgreSQL requires a certificate hostname")
    except (TypeError, ValueError):
        failures.append("DATABASE_URL is not a valid PostgreSQL connection URL")

    try:
        redis = urlsplit(str(settings.redis_url))
        redis_query = parse_qs(redis.query, keep_blank_values=True)
        redis_scheme = redis.scheme.casefold()
        redis_is_socket = redis_scheme == "unix" and _absolute_socket(redis.path)
        if redis_is_socket:
            if not allow_sockets:
                failures.append(
                    "local Redis sockets require "
                    "PRODUCTION_ALLOW_LOCAL_DATA_SERVICE_SOCKETS=true"
                )
        elif redis_scheme != "rediss":
            failures.append("network Redis requires rediss:// with TLS")
        else:
            cert_reqs = _one_query_value(redis_query, "ssl_cert_reqs")
            if cert_reqs is not None and cert_reqs not in {
                "required",
                "cert_required",
                "2",
            }:
                failures.append("Redis TLS certificate verification cannot be disabled")
            hostname_check = _one_query_value(redis_query, "ssl_check_hostname")
            if hostname_check is not None and hostname_check not in {"true", "1", "yes"}:
                failures.append("Redis TLS hostname verification cannot be disabled")
            if not redis.hostname:
                failures.append("network Redis requires a certificate hostname")
    except (TypeError, ValueError):
        failures.append("REDIS_URL is not a valid Redis connection URL")

    if (
        type(settings.database_pool_size) is not int
        or not 1 <= settings.database_pool_size <= 200
    ):
        failures.append("DATABASE_POOL_SIZE must be an integer between 1 and 200")
    if (
        type(settings.database_max_overflow) is not int
        or not 0 <= settings.database_max_overflow <= 400
    ):
        failures.append("DATABASE_MAX_OVERFLOW must be an integer between 0 and 400")
    if (
        type(settings.database_pool_size) is int
        and type(settings.database_max_overflow) is int
        and settings.database_pool_size + settings.database_max_overflow > 500
    ):
        failures.append(
            "DATABASE_POOL_SIZE plus DATABASE_MAX_OVERFLOW must not exceed 500"
        )
    try:
        pool_timeout = float(settings.database_pool_timeout_seconds)
    except (TypeError, ValueError):
        pool_timeout = 0
    if not 0.1 <= pool_timeout <= 300:
        failures.append("DATABASE_POOL_TIMEOUT_SECONDS must be between 0.1 and 300")
    return failures
