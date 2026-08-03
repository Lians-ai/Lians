"""Exact database-schema contract shared by readiness and release tooling."""

from __future__ import annotations

import asyncio
from collections.abc import Iterable

from alembic.config import Config
from alembic.script import ScriptDirectory
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncSession

from .db import engine
from .tenant_isolation_posture import tenant_isolation_posture_status
from .version import EXPECTED_ALEMBIC_HEAD


class SchemaContractError(RuntimeError):
    """The image, migration graph, or database does not match the release contract."""


def _normalized_heads(values: Iterable[object]) -> tuple[str, ...]:
    return tuple(sorted({str(value).strip() for value in values if str(value).strip()}))


def assert_expected_heads(values: Iterable[object], *, source: str) -> None:
    """Require exactly the immutable head declared by this release image."""
    actual = _normalized_heads(values)
    expected = (EXPECTED_ALEMBIC_HEAD,)
    if actual != expected:
        raise SchemaContractError(f"{source} does not match the release schema contract")


async def database_heads(db: AsyncSession | AsyncConnection) -> tuple[str, ...]:
    """Read every live Alembic head; multiple rows indicate an unmerged graph."""
    result = await db.execute(text("SELECT version_num FROM alembic_version"))
    return _normalized_heads(result.scalars().all())


async def assert_database_schema(db: AsyncSession | AsyncConnection) -> None:
    assert_expected_heads(await database_heads(db), source="database")
    tenant_posture = await tenant_isolation_posture_status(db)
    if (
        tenant_posture["backend"] == "postgresql"
        and not tenant_posture["enforced"]
    ):
        raise SchemaContractError(
            "database tenant-isolation posture does not match the release contract"
        )


def assert_packaged_schema(config_path: str = "alembic.ini") -> None:
    """Verify the migration files packaged beside the wheel match the constant."""
    scripts = ScriptDirectory.from_config(Config(config_path))
    assert_expected_heads(scripts.get_heads(), source="packaged migration graph")


async def _check_runtime_database() -> None:
    assert_packaged_schema()
    async with engine.connect() as connection:
        await assert_database_schema(connection)


def main() -> None:
    """Fail closed for deploy preflight jobs; emit no database identifiers."""
    try:
        asyncio.run(_check_runtime_database())
    except SchemaContractError as exc:
        raise SystemExit(str(exc)) from exc
    print("database and packaged migrations match the release schema contract")


if __name__ == "__main__":
    main()
