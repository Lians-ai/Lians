"""Startup gates for the production hosted MCP boundary."""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from pathlib import Path
from types import SimpleNamespace

import pytest
import src.lians.main as main_module
from src.lians.config import Settings
from src.lians.main import (
    _HOSTED_RLS_TABLES,
    _lifespan_task_scope,
    _validate_hosted_rls,
    _validate_production_secrets,
    _wait_for_hosted_dependencies,
)


class _Rows:
    def __init__(self, rows):
        self._rows = rows

    def mappings(self):
        return self

    def all(self):
        return self._rows


class _Connection:
    def __init__(self, rows):
        self._rows = rows

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args):
        return None

    async def execute(self, _statement):
        return _Rows(self._rows)


class _Engine:
    def __init__(self, rows, dialect="postgresql"):
        self.dialect = SimpleNamespace(name=dialect)
        self._rows = rows

    def connect(self):
        return _Connection(self._rows)


async def test_hosted_production_requires_force_rls_on_every_storage_table():
    rows = [
        {
            "relname": table,
            "relrowsecurity": True,
            "relforcerowsecurity": table != "event_log",
        }
        for table in _HOSTED_RLS_TABLES
    ]

    with pytest.raises(RuntimeError, match="event_log"):
        await _validate_hosted_rls(_Engine(rows), production=True, enabled=True)


async def test_hosted_rls_gate_accepts_complete_catalog_and_skips_non_hosted():
    rows = [
        {"relname": table, "relrowsecurity": True, "relforcerowsecurity": True}
        for table in _HOSTED_RLS_TABLES
    ]

    await _validate_hosted_rls(_Engine(rows), production=True, enabled=True)
    with pytest.raises(RuntimeError, match="requires PostgreSQL"):
        await _validate_hosted_rls(_Engine([], dialect="sqlite"), production=True, enabled=True)
    await _validate_hosted_rls(_Engine([]), production=True, enabled=False)


def test_hosted_startup_timeout_has_an_independent_env_setting(monkeypatch):
    assert Settings.model_fields["hosted_mcp_startup_timeout_seconds"].default == 180
    assert Settings.model_fields["hosted_mcp_tool_timeout_seconds"].default == 30

    monkeypatch.setenv("HOSTED_MCP_STARTUP_TIMEOUT_SECONDS", "240")
    monkeypatch.setenv("HOSTED_MCP_TOOL_TIMEOUT_SECONDS", "17")
    settings = Settings(_env_file=None)

    assert settings.hosted_mcp_startup_timeout_seconds == 240
    assert settings.hosted_mcp_tool_timeout_seconds == 17


def test_production_rejects_unencrypted_override(monkeypatch):
    monkeypatch.setenv("AGENTMEM_ALLOW_UNENCRYPTED", "true")
    settings = SimpleNamespace(
        deployment_environment="production",
        admin_secret="a" * 48,
        cors_origins="https://www.lians.ai",
    )

    with pytest.raises(RuntimeError, match="AGENTMEM_ALLOW_UNENCRYPTED"):
        _validate_production_secrets(settings)


def test_hosted_startup_timeout_default_is_published_to_deploy_configs():
    repository_root = Path(__file__).parents[2]
    env_example = (repository_root / "agentmem" / ".env.example").read_text(encoding="utf-8")
    fly_config = (repository_root / "fly.toml").read_text(encoding="utf-8")
    render_config = (repository_root / "render.yaml").read_text(encoding="utf-8")

    assert "HOSTED_MCP_STARTUP_TIMEOUT_SECONDS=180" in env_example
    assert 'HOSTED_MCP_STARTUP_TIMEOUT_SECONDS = "180"' in fly_config
    assert ('- key: HOSTED_MCP_STARTUP_TIMEOUT_SECONDS\n        value: "180"') in render_config


async def test_hosted_dependency_gate_forces_one_startup_jwks_refresh():
    calls = []

    class _Verifier:
        async def warm_jwks(self, *, force_refresh: bool = False):
            calls.append(force_refresh)

    async def _warm_embedding():
        return True

    task = asyncio.create_task(_warm_embedding())
    await _wait_for_hosted_dependencies(
        SimpleNamespace(verifier=_Verifier()),
        task,
        timeout_seconds=1,
    )

    assert calls == [True]


async def test_hosted_dependency_gate_fails_closed_for_embedding_or_jwks():
    class _Verifier:
        def __init__(self, *, fail: bool = False):
            self.fail = fail
            self.calls = 0

        async def warm_jwks(self, *, force_refresh: bool = False):
            self.calls += 1
            assert force_refresh is True
            if self.fail:
                raise RuntimeError("JWKS unavailable")

    async def _embedding_ready(value: bool):
        return value

    skipped_verifier = _Verifier()
    failed_embedding = asyncio.create_task(_embedding_ready(False))
    with pytest.raises(RuntimeError, match="working embedding provider"):
        await _wait_for_hosted_dependencies(
            SimpleNamespace(verifier=skipped_verifier),
            failed_embedding,
            timeout_seconds=1,
        )
    assert skipped_verifier.calls == 0

    failing_verifier = _Verifier(fail=True)
    ready_embedding = asyncio.create_task(_embedding_ready(True))
    with pytest.raises(RuntimeError, match="JWKS unavailable"):
        await _wait_for_hosted_dependencies(
            SimpleNamespace(verifier=failing_verifier),
            ready_embedding,
            timeout_seconds=1,
        )


async def test_lifespan_task_scope_enters_mcp_first_and_drains_all_tasks():
    events = []

    @asynccontextmanager
    async def _session():
        events.append("session-enter")
        try:
            yield
        finally:
            events.append("session-exit")

    async def _running_worker():
        events.append("worker-start")
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            events.append("worker-cancelled")
            raise

    async def _failed_worker():
        events.append("failed-worker-start")
        raise ValueError("background failure")

    with pytest.raises(RuntimeError, match="startup failure"):
        async with _lifespan_task_scope(_session()) as tasks:
            assert events == ["session-enter"]
            running_task = asyncio.create_task(_running_worker())
            failed_task = asyncio.create_task(_failed_worker())
            tasks.extend((running_task, failed_task))
            await asyncio.sleep(0)
            raise RuntimeError("startup failure")

    assert running_task.cancelled()
    assert failed_task.done()
    assert events.index("session-enter") < events.index("worker-start")
    assert events.index("worker-cancelled") < events.index("session-exit")


async def test_lifespan_task_scope_starts_no_tasks_when_session_enter_fails():
    events = []

    @asynccontextmanager
    async def _failed_session():
        events.append("session-enter")
        raise RuntimeError("session enter failed")
        yield  # pragma: no cover - required to define an async context manager

    with pytest.raises(RuntimeError, match="session enter failed"):
        async with _lifespan_task_scope(_failed_session()):
            events.append("body-entered")

    assert events == ["session-enter"]


async def test_readyz_uses_cached_jwks_check_without_forcing_network(monkeypatch):
    calls = []

    class _Verifier:
        async def warm_jwks(self, *, force_refresh: bool = False):
            calls.append(force_refresh)

    async def _healthy(_db):
        return {"status": "ok"}

    monkeypatch.setattr(
        main_module,
        "_hosted_mcp_runtime",
        SimpleNamespace(verifier=_Verifier()),
    )
    monkeypatch.setattr(main_module, "health", _healthy)

    result = await main_module.readyz(object())

    assert result == {"status": "ok"}
    assert calls == [False]
