"""
Redis hot cache for recall results.

Architecture:
- Key:   agentmem:recall:{namespace}:{agent_id}:{query_hash}:{as_of}:{k}:{filters_hash}
- Value: JSON-serialised RecallResult
- TTL:   config.recall_cache_ttl_seconds (default 60 s)

Invalidation: any write to (namespace, agent_id) deletes all recall keys for
that pair via SCAN + DEL. The pattern is narrow enough that SCAN is safe
even for large deployments (keys are bounded by unique query×filter×k combos).

All Redis errors are swallowed — the cache layer is never on the critical path.
"""
from __future__ import annotations

import hashlib
import json
from datetime import datetime
from typing import Any, Optional

_redis_client: Any = None  # redis.asyncio.Redis, lazily initialised


def _get_redis() -> Any:
    global _redis_client
    if _redis_client is None:
        import redis.asyncio as aioredis
        from .config import get_settings
        _redis_client = aioredis.from_url(
            get_settings().redis_url,
            decode_responses=True,
            socket_connect_timeout=1,
            socket_timeout=1,
        )
    return _redis_client


def _h(s: str) -> str:
    return hashlib.sha256(s.encode()).hexdigest()[:16]


def _recall_key(
    namespace: str,
    agent_id: str,
    query: str,
    as_of: Optional[datetime],
    k: int,
    filters: Optional[dict],
) -> str:
    as_of_str = as_of.isoformat() if as_of else "none"
    filters_str = json.dumps(filters or {}, sort_keys=True)
    return f"agentmem:recall:{namespace}:{agent_id}:{_h(query)}:{as_of_str}:{k}:{_h(filters_str)}"


async def get_cached_recall(
    namespace: str,
    agent_id: str,
    query: str,
    as_of: Optional[datetime],
    k: int,
    filters: Optional[dict],
) -> Optional[str]:
    try:
        key = _recall_key(namespace, agent_id, query, as_of, k, filters)
        return await _get_redis().get(key)
    except Exception:
        return None


async def set_cached_recall(
    namespace: str,
    agent_id: str,
    query: str,
    as_of: Optional[datetime],
    k: int,
    filters: Optional[dict],
    payload: str,
    ttl: int,
) -> None:
    try:
        key = _recall_key(namespace, agent_id, query, as_of, k, filters)
        await _get_redis().setex(key, ttl, payload)
    except Exception:
        pass


async def invalidate_agent(namespace: str, agent_id: str) -> None:
    """Delete all recall cache entries for (namespace, agent_id) after a write."""
    try:
        pattern = f"agentmem:recall:{namespace}:{agent_id}:*"
        r = _get_redis()
        keys = [k async for k in r.scan_iter(pattern, count=100)]
        if keys:
            await r.delete(*keys)
    except Exception:
        pass
