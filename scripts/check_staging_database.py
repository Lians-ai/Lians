"""Read-only health and Decision Envelope integrity check for staging.

The database URL is read from ``DATABASE_URL``. A Fly proxy host and port may
be supplied without constructing or printing a second URL containing the
database password.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
from dataclasses import dataclass, field
from urllib.parse import parse_qs, unquote, urlparse

import asyncpg


@dataclass(frozen=True)
class ConnectionSettings:
    host: str
    port: int
    user: str
    password: str = field(repr=False)
    database: str
    ssl: bool


def parse_connection_settings(
    database_url: str,
    *,
    host_override: str | None = None,
    port_override: int | None = None,
) -> ConnectionSettings:
    parsed = urlparse(database_url)
    if parsed.scheme not in {"postgres", "postgresql", "postgresql+asyncpg"}:
        raise ValueError("DATABASE_URL must be a PostgreSQL URL")
    if not parsed.hostname or not parsed.username or parsed.password is None:
        raise ValueError("DATABASE_URL must include host, username, and password")
    database = parsed.path.lstrip("/")
    if not database:
        raise ValueError("DATABASE_URL must include a database name")

    query = parse_qs(parsed.query)
    sslmode = query.get("sslmode", query.get("ssl", ["prefer"]))[0].lower()
    ssl_enabled = sslmode not in {"disable", "false", "0", "no"}
    if host_override is not None:
        # flyctl proxy is a local WireGuard tunnel. PostgreSQL TLS is not
        # required between the runner and the local proxy endpoint.
        ssl_enabled = False

    return ConnectionSettings(
        host=host_override or parsed.hostname,
        port=port_override or parsed.port or 5432,
        user=unquote(parsed.username),
        password=unquote(parsed.password),
        database=database,
        ssl=ssl_enabled,
    )


async def check_staging(settings: ConnectionSettings, expected_revision: str) -> dict[str, object]:
    connection = await asyncpg.connect(
        host=settings.host,
        port=settings.port,
        user=settings.user,
        password=settings.password,
        database=settings.database,
        ssl=settings.ssl,
        timeout=15,
        command_timeout=30,
    )
    try:
        await connection.execute(
            "SELECT set_config('app.current_namespace', '__admin__', false)"
        )
        await connection.execute(
            "SELECT set_config('agentmem.barrier_group', '', false)"
        )
        result = await connection.fetchrow(
            """
            SELECT
              current_database() AS database_name,
              current_setting('server_version_num')::integer / 10000 AS postgres_major,
              (SELECT version_num FROM alembic_version) AS revision,
              (SELECT count(*) FROM decision_records) AS decision_records,
              (SELECT count(*) FROM decision_envelopes) AS decision_envelopes,
              (SELECT count(*) FROM decision_evidence_links) AS evidence_links,
              (
                SELECT count(*)
                FROM decision_records
                WHERE envelope_id IS NULL
              ) AS missing_envelopes,
              (
                SELECT count(*)
                FROM decision_records AS record
                LEFT JOIN decision_envelopes AS envelope
                  ON envelope.id = record.envelope_id
                WHERE envelope.id IS NULL
              ) AS orphaned_decisions,
              (
                SELECT count(*)
                FROM decision_evidence_links AS evidence
                LEFT JOIN decision_envelopes AS envelope
                  ON envelope.id = evidence.envelope_id
                WHERE envelope.id IS NULL
              ) AS orphaned_evidence
            """
        )
        if result is None:
            raise RuntimeError("Staging integrity query returned no result")
        summary = dict(result)
    finally:
        await connection.close()

    if summary["postgres_major"] != 17:
        raise RuntimeError(
            f"Expected PostgreSQL 17, found major version {summary['postgres_major']}"
        )
    if summary["revision"] != expected_revision:
        raise RuntimeError(
            f"Expected Alembic revision {expected_revision}, found {summary['revision']}"
        )
    for integrity_field in (
        "missing_envelopes",
        "orphaned_decisions",
        "orphaned_evidence",
    ):
        if summary[integrity_field] != 0:
            raise RuntimeError(
                "Staging integrity check failed: "
                f"{integrity_field}={summary[integrity_field]}"
            )

    return summary


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--host", help="Local Fly proxy host override")
    parser.add_argument("--port", type=int, help="Local Fly proxy port override")
    parser.add_argument(
        "--expected-revision",
        default="0028_decision_envelopes",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    database_url = os.environ.get("DATABASE_URL")
    if not database_url:
        raise SystemExit("DATABASE_URL is required")
    settings = parse_connection_settings(
        database_url,
        host_override=args.host,
        port_override=args.port,
    )
    summary = asyncio.run(check_staging(settings, args.expected_revision))
    print(json.dumps(summary, sort_keys=True))


if __name__ == "__main__":
    main()
