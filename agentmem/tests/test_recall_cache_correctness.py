"""Focused invariants for generation-fenced recall caching and finalization."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest
from lians import cache, session_cache
from lians.config import get_settings
from lians.schemas import RecallRequest, RecallResult


class _FakeRedis:
    def __init__(self) -> None:
        self.values: dict[str, str] = {}

    async def get(self, key: str) -> str | None:
        return self.values.get(key)

    async def set(self, key: str, value: str, *, nx: bool = False) -> bool:
        if nx and key in self.values:
            return False
        self.values[key] = value
        return True

    async def setex(self, key: str, _ttl: int, value: str) -> None:
        self.values[key] = value


@pytest.fixture(autouse=True)
def _clear_local_cache() -> None:
    session_cache.clear_all()
    yield
    session_cache.clear_all()


def test_session_cache_honors_shared_generation() -> None:
    facts = [object()]
    session_cache.set_working_set("ns", "agent", facts, generation="gen-7")

    assert session_cache.get_working_set("ns", "agent", generation="gen-7") == facts
    assert session_cache.get_working_set("ns", "agent", generation="gen-8") is None


def test_session_cache_honors_configured_ttl(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SESSION_CACHE_TTL_SECONDS", "1")
    get_settings.cache_clear()
    clock = iter((10.0, 10.5, 11.0))
    monkeypatch.setattr(session_cache.time, "monotonic", lambda: next(clock))

    session_cache.set_working_set("ns", "agent", ["fact"], generation="gen")
    assert session_cache.get_working_set("ns", "agent", generation="gen") == ["fact"]
    assert session_cache.get_working_set("ns", "agent", generation="gen") is None


def test_session_cache_honors_configured_capacity(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SESSION_CACHE_MAX_ENTRIES", "1")
    get_settings.cache_clear()

    session_cache.set_working_set("ns", "first", ["old"], generation="gen")
    session_cache.set_working_set("ns", "second", ["new"], generation="gen")

    assert session_cache.working_set_size() == 1
    assert session_cache.get_working_set("ns", "first", generation="gen") is None
    assert session_cache.get_working_set("ns", "second", generation="gen") == ["new"]


@pytest.mark.asyncio
async def test_redis_generation_fences_old_results(monkeypatch: pytest.MonkeyPatch) -> None:
    redis = _FakeRedis()
    monkeypatch.setattr(cache, "_enabled", lambda: True)
    monkeypatch.setattr(cache, "_get_redis", lambda: redis)

    generation = await cache.get_agent_cache_generation("ns", "agent")
    assert generation is not None
    await cache.set_cached_recall(
        "ns",
        "agent",
        "query",
        None,
        5,
        {},
        "old-result",
        60,
        generation=generation,
    )
    next_generation = await cache.invalidate_agent("ns", "agent")
    assert next_generation is not None
    assert next_generation != generation

    assert (
        await cache.get_cached_recall(
            "ns", "agent", "query", None, 5, {}, generation=next_generation
        )
        is None
    )


@pytest.mark.asyncio
async def test_redis_invalidation_failure_is_not_swallowed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    redis = MagicMock()
    redis.set = AsyncMock(side_effect=ConnectionError("redis unavailable"))
    monkeypatch.setattr(cache, "_enabled", lambda: True)
    monkeypatch.setattr(cache, "_get_redis", lambda: redis)

    with pytest.raises(cache.RecallCacheInvalidationError):
        await cache.invalidate_agent("ns", "agent")


@pytest.mark.asyncio
async def test_complete_recall_commits_audit_and_durable_meter_together(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from lians import memory_service, metering

    audit_id = uuid4()
    audit_time = datetime.now(UTC)
    call_order: list[str] = []

    async def record_audit(*_args, **_kwargs):
        call_order.append("audit")
        return SimpleNamespace(id=audit_id, created_at=audit_time)

    async def reserve_quota(*_args, **_kwargs):
        call_order.append("quota")

    async def record_meter(*_args, **_kwargs):
        call_order.append("meter")

    async def record_commit():
        call_order.append("commit")

    chain_log = AsyncMock(side_effect=record_audit)
    quota = AsyncMock(side_effect=reserve_quota)
    meter = AsyncMock(side_effect=record_meter)
    commit = AsyncMock(side_effect=record_commit)
    db = SimpleNamespace(commit=commit)
    monkeypatch.setattr(memory_service, "chain_log", chain_log)
    monkeypatch.setattr(memory_service, "reserve_namespace_usage", quota)
    monkeypatch.setattr(metering, "enqueue_usage_event", meter)

    req = RecallRequest(agent_id="agent", query="current guidance")
    result = RecallResult(memories=[], as_of=None, total_candidates=0)
    returned = await memory_service._complete_recall(
        db,
        "ns",
        req,
        result,
        router="cache",
        cache_hit=True,
        started_at=0.0,
    )

    assert returned is result
    quota.assert_awaited_once_with(db, namespace="ns", recalls=1)
    chain_log.assert_awaited_once()
    assert chain_log.await_args.kwargs["payload"]["router"] == "cache"
    assert chain_log.await_args.kwargs["payload"]["cache_hit"] is True
    commit.assert_awaited_once()
    meter.assert_awaited_once_with(
        db,
        namespace="ns",
        event_name=memory_service.get_settings().stripe_meter_recall_event,
        quantity=1,
        source_identifier=f"r:{audit_id}",
        occurred_at=audit_time,
    )
    assert call_order == ["quota", "audit", "meter", "commit"]


@pytest.mark.asyncio
async def test_async_adjudication_restores_read_model_and_fences_before_commit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from lians import audit_chain, current_facts, memory_service, supersession

    old_memory = MagicMock()
    old_memory.valid_to = object()
    old_memory.metadata_ = {"ticker": "NVDA"}
    old_memory.content_hash = "a" * 64
    db = MagicMock()
    db.get = AsyncMock(return_value=old_memory)
    db.commit = AsyncMock()

    class _SessionContext:
        async def __aenter__(self):
            return db

        async def __aexit__(self, *_args):
            return False

    queue: asyncio.Queue = asyncio.Queue()
    monkeypatch.setattr(supersession, "_llm_queue", queue)
    await queue.put(("ns", "agent", uuid4(), uuid4(), "old", "new", {}))
    monkeypatch.setattr(
        supersession,
        "llm_adjudicate",
        AsyncMock(return_value=("CONFIRMS", 0.99, "same fact")),
    )
    lock = AsyncMock()
    fence = AsyncMock()
    upsert = AsyncMock()
    monkeypatch.setattr(memory_service, "_acquire_pg_advisory_lock", lock)
    monkeypatch.setattr(memory_service, "_fence_recall_caches_before_commit", fence)
    monkeypatch.setattr(current_facts, "upsert_live_fact", upsert)
    monkeypatch.setattr(audit_chain, "chain_log", AsyncMock())

    worker = asyncio.create_task(
        supersession.run_llm_adjudication_worker(lambda: _SessionContext())
    )
    await asyncio.wait_for(queue.join(), timeout=1)
    worker.cancel()
    with pytest.raises(asyncio.CancelledError):
        await worker

    old_memory.reopen_validity.assert_called_once()
    upsert.assert_awaited_once()
    fence.assert_awaited_once_with(db, "ns", "agent")
    db.commit.assert_awaited_once()
