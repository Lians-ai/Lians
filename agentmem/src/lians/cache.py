"""Redis hot cache for recall results.

Architecture:

* Generation: ``agentmem:recall-generation:{namespace_agent_hash}``
* Value key: ``agentmem:recall:{schema}:{namespace_agent_hash}:{generation}:...``
* TTL: ``config.recall_cache_ttl_seconds`` (default 60 seconds)

Invalidation is O(1): a write increments the pair's generation. Old-generation
entries become unreachable immediately and expire under their normal TTL. This
avoids a keyspace scan and stays correct when reads and writes race across
processes.

The key includes the complete retrieval policy, not only filters. A fast result
can never be served for a deep or reconstruction request.

Lookups return the generation they observed. A later cache fill writes only to
that captured generation, so a concurrent write/erase cannot make stale work
reachable by advancing the generation between the lookup and fill.

All Redis errors are swallowed because cache availability is never required for
memory correctness.
"""
from __future__ import annotations

import hashlib
import json
import logging
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Optional

_redis_client: Any = None
logger = logging.getLogger("agentmem.cache")
_CACHE_SCHEMA_VERSION = "scoring-v1"


@dataclass(frozen=True)
class RecallCacheLookup:
    """A cache read bound to the generation observed before recall starts."""

    payload: Optional[str]
    generation: str


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
        f"agentmem:recall:{_CACHE_SCHEMA_VERSION}:"
        f"{_pair_hash(namespace, agent_id)}:{generation}:"
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
) -> Optional[RecallCacheLookup]:
    if not _enabled():
        return None
    try:
        redis = _get_redis()
        generation_key = _generation_key(namespace, agent_id)
        generation = await redis.get(generation_key) or "0"
        key = _recall_key(
            namespace, agent_id, generation, query, as_of, k, filters, policy
        )
        payload = await redis.get(key)
        if payload is not None:
            # Linearize a hit after reading its value. If invalidation completed
            # between the generation read and value read, discard the old hit
            # and let recall compute against the newer database state.
            current_generation = await redis.get(generation_key) or "0"
            if current_generation != generation:
                return RecallCacheLookup(
                    payload=None,
                    generation=current_generation,
                )
        return RecallCacheLookup(payload=payload, generation=generation)
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
    *,
    generation: str,
) -> None:
    if not _enabled():
        return
    try:
        redis = _get_redis()
        key = _recall_key(
            namespace, agent_id, generation, query, as_of, k, filters, policy
        )
        await redis.setex(key, ttl, payload)
    except Exception:
        logger.debug("Recall cache write failed; continuing without cache", exc_info=True)


async def invalidate_agent(namespace: str, agent_id: str) -> None:
    """Make every cached result for an agent unreachable in O(1)."""
    if not _enabled():
        return
    try:
        await _get_redis().incr(_generation_key(namespace, agent_id))
    except Exception:
        logger.debug("Recall cache invalidation failed; continuing without cache", exc_info=True)
