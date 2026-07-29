from datetime import datetime, timezone
from unittest.mock import AsyncMock, patch

import pytest

from src.lians import cache


@pytest.fixture
def enabled(monkeypatch):
    monkeypatch.setattr(cache, "_enabled", lambda: True)


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


@pytest.mark.asyncio
async def test_invalidation_is_single_increment_without_scan(enabled):
    redis = AsyncMock()
    with patch.object(cache, "_get_redis", return_value=redis):
        await cache.invalidate_agent("tenant", "agent")
    redis.incr.assert_awaited_once()
    assert not redis.scan_iter.called
    assert not redis.delete.called


@pytest.mark.asyncio
async def test_set_uses_current_generation(enabled):
    redis = AsyncMock()
    redis.get = AsyncMock(return_value="9")
    with patch.object(cache, "_get_redis", return_value=redis):
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
        )
    key = redis.setex.await_args.args[0]
    assert ":9:" in key
    assert redis.setex.await_args.args[1:] == (60, "payload")
