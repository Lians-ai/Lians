"""PostgreSQL contracts for bounded ValidMind opaque agent lookup."""

from __future__ import annotations

import os
from uuid import uuid4

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine


TEST_DB_URL = os.environ.get("TEST_DATABASE_URL", "")
requires_postgres = pytest.mark.skipif(
    not (TEST_DB_URL and "postgresql" in TEST_DB_URL),
    reason="TEST_DATABASE_URL is not a PostgreSQL URL",
)


@requires_postgres
@pytest.mark.asyncio
async def test_validmind_agent_lookup_is_bounded_tenant_checked_and_callable() -> None:
    engine = create_async_engine(TEST_DB_URL, pool_pre_ping=True)
    namespace = f"validmind-agent-lookup-{uuid4().hex}"
    other_namespace = f"validmind-agent-other-{uuid4().hex}"
    agent_id = f"agent-{uuid4().hex}"
    async with engine.connect() as connection:
        transaction = await connection.begin()
        session = AsyncSession(bind=connection, expire_on_commit=False)
        session_authorization_changed = False
        try:
            await session.execute(
                text("SELECT set_config('app.current_namespace', '__admin__', true)")
            )
            await session.execute(
                text("SELECT set_config('agentmem.barrier_group', '', true)")
            )
            await session.execute(
                text(
                    "INSERT INTO agents (agent_id, namespace) "
                    "VALUES (:agent_id, :namespace)"
                ),
                {"agent_id": agent_id, "namespace": namespace},
            )
            external_id = await session.scalar(
                text(
                    "SELECT 'lians-agent-'::text || substr("
                    "public.lians_sha256_text('agent:'::text || :agent_id), 1, 20)"
                ),
                {"agent_id": agent_id},
            )
            function_posture = (
                await session.execute(
                    text(
                        "SELECT p.prosecdef, p.proconfig, "
                        "has_function_privilege("
                        "'lians_runtime', p.oid, 'EXECUTE') "
                        "FROM pg_proc AS p "
                        "JOIN pg_namespace AS n ON n.oid = p.pronamespace "
                        "WHERE n.nspname = 'public' "
                        "AND p.oid = 'public.lians_validmind_lookup_agent("
                        "text,text)'::regprocedure"
                    )
                )
            ).one()
            assert function_posture == (
                True,
                ["search_path=pg_catalog, public"],
                True,
            )

            await session.execute(
                text("SELECT set_config('app.current_namespace', :value, true)"),
                {"value": namespace},
            )
            await session.execute(text("SET SESSION AUTHORIZATION lians_runtime"))
            session_authorization_changed = True
            visible = list(
                (
                    await session.execute(
                        text(
                            "SELECT agent_id FROM "
                            "public.lians_validmind_lookup_agent("
                            ":namespace, :external_id)"
                        ),
                        {"namespace": namespace, "external_id": external_id},
                    )
                ).scalars().all()
            )
            cross_tenant = list(
                (
                    await session.execute(
                        text(
                            "SELECT agent_id FROM "
                            "public.lians_validmind_lookup_agent("
                            ":namespace, :external_id)"
                        ),
                        {
                            "namespace": other_namespace,
                            "external_id": external_id,
                        },
                    )
                ).scalars().all()
            )
            assert visible == [agent_id]
            assert cross_tenant == []
        finally:
            if session_authorization_changed:
                await session.execute(text("RESET SESSION AUTHORIZATION"))
            await session.close()
            await transaction.rollback()
    await engine.dispose()
