"""Redis hot cache for recall results.

Architecture:

* Generation: ``agentmem:recall-generation:{namespace_agent_hash}``
* Value key: ``agentmem:recall:{namespace_agent_hash}:{generation}:...``
* TTL: ``config.recall_cache_ttl_seconds`` (default 60 seconds)

Invalidation is O(1): a write increments the pair's generation. Old-generation
entries become unreachable immediately and expire under their normal TTL. This
avoids a keyspace scan and stays correct when reads and writes race across
processes.

The key includes the complete retrieval policy, not only filters. A fast result
can never be served for a deep or reconstruction request.

All Redis errors are swallowed because cache availability is never required for
memory correctness.
"""
from __future__ import annotations

import hashlib
import json
from datetime import datetime
from typing import Any, Optional

_redis_client: Any = None


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


def _h(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()[:16]


def _pair_hash(namespace: str, agent_id: str) -> str:
    return hashlib.sha256(f"{namespace}\0{agent_id}".encode()).hexdigest()[:32]


def _generation_key(namespace: str, agent_id: str) -> str:
    return f"agentmem:recall-generation:{_pair_hash(namespace, agent_id)}"


def _recall_key(
    namespace: str,
    agent_id: str,
    generation: str,
    query: str,
    as_of: Optional[datetime],
    k: int,
    filters: Optional[dict],
    policy: Optional[dict],
) -> str:
    as_of_str = as_of.isoformat() if as_of else "none"
    filters_str = json.dumps(filters or {}, sort_keys=True)
    policy_str = json.dumps(policy or {}, sort_keys=True)
    return (
        f"agentmem:recall:{_pair_hash(namespace, agent_id)}:{generation}:"
        f"{_h(query)}:{as_of_str}:{k}:{_h(filters_str)}:{_h(policy_str)}"
    )


def _enabled() -> bool:
    from .config import get_settings

    return get_settings().recall_cache_enabled


async def get_cached_recall(
    namespace: str,
    agent_id: str,
    query: str,
    as_of: Optional[datetime],
    k: int,
    filters: Optional[dict],
    policy: Optional[dict] = None,
) -> Optional[str]:
    if not _enabled():
        return None
    try:
        redis = _get_redis()
        generation = await redis.get(_generation_key(namespace, agent_id)) or "0"
        key = _recall_key(
            namespace, agent_id, generation, query, as_of, k, filters, policy
        )
        return await redis.get(key)
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
    policy: Optional[dict] = None,
) -> None:
    if not _enabled():
        return
    try:
        redis = _get_redis()
        generation = await redis.get(_generation_key(namespace, agent_id)) or "0"
        key = _recall_key(
            namespace, agent_id, generation, query, as_of, k, filters, policy
        )
        await redis.setex(key, ttl, payload)
    except Exception:
        pass


async def invalidate_agent(namespace: str, agent_id: str) -> None:
    """Make every cached result for an agent unreachable in O(1)."""
    if not _enabled():
        return
    try:
        await _get_redis().incr(_generation_key(namespace, agent_id))
    except Exception:
        pass
