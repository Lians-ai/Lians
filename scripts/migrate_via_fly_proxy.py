"""Run Alembic through a local Fly proxy without exposing migration credentials.

The production application uses a least-privilege role. DDL credentials live
only in the protected GitHub environment and are rewritten to the loopback
proxy for this one process.
"""
from __future__ import annotations

import argparse
import os
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit


def _proxied_url(source: str, host: str, port: int) -> str:
    parsed = urlsplit(source)
    if parsed.scheme not in {"postgresql", "postgres", "postgresql+asyncpg"}:
        raise ValueError("Migration URL must be PostgreSQL")
    if not parsed.username or not parsed.hostname or not parsed.path.strip("/"):
        raise ValueError("Migration URL is incomplete")
    userinfo = parsed.username
    if parsed.password is not None:
        userinfo = f"{userinfo}:{parsed.password}"
    netloc = f"{userinfo}@{host}:{port}"
    return urlunsplit((parsed.scheme, netloc, parsed.path, parsed.query, ""))


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=15433)
    args = parser.parse_args()

    source = os.environ.pop("MIGRATION_DATABASE_URL", "")
    if not source:
        raise SystemExit("MIGRATION_DATABASE_URL is required")
    os.environ["DATABASE_URL"] = _proxied_url(source, args.host, args.port)

    repo_root = Path(__file__).resolve().parents[1]
    agentmem = repo_root / "agentmem"
    os.chdir(agentmem)

    from alembic import command
    from alembic.config import Config

    command.upgrade(Config(str(agentmem / "alembic.ini")), "head")
    print("Production database migration completed.")


if __name__ == "__main__":
    main()
