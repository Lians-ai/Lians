from datetime import datetime, timezone
from unittest.mock import AsyncMock, patch

import pytest

from src.lians import cache


@pytest.fixture
def enabled(monkeypatch):
    cache._cache_bypass_pairs.clear()
    monkeypatch.setattr(cache, "_enabled", lambda: True)
    yield
    cache._cache_bypass_pairs.clear()


@pytest.mark.asyncio
async def test_policy_is_part_of_cache_key(enabled):
    redis = AsyncMock()
    redis.get = AsyncMock(side_effect=["3", None, "3", None])
    with patch.object(cache, "_get_redis", return_value=redis):
        await cache.get_cached_recall(
            "tenant",
            "agent",
            "query",
            None,
            5,
            {},
            {"mode": "fast", "strategy": "standard"},
        )
        await cache.get_cached_recall(
            "tenant",
            "agent",
            "query",
            None,
            5,
            {},
            {"mode": "deep", "strategy": "adaptive"},
        )
    first_key = redis.get.await_args_list[1].args[0]
    second_key = redis.get.await_args_list[3].args[0]
    assert first_key != second_key
    assert first_key.startswith("agentmem:recall:scoring-v2:")
    assert not first_key.startswith("agentmem:recall:" + cache._pair_hash("tenant", "agent"))


@pytest.mark.asyncio
async def test_invalidation_is_single_increment_without_scan(enabled):
    redis = AsyncMock()
    with patch.object(cache, "_get_redis", return_value=redis):
        await cache.invalidate_agent("tenant", "agent")
    redis.incr.assert_awaited_once()
    assert not redis.scan_iter.called
    assert not redis.delete.called


@pytest.mark.asyncio
async def test_failed_required_invalidation_quarantines_cache_and_raises(enabled):
    redis = AsyncMock()
    redis.incr.side_effect = ConnectionError("redis unavailable")
    with patch.object(cache, "_get_redis", return_value=redis):
        with pytest.raises(cache.RecallCacheInvalidationError):
            await cache.invalidate_agent(
                "tenant", "agent", fail_closed=True
            )
        lookup = await cache.get_cached_recall(
            "tenant", "agent", "query", None, 5, {}, {"mode": "fast"}
        )
        await cache.set_cached_recall(
            "tenant", "agent", "query", None, 5, {}, "stale", 60,
            {"mode": "fast"}, generation="0",
        )

    assert lookup is None
    redis.get.assert_not_awaited()
    redis.setex.assert_not_awaited()


@pytest.mark.asyncio
async def test_hit_is_discarded_when_generation_changes_during_lookup(enabled):
    redis = AsyncMock()
    redis.get = AsyncMock(side_effect=["9", "stale payload", "10"])
    with patch.object(cache, "_get_redis", return_value=redis):
        lookup = await cache.get_cached_recall(
            "tenant", "agent", "query", None, 5, {}, {"mode": "fast"}
        )
    assert lookup is not None
    assert lookup.payload is None
    assert lookup.generation == "10"


@pytest.mark.asyncio
async def test_late_set_uses_lookup_generation_after_invalidation(enabled):
    redis = AsyncMock()
    redis.get = AsyncMock(side_effect=["9", None])
    with patch.object(cache, "_get_redis", return_value=redis):
        lookup = await cache.get_cached_recall(
            "tenant",
            "agent",
            "query",
            datetime(2026, 7, 29, tzinfo=timezone.utc),
            8,
            {"x": 1},
            {"mode": "reconstruct"},
        )
        assert lookup is not None and lookup.payload is None
        await cache.invalidate_agent("tenant", "agent")
        await cache.set_cached_recall(
            "tenant",
            "agent",
            "query",
            datetime(2026, 7, 29, tzinfo=timezone.utc),
            8,
            {"x": 1},
            "payload",
            60,
            {"mode": "reconstruct"},
            generation=lookup.generation,
        )
    key = redis.setex.await_args.args[0]
    assert ":9:" in key
    assert redis.setex.await_args.args[1:] == (60, "payload")
    assert redis.get.await_count == 2
    redis.incr.assert_awaited_once_with(cache._generation_key("tenant", "agent"))
