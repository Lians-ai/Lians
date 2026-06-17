"""
LocalAgentMemClient — zero-setup local mode.

Calls the AgentMem service layer directly against an in-memory (or file-based)
SQLite database.  No server, no Docker, no API key.  Perfect for prototyping,
notebooks, and CI.

Usage::

    from agentmem_sdk import LocalAgentMemClient
    from datetime import datetime, timezone

    with LocalAgentMemClient() as mem:
        mem.add(
            agent_id="research",
            content="NVDA Q3 guidance raised to $36B",
            event_time=datetime(2026, 5, 10, tzinfo=timezone.utc),
            metadata={"ticker": "NVDA", "metric": "guidance"},
        )
        result = mem.recall(agent_id="research", query="NVDA guidance")
        print(result["memories"][0]["content"])

Persistent mode::

    mem = LocalAgentMemClient(db_path="~/.agentmem/local.db")

Switching to the hosted API later requires only changing the client class::

    # from agentmem_sdk import LocalAgentMemClient as AgentMemClient   # dev
    from agentmem_sdk import AgentMemClient                             # prod
"""
from __future__ import annotations

import asyncio
import os
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession
from sqlalchemy.pool import StaticPool


def _ensure_src_importable() -> None:
    """
    Add the agentmem package root to sys.path so that
    ``from src.agentmem.xxx import ...`` resolves in development.
    This is a no-op once agentmem is installed as a proper package
    (in which case ``from agentmem.xxx import ...`` takes over).
    Structure assumption: this file lives at
      <pkg_root>/sdk/python/agentmem_sdk/local_client.py
    so parents[3] == <pkg_root>.
    """
    import sys as _sys
    pkg_root = str(Path(__file__).resolve().parents[3])
    if pkg_root not in _sys.path:
        _sys.path.insert(0, pkg_root)


_ensure_src_importable()


class LocalAgentMemClient:
    """
    Synchronous AgentMem client backed by local SQLite — no server required.

    Parameters
    ----------
    db_path:
        Path to a SQLite file for persistent storage.  ``None`` (default) uses
        an in-memory database that is discarded when the client is closed.
    namespace:
        Logical tenant namespace.  Useful when sharing one DB file across
        multiple projects.  Defaults to ``"local"``.
    embedding_provider:
        Override the embedding provider (``"local"`` | ``"voyage"`` | ``"openai"``).
        Defaults to ``"local"`` (deterministic word-projection, zero API calls).
    """

    def __init__(
        self,
        db_path: Optional[str] = None,
        namespace: str = "local",
        embedding_provider: str = "local",
    ):
        self._namespace = namespace
        self._loop = asyncio.new_event_loop()

        # Point the settings at the local embedding provider before any import
        os.environ.setdefault("EMBEDDING_PROVIDER", embedding_provider)

        # Build the async engine
        if db_path is None:
            url = "sqlite+aiosqlite:///:memory:"
            engine_kwargs: dict[str, Any] = {
                "connect_args": {"check_same_thread": False},
                "poolclass": StaticPool,
            }
        else:
            resolved = str(Path(db_path).expanduser())
            Path(resolved).parent.mkdir(parents=True, exist_ok=True)
            url = f"sqlite+aiosqlite:///{resolved}"
            engine_kwargs = {"connect_args": {"check_same_thread": False}}

        self._engine = create_async_engine(url, **engine_kwargs)
        self._session_factory = async_sessionmaker(
            self._engine, expire_on_commit=False, class_=AsyncSession
        )
        self._loop.run_until_complete(self._init_db())

    # ------------------------------------------------------------------
    # Setup / teardown
    # ------------------------------------------------------------------

    async def _init_db(self) -> None:
        from src.agentmem.models import Base  # lazy import; avoids circular refs

        # Drop Postgres-only indexes so SQLite doesn't choke
        pg_indexes = [
            idx
            for table in Base.metadata.tables.values()
            for idx in list(table.indexes)
            if idx.dialect_kwargs.get("postgresql_using") is not None
        ]
        for idx in pg_indexes:
            idx.table.indexes.discard(idx)

        async with self._engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

    def __enter__(self) -> "LocalAgentMemClient":
        return self

    def __exit__(self, *_: Any) -> None:
        self.close()

    def close(self) -> None:
        self._loop.run_until_complete(self._engine.dispose())
        self._loop.close()

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _run(self, coro):  # type: ignore[type-arg]
        return self._loop.run_until_complete(coro)

    def _session(self) -> Any:
        return self._session_factory()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def add(
        self,
        agent_id: str,
        content: str,
        event_time: datetime,
        source: Optional[str] = None,
        subject_id: Optional[str] = None,
        metadata: Optional[dict[str, Any]] = None,
        importance: float = 0.5,
    ) -> dict:
        """Add a memory. Returns the created MemoryOut as a dict."""
        return self._run(self._async_add(
            agent_id=agent_id, content=content, event_time=event_time,
            source=source, subject_id=subject_id,
            metadata=metadata or {}, importance=importance,
        ))

    async def _async_add(self, **kwargs) -> dict:
        from src.agentmem.schemas import MemoryAdd
        from src.agentmem.memory_service import add_memory
        req = MemoryAdd(**kwargs)
        async with self._session_factory() as db:
            result = await add_memory(db, self._namespace, req)
        return result.model_dump(mode="json")

    def recall(
        self,
        agent_id: str,
        query: str,
        k: int = 5,
        as_of: Optional[datetime] = None,
        filters: Optional[dict[str, Any]] = None,
    ) -> dict:
        """Recall memories. Returns RecallResult as a dict."""
        return self._run(self._async_recall(
            agent_id=agent_id, query=query, k=k, as_of=as_of,
            filters=filters or {},
        ))

    async def _async_recall(self, **kwargs) -> dict:
        from src.agentmem.schemas import RecallRequest
        from src.agentmem.memory_service import recall_memories
        req = RecallRequest(**kwargs)
        async with self._session_factory() as db:
            result = await recall_memories(db, self._namespace, req)
        return result.model_dump(mode="json")

    def reconstruct(
        self,
        agent_id: str,
        as_of: datetime,
        query: Optional[str] = None,
        k: int = 20,
    ) -> dict:
        """Audit reconstruction. Returns AuditReconstructResult as a dict."""
        return self._run(self._async_reconstruct(
            agent_id=agent_id, as_of=as_of, query=query, k=k,
        ))

    async def _async_reconstruct(self, **kwargs) -> dict:
        from src.agentmem.audit import reconstruct
        async with self._session_factory() as db:
            result = await reconstruct(db, self._namespace, **kwargs)
        return result.model_dump(mode="json")

    def erase(self, subject_id: str, request_ref: str) -> dict:
        """
        GDPR crypto-shred.  Returns ``{"subject_id": ..., "memories_erased": N}``.
        """
        return self._run(self._async_erase(
            subject_id=subject_id, request_ref=request_ref,
        ))

    async def _async_erase(self, subject_id: str, request_ref: str) -> dict:
        from src.agentmem.memory_service import erase_subject
        async with self._session_factory() as db:
            count = await erase_subject(db, self._namespace, subject_id, request_ref)
        return {
            "subject_id": subject_id,
            "memories_erased": count,
            "request_ref": request_ref,
        }
