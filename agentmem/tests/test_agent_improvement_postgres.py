"""PostgreSQL-only contracts for the governed agent-improvement plane."""

from __future__ import annotations

import os
from uuid import uuid4

import pytest
import pytest_asyncio
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from lians import (
    improvement_models,
    learning_models,
    optimization_models,
    release_models,
    runtime_models,
)

TEST_DB_URL = os.environ.get("TEST_DATABASE_URL", "")
PG_AVAILABLE = bool(TEST_DB_URL and "postgresql" in TEST_DB_URL)

pytestmark = pytest.mark.skipif(
    not PG_AVAILABLE,
    reason="TEST_DATABASE_URL is not a PostgreSQL URL",
)

_MODULES = (
    improvement_models,
    learning_models,
    optimization_models,
    release_models,
    runtime_models,
)
IMPROVEMENT_TABLES = sorted(
    {
        value.__table__.name
        for module in _MODULES
        for value in vars(module).values()
        if isinstance(value, type) and hasattr(value, "__table__")
    }
)


@pytest_asyncio.fixture
async def pg_engine():
    engine = create_async_engine(TEST_DB_URL, pool_pre_ping=True)
    yield engine
    await engine.dispose()


@pytest_asyncio.fixture
async def pg_factory(pg_engine):
    return async_sessionmaker(pg_engine, expire_on_commit=False, class_=AsyncSession)


@pytest.mark.asyncio
async def test_all_improvement_tables_have_forced_rls_least_privilege_and_triggers(
    pg_factory,
) -> None:
    assert len(IMPROVEMENT_TABLES) == 30
    async with pg_factory() as db:
        revision = (await db.execute(text("SELECT version_num FROM alembic_version"))).scalar_one()
        assert revision == "0064_agent_improvement_plane"

        boundaries = (
            await db.execute(
                text(
                    """
                    SELECT c.relname, c.relrowsecurity, c.relforcerowsecurity,
                           has_table_privilege('lians_runtime', c.oid, 'SELECT'),
                           has_table_privilege('lians_runtime', c.oid, 'INSERT'),
                           has_table_privilege('lians_runtime', c.oid, 'UPDATE'),
                           has_table_privilege('lians_runtime', c.oid, 'DELETE'),
                           has_table_privilege('lians_runtime', c.oid, 'TRUNCATE'),
                           count(t.oid) FILTER (WHERE NOT t.tgisinternal)
                    FROM pg_class c
                    JOIN pg_namespace n ON n.oid = c.relnamespace
                    LEFT JOIN pg_trigger t ON t.tgrelid = c.oid
                    WHERE n.nspname = 'public' AND c.relname = ANY(:tables)
                    GROUP BY c.oid, c.relname, c.relrowsecurity, c.relforcerowsecurity
                    ORDER BY c.relname
                    """
                ),
                {"tables": IMPROVEMENT_TABLES},
            )
        ).all()

        role = (
            await db.execute(
                text(
                    "SELECT rolcanlogin, rolsuper, rolbypassrls "
                    "FROM pg_roles WHERE rolname = 'lians_runtime'"
                )
            )
        ).one()
        candidate_unique = (
            await db.execute(
                text(
                    "SELECT count(*) FROM pg_constraint "
                    "WHERE conname = 'uq_optimization_candidate_id_namespace'"
                )
            )
        ).scalar_one()

    assert len(boundaries) == len(IMPROVEMENT_TABLES)
    for row in boundaries:
        assert row[1:8] == (True, True, True, True, False, False, False)
        assert row[8] == 2
    assert role == (False, False, False)
    assert candidate_unique == 1


@pytest.mark.asyncio
async def test_improvement_rls_filters_barriers_and_database_rejects_mutation(
    pg_factory,
) -> None:
    namespace = f"improvement-pg-{uuid4().hex}"
    group_a = f"desk-a-{uuid4().hex[:8]}"
    group_b = f"desk-b-{uuid4().hex[:8]}"
    id_a, id_b = uuid4(), uuid4()

    async with pg_factory() as db:
        await db.execute(
            text(
                """
                INSERT INTO agent_definitions
                    (id, namespace, barrier_group, barrier_scope, key, name,
                     metadata, definition_hash, created_by_principal_ref, created_at)
                VALUES
                    (:id_a, :namespace, :group_a, :scope_a, 'agent-a', 'Agent A',
                     '{}'::json, :hash_a, 'postgres-test', now()),
                    (:id_b, :namespace, :group_b, :scope_b, 'agent-b', 'Agent B',
                     '{}'::json, :hash_b, 'postgres-test', now())
                """
            ),
            {
                "id_a": id_a,
                "id_b": id_b,
                "namespace": namespace,
                "group_a": group_a,
                "group_b": group_b,
                "scope_a": "a" * 64,
                "scope_b": "b" * 64,
                "hash_a": "c" * 64,
                "hash_b": "d" * 64,
            },
        )
        await db.execute(text("SET LOCAL ROLE lians_runtime"))
        await db.execute(
            text("SELECT set_config('app.current_namespace', :namespace, true)"),
            {"namespace": namespace},
        )
        await db.execute(
            text("SELECT set_config('agentmem.barrier_group', :barrier, true)"),
            {"barrier": group_a},
        )
        visible = {
            row[0]
            for row in (
                await db.execute(
                    text(
                        "SELECT key FROM agent_definitions "
                        "WHERE namespace = :namespace ORDER BY key"
                    ),
                    {"namespace": namespace},
                )
            ).all()
        }
        await db.rollback()

    assert visible == {"agent-a"}

    async with pg_factory() as db:
        await db.execute(
            text(
                """
                INSERT INTO agent_definitions
                    (id, namespace, barrier_group, barrier_scope, key, name,
                     metadata, definition_hash, created_by_principal_ref, created_at)
                VALUES
                    (:id, :namespace, NULL, :scope, 'immutable', 'Immutable',
                     '{}'::json, :definition_hash, 'postgres-test', now())
                """
            ),
            {
                "id": uuid4(),
                "namespace": namespace,
                "scope": "e" * 64,
                "definition_hash": "f" * 64,
            },
        )
        with pytest.raises(DBAPIError, match="append-only"):
            await db.execute(
                text(
                    "UPDATE agent_definitions SET name = 'Changed' "
                    "WHERE namespace = :namespace AND key = 'immutable'"
                ),
                {"namespace": namespace},
            )
        await db.rollback()
