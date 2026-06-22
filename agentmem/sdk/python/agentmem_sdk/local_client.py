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
        # LocalAgentMemClient is a dev/test tool — allow running without a real key.
        # Production deployments use AgentMemClient (HTTP) against a server that
        # enforces MASTER_ENCRYPTION_KEY at startup.
        os.environ.setdefault("MASTER_ENCRYPTION_KEY", "")
        os.environ.setdefault("AGENTMEM_ALLOW_UNENCRYPTED", "true")

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
        from src.agentmem.kms import load_master_key

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

        await load_master_key()

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

    def audit_export(
        self,
        from_dt: Optional[datetime] = None,
        to_dt: Optional[datetime] = None,
        limit: int = 100_000,
        verify: bool = False,
    ) -> dict:
        """
        Export the full audit log for this namespace.

        Returns a dict matching AuditExportResult schema with an ``events``
        list of all event_log rows in chronological order.  Pass ``verify=True``
        to include a tamper-evidence chain verification report.
        """
        return self._run(self._async_audit_export(
            from_dt=from_dt, to_dt=to_dt, limit=limit, include_chain_status=verify,
        ))

    async def _async_audit_export(
        self,
        from_dt: Optional[datetime],
        to_dt: Optional[datetime],
        limit: int,
        include_chain_status: bool,
    ) -> dict:
        from src.agentmem.audit_chain import export_audit_log
        async with self._session_factory() as db:
            result = await export_audit_log(
                db,
                namespace=self._namespace,
                from_dt=from_dt,
                to_dt=to_dt,
                limit=limit,
                include_chain_status=include_chain_status,
            )
        # Serialize datetimes to ISO strings for consistent dict output
        for evt in result.get("events", []):
            if isinstance(evt.get("created_at"), datetime):
                evt["created_at"] = evt["created_at"].isoformat()
        if result.get("from_") and isinstance(result["from_"], datetime):
            result["from_"] = result["from_"].isoformat()
        if result.get("to") and isinstance(result["to"], datetime):
            result["to"] = result["to"].isoformat()
        return result

    def verify_chain(self) -> dict:
        """
        Verify the SEC 17a-4 tamper-evidence hash chain for this namespace.

        Returns ``{"status": "ok", "rows_checked": N, "violations": []}``
        or ``{"status": "tampered", "violations": [...]}`` with details on
        every broken link.
        """
        return self._run(self._async_verify_chain())

    async def _async_verify_chain(self) -> dict:
        from src.agentmem.audit_chain import verify_chain
        async with self._session_factory() as db:
            return await verify_chain(db, namespace=self._namespace)

    # ── Batch write ───────────────────────────────────────────────────────────

    def batch_add(self, memories: list[dict]) -> dict:
        """
        Add multiple memories in a single call.

        Items are processed sequentially so a later item can supersede an earlier
        one within the same batch.  Returns a MemoryBatchResult dict.
        """
        return self._run(self._async_batch_add(memories))

    async def _async_batch_add(self, memories: list[dict]) -> dict:
        from src.agentmem.schemas import MemoryAdd
        from src.agentmem.memory_service import batch_add_memories
        items = [MemoryAdd(**m) for m in memories]
        async with self._session_factory() as db:
            result = await batch_add_memories(db, self._namespace, items)
        return result.model_dump(mode="json")

    # ── Point-in-time convenience ──────────────────────────────────────────────

    def recall_at(
        self,
        agent_id: str,
        query: str,
        as_of: datetime,
        k: int = 5,
        filters: Optional[dict[str, Any]] = None,
    ) -> dict:
        """
        Recall memories valid at *as_of* (point-in-time compliance query).

        Equivalent to ``recall(..., as_of=as_of)`` but signals intent at the
        call site — use for audit questions rather than present-time queries.
        """
        return self.recall(agent_id=agent_id, query=query, k=k, as_of=as_of, filters=filters)

    # ── Supersession review ───────────────────────────────────────────────────

    def review_supersessions(
        self,
        threshold: Optional[float] = None,
        limit: int = 50,
    ) -> dict:
        """
        Return supersession events whose confidence is below *threshold*.

        Returns a SupersessionReviewResult dict with an ``items`` list, sorted
        newest-first.  Defaults to the configured review threshold.
        """
        return self._run(self._async_review_supersessions(threshold=threshold, limit=limit))

    async def _async_review_supersessions(
        self,
        threshold: Optional[float],
        limit: int,
    ) -> dict:
        from src.agentmem.memory_service import get_pending_supersessions
        async with self._session_factory() as db:
            result = await get_pending_supersessions(
                db=db,
                namespace=self._namespace,
                confidence_threshold=threshold,
                limit=limit,
            )
        return result.model_dump(mode="json")

    def confirm_supersession(
        self,
        memory_id: str,
        reviewer_note: Optional[str] = None,
    ) -> dict:
        """
        Confirm a supersession was correct.

        Writes an immutable audit event; the superseded memory remains closed.
        Returns a SupersessionActionResult dict.
        """
        return self._run(self._async_supersession_action(memory_id, "confirm", reviewer_note))

    def reject_supersession(
        self,
        memory_id: str,
        reviewer_note: Optional[str] = None,
    ) -> dict:
        """
        Reject a supersession — the engine was wrong.

        Restores the old memory as currently valid (valid_to = NULL) and writes
        an immutable audit event.  Returns a SupersessionActionResult dict.
        """
        return self._run(self._async_supersession_action(memory_id, "reject", reviewer_note))

    async def _async_supersession_action(
        self,
        memory_id: str,
        action: str,
        reviewer_note: Optional[str],
    ) -> dict:
        from uuid import UUID
        from src.agentmem.schemas import SupersessionAction
        from src.agentmem.memory_service import apply_supersession_action
        body = SupersessionAction(action=action, reviewer_note=reviewer_note)
        async with self._session_factory() as db:
            result = await apply_supersession_action(
                db, self._namespace, UUID(memory_id), body
            )
        return result.model_dump(mode="json")

    # ── Snapshot / compliance ─────────────────────────────────────────────────

    def snapshot(
        self,
        agent_id: str,
        as_of: datetime,
        limit: int = 1000,
    ) -> dict:
        """
        Return agent's complete knowledge state at *as_of* (exhaustive, no ANN).

        Unlike ``recall()``, this is not ranked — it returns every memory that
        was valid at that instant, ordered by event_time ascending.  Use for
        compliance audit: "what did the agent know on date X?"
        """
        return self._run(self._async_snapshot(agent_id=agent_id, as_of=as_of, limit=limit))

    async def _async_snapshot(self, agent_id: str, as_of: datetime, limit: int) -> dict:
        from src.agentmem.memory_service import get_knowledge_snapshot
        async with self._session_factory() as db:
            items = await get_knowledge_snapshot(db, self._namespace, agent_id, as_of, limit)
        return {
            "agent_id": agent_id,
            "namespace": self._namespace,
            "as_of": as_of.isoformat(),
            "total": len(items),
            "items": [m.model_dump(mode="json") for m in items],
        }

    def backtest_check(
        self,
        agent_id: str,
        simulation_as_of: datetime,
    ) -> dict:
        """
        Detect lookahead bias in a backtest.

        Returns a ContaminationReport dict with any ``future_event`` or
        ``late_revision`` flags that could invalidate the simulation.
        """
        return self._run(self._async_backtest_check(
            agent_id=agent_id, simulation_as_of=simulation_as_of,
        ))

    async def _async_backtest_check(self, agent_id: str, simulation_as_of: datetime) -> dict:
        from src.agentmem.backtest import check_contamination
        async with self._session_factory() as db:
            report = await check_contamination(db, self._namespace, agent_id, simulation_as_of)
        return report.model_dump(mode="json")

    def list_conflicts(
        self,
        status: Optional[str] = None,
        limit: int = 50,
    ) -> dict:
        """
        List conflict flags for this namespace.

        *status* filters by ``"open"``, ``"accept_a"``, ``"accept_b"``, or
        ``"dismissed"``.  Returns a ConflictListResult dict.
        """
        return self._run(self._async_list_conflicts(status=status, limit=limit))

    async def _async_list_conflicts(self, status: Optional[str], limit: int) -> dict:
        from src.agentmem.memory_service import list_conflicts
        async with self._session_factory() as db:
            result = await list_conflicts(db, self._namespace, status=status, limit=limit)
        return result.model_dump(mode="json")

    def resolve_conflict(
        self,
        conflict_id: str,
        resolution: str,
        note: Optional[str] = None,
    ) -> dict:
        """
        Resolve a conflict flag.

        *resolution* must be ``"accept_a"``, ``"accept_b"``, or ``"dismiss"``.
        Returns a ConflictResolveResult dict.
        """
        return self._run(self._async_resolve_conflict(
            conflict_id=conflict_id, resolution=resolution, note=note,
        ))

    async def _async_resolve_conflict(
        self, conflict_id: str, resolution: str, note: Optional[str]
    ) -> dict:
        from uuid import UUID
        from src.agentmem.schemas import ConflictResolveRequest
        from src.agentmem.memory_service import resolve_conflict
        req = ConflictResolveRequest(resolution=resolution, note=note)
        async with self._session_factory() as db:
            result = await resolve_conflict(db, self._namespace, UUID(conflict_id), req)
        return result.model_dump(mode="json")

    def fact_history(
        self,
        agent_id: str,
        ticker: str,
        metric: str,
        limit: int = 100,
    ) -> list[dict]:
        """
        Return all versions of a ticker+metric fact, ordered by event_time ascending.

        Shows the full history of revisions — useful for understanding how a
        structured fact evolved over time (e.g., NVDA guidance across quarters).
        """
        return self._run(self._async_fact_history(
            agent_id=agent_id, ticker=ticker, metric=metric, limit=limit,
        ))

    async def _async_fact_history(
        self, agent_id: str, ticker: str, metric: str, limit: int
    ) -> list[dict]:
        from src.agentmem.adapters.finance import FinanceAdapter
        adapter = FinanceAdapter()
        async with self._session_factory() as db:
            items = await adapter.fact_history(db, self._namespace, agent_id, ticker, metric, limit)
        return [m.model_dump(mode="json") for m in items]

    def erasure_certificate(self, subject_id: str) -> dict:
        """
        Return a verifiable erasure certificate for a data subject.

        The certificate includes SHA-256 content hashes of erased memories
        and the audit chain status — suitable for regulatory filing.
        """
        return self._run(self._async_erasure_certificate(subject_id=subject_id))

    async def _async_erasure_certificate(self, subject_id: str) -> dict:
        from src.agentmem.memory_service import get_erasure_certificate
        async with self._session_factory() as db:
            return await get_erasure_certificate(db, self._namespace, subject_id)

    def compliance_report(self, *args: Any, **kwargs: Any) -> dict:  # noqa: ARG002
        """
        Not available in local mode.

        The compliance report aggregates SQL window queries that require a
        full PostgreSQL deployment.  Use AgentMemClient (HTTP) against a
        running server for this endpoint.
        """
        raise NotImplementedError(
            "compliance_report() requires a server deployment. "
            "Use AgentMemClient (HTTP) instead of LocalAgentMemClient."
        )

    def register_webhook(self, *args: Any, **kwargs: Any) -> dict:  # noqa: ARG002
        """Not available in local mode — webhooks require an HTTP server."""
        raise NotImplementedError(
            "Webhooks require a server deployment. "
            "Use AgentMemClient (HTTP) instead of LocalAgentMemClient."
        )

    def list_webhooks(self, *args: Any, **kwargs: Any) -> list:  # noqa: ARG002
        """Not available in local mode — webhooks require an HTTP server."""
        raise NotImplementedError(
            "Webhooks require a server deployment. "
            "Use AgentMemClient (HTTP) instead of LocalAgentMemClient."
        )

    def update_webhook(self, *args: Any, **kwargs: Any) -> dict:  # noqa: ARG002
        """Not available in local mode — webhooks require an HTTP server."""
        raise NotImplementedError(
            "Webhooks require a server deployment. "
            "Use AgentMemClient (HTTP) instead of LocalAgentMemClient."
        )

    def delete_webhook(self, *args: Any, **kwargs: Any) -> None:  # noqa: ARG002
        """Not available in local mode — webhooks require an HTTP server."""
        raise NotImplementedError(
            "Webhooks require a server deployment. "
            "Use AgentMemClient (HTTP) instead of LocalAgentMemClient."
        )

    def webhook_deliveries(self, *args: Any, **kwargs: Any) -> list:  # noqa: ARG002
        """Not available in local mode — webhooks require an HTTP server."""
        raise NotImplementedError(
            "Webhooks require a server deployment. "
            "Use AgentMemClient (HTTP) instead of LocalAgentMemClient."
        )
