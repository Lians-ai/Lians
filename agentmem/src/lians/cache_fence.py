"""Database ordering boundary for namespace-wide recall-cache generations."""

from __future__ import annotations

import hashlib

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession


def _namespace_cache_lock_key(namespace: str) -> int:
    digest = hashlib.sha256(
        b"lians/recall-cache-namespace/v1\0" + namespace.encode("utf-8")
    ).digest()
    return int.from_bytes(digest[:8], "big", signed=True)


async def acquire_namespace_cache_lock(
    db: AsyncSession,
    namespace: str,
    *,
    shared: bool,
) -> bool:
    """Acquire the transaction lock ordering recalls against an erasure fence."""

    if db.get_bind().dialect.name != "postgresql":
        return False
    function = "pg_advisory_xact_lock_shared" if shared else "pg_advisory_xact_lock"
    await db.execute(
        text(f"SELECT {function}(:namespace_cache_lock)"),
        {"namespace_cache_lock": _namespace_cache_lock_key(namespace)},
    )
    return True
