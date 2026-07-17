"""
LocalLiansClient â€” zero-setup local mode.

Calls the Lians service layer directly against an in-memory (or file-based)
SQLite database.  No server, no Docker, no API key.  Perfect for prototyping,
notebooks, and CI.

Usage::

    from lians import LocalLiansClient
    from datetime import datetime, timezone

    with LocalLiansClient() as mem:
        mem.add(
            agent_id="research",
            content="NVDA Q3 guidance raised to $36B",
            event_time=datetime(2026, 5, 10, tzinfo=timezone.utc),
            metadata={"ticker": "NVDA", "metric": "guidance"},
        )
        result = mem.recall(agent_id="research", query="NVDA guidance")
        print(result["memories"][0]["content"])

Persistent mode::

    mem = LocalLiansClient(db_path="~/.lians/local.db")

Switching to the hosted API later requires only changing the client class::

    # from lians import LocalLiansClient   # dev
    from lians import LiansClient          # prod
"""
from __future__ import annotations

import asyncio
import os
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Optional

from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession
from sqlalchemy.pool import StaticPool


def _ensure_src_importable() -> None:
    """
    Make ``from src.lians.xxx import ...`` resolve in both environments:

    1. **Monorepo checkout** — this file lives at
       ``<pkg_root>/sdk/python/lians/local_client.py``, so ``parents[3]`` is
       the agentmem root that contains ``src/lians``; add it to sys.path.
    2. **Installed wheel** — the engine ships inside the wheel as
       ``lians_engine.lians`` (see pyproject force-include). Alias it to
       ``src.lians`` in sys.modules so the service-layer imports above
       resolve identically. The engine's own imports are all relative, so
       the alias is the only indirection needed.

    A plain ``pip install lians-sdk`` (no ``[local]`` extra) leaves neither
    available; LocalLiansClient then raises a clear error on first use.
    """
    import sys as _sys
    pkg_root = Path(__file__).resolve().parents[3]
    if (pkg_root / "src" / "lians").is_dir():
        if str(pkg_root) not in _sys.path:
            _sys.path.insert(0, str(pkg_root))
        return

    try:
        import lians_engine.lians as _engine
    except ModuleNotFoundError:
        return  # HTTP-only install; the local engine was never shipped/needed

    import types
    shim = _sys.modules.get("src")
    if shim is None:
        shim = types.ModuleType("src")
        shim.__path__ = []  # mark as package so submodule imports are legal
        _sys.modules["src"] = shim
    _sys.modules.setdefault("src.lians", _engine)
    if not hasattr(shim, "lians"):
        shim.lians = _engine


_ensure_src_importable()


class LocalLiansClient:
    """
    Synchronous Lians client backed by local SQLite â€” no server required.

    Parameters
    ----------
    db_path:
        Path to a SQLite file for persistent storage.  ``None`` (default) uses
        an in-memory database that is discarded when the client is closed.
    namespace:
        Logical tenant namespace.  Useful when sharing one DB file across
        multiple projects.  Defaults to ``"local"``.
    embedding_provider:
        Override the embedding provider (``"sentence-transformers"`` |
        ``"local"`` | ``"voyage"`` | ``"openai"``). When omitted, uses the
        real local model (``sentence-transformers``) if it is installed and
        falls back to the deterministic test stub (``"local"``) otherwise.
        Pass ``"local"`` explicitly for the zero-model test stub.
    """

    def __init__(
        self,
        db_path: Optional[str] = None,
        namespace: str = "local",
        embedding_provider: Optional[str] = None,
    ):
        self._namespace = namespace
        self._loop = asyncio.new_event_loop()

        if embedding_provider is None:
            # Defaulting to the test stub cost real users 24%-grade recall
            # (LOCOMO: stub 24% vs real local model 82% evidence hit@10);
            # prefer the real model whenever the [local] extra is present.
            try:
                import sentence_transformers  # noqa: F401
                embedding_provider = "sentence-transformers"
            except ImportError:
                import warnings
                warnings.warn(
                    "lians: sentence-transformers is not installed, so local "
                    "mode is using the deterministic TEST-GRADE embedding "
                    "stub. Install lians-sdk[local] for real semantic recall.",
                    stacklevel=2,
                )
                embedding_provider = "local"

        # Point the settings at the local embedding provider before any import
        os.environ.setdefault("EMBEDDING_PROVIDER", embedding_provider)
        # LocalAgentMemClient is a dev/test tool â€” allow running without a real key.
        # Production deployments use AgentMemClient (HTTP) against a server that
        # enforces MASTER_ENCRYPTION_KEY at startup.
        os.environ.setdefault("MASTER_ENCRYPTION_KEY", "")
        os.environ.setdefault("AGENTMEM_ALLOW_UNENCRYPTED", "true")
        # Local mode has no Redis: every cache attempt would burn ~1-2s in
        # connection timeouts per call. Callers can still opt back in.
        os.environ.setdefault("RECALL_CACHE_ENABLED", "false")

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
        from src.lians.models import Base  # lazy import; avoids circular refs
        from src.lians.kms import load_master_key
        # In-process recall caches are keyed by (namespace, agent); a fresh
        # client is a fresh database, so anything cached by a previous client
        # in this process must not leak into it.
        from src.lians.session_cache import clear_all
        clear_all()

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

    def __enter__(self) -> "LocalLiansClient":
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
        from src.lians.schemas import MemoryAdd
        from src.lians.memory_service import add_memory
        req = MemoryAdd(**kwargs)
        async with self._session_factory() as db:
            result = await add_memory(db, self._namespace, req)
        return result.model_dump(mode="json")

    def add_batch(self, agent_id: str, items: list[dict]) -> list[dict]:
        """Add many memories in one call, embedding all contents in a single
        batched model pass (10-20x faster than per-item ``add`` on local
        models). Each item takes the same keys as ``add`` minus ``agent_id``.
        Writes are applied in order; returns the created MemoryOut dicts."""
        return self._run(self._async_add_batch(agent_id, items))

    async def _async_add_batch(self, agent_id: str, items: list[dict]) -> list[dict]:
        from src.lians.schemas import MemoryAdd
        from src.lians.memory_service import add_memory
        from src.lians.embeddings import get_embedding_provider
        provider = get_embedding_provider()
        embeddings = await provider.embed([it["content"] for it in items])
        out = []
        async with self._session_factory() as db:
            for it, emb in zip(items, embeddings):
                req = MemoryAdd(agent_id=agent_id, **it)
                result = await add_memory(
                    db, self._namespace, req, precomputed_embedding=emb
                )
                out.append(result.model_dump(mode="json"))
        return out

    def recall(
        self,
        agent_id: str,
        query: str,
        k: int = 5,
        as_of: Optional[datetime] = None,
        filters: Optional[dict[str, Any]] = None,
        include_context: bool = False,
    ) -> dict:
        """Recall memories. Returns RecallResult as a dict.

        ``include_context=True`` attaches each hit's temporally-adjacent
        neighbors as ``context_before``/``context_after`` — the other half of
        an exchange, for consumers that feed memories to an LLM.
        """
        return self._run(self._async_recall(
            agent_id=agent_id, query=query, k=k, as_of=as_of,
            filters=filters or {}, include_context=include_context,
        ))

    async def _async_recall(self, **kwargs) -> dict:
        from src.lians.schemas import RecallRequest
        from src.lians.memory_service import recall_memories
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
        from src.lians.audit import reconstruct
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
        from src.lians.memory_service import erase_subject
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
        from src.lians.audit_chain import export_audit_log
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
        from src.lians.audit_chain import verify_chain
        async with self._session_factory() as db:
            return await verify_chain(db, namespace=self._namespace)

    # â”€â”€ Batch write â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

    def batch_add(self, memories: list[dict]) -> dict:
        """
        Add multiple memories in a single call.

        Items are processed sequentially so a later item can supersede an earlier
        one within the same batch.  Returns a MemoryBatchResult dict.
        """
        return self._run(self._async_batch_add(memories))

    async def _async_batch_add(self, memories: list[dict]) -> dict:
        from src.lians.schemas import MemoryAdd
        from src.lians.memory_service import batch_add_memories
        items = [MemoryAdd(**m) for m in memories]
        async with self._session_factory() as db:
            result = await batch_add_memories(db, self._namespace, items)
        return result.model_dump(mode="json")

    def add_from_messages(
        self,
        agent_id: str,
        messages: list[dict[str, Any]],
        event_time: Optional[datetime] = None,
        source: Optional[str] = "conversation",
        subject_id: Optional[str] = None,
        metadata: Optional[dict[str, Any]] = None,
        importance: float = 0.5,
        roles: Optional[list[str]] = None,
        distill: bool = False,
    ) -> dict:
        """
        Extract and store facts from a conversation message list.

        Accepts the standard OpenAI / LangChain messages format:
        ``[{"role": "user", "content": "..."}, {"role": "assistant", "content": "..."}]``

        Each message whose role matches *roles* (default: ``["assistant"]``;
        with ``distill=True``: user and assistant) is stored as a separate
        memory with supersession and audit-chain writes applied.

        ``distill=True`` additionally runs LLM fact distillation over the
        whole transcript (``src.lians.enrichment``): every atomic, dated,
        speaker-attributed fact is stored as a derived memory
        (``metadata.derived = true``) alongside the raw messages. Recall then
        surfaces dense facts and raw evidence together, and — unlike
        extract-only memory layers — the verbatim transcript stays in the
        bitemporal log as provenance for every derived fact. Opt-in: it puts
        an LLM call in the ingest path (``OPENAI_API_KEY``, model via
        ``LIANS_DISTILL_MODEL``) with its cost, latency, and
        non-determinism; the default path stays LLM-free.

        Parameters
        ----------
        messages:
            List of ``{"role": str, "content": str}`` dicts.
        event_time:
            Timestamp for all extracted memories. Defaults to now().
        roles:
            Roles to extract from. Defaults to ``["assistant"]``
            (``["user", "assistant"]`` when ``distill=True``, so derived
            facts always have their raw provenance stored).
        distill:
            Also store LLM-distilled fact memories for the transcript.
        source, subject_id, metadata, importance:
            Same as ``add()``.

        Returns
        -------
        MemoryBatchResult dict: ``{"added": N, "memories": [...]}``.
        """
        from datetime import timezone as _tz
        if roles is not None:
            _roles = set(roles)
        else:
            _roles = {"user", "assistant"} if distill else {"assistant"}
        _event_time = event_time or datetime.now(_tz.utc)
        _meta_base = dict(metadata or {})

        batch = []
        for i, msg in enumerate(messages):
            role = (msg.get("role") or "").lower()
            content = (msg.get("content") or "").strip()
            if role not in _roles or not content:
                continue
            batch.append({
                "agent_id":   agent_id,
                "content":    content,
                "event_time": _event_time.isoformat(),
                "source":     source,
                "subject_id": subject_id,
                "metadata":   {**_meta_base, "role": role, "message_index": i},
                "importance": importance,
            })

        if distill:
            transcript = "\n".join(
                f"{(m.get('role') or 'user')}: {(m.get('content') or '').strip()}"
                for m in messages if (m.get("content") or "").strip()
            )
            if transcript:
                from src.lians.enrichment import distill_batch
                facts = self._run(distill_batch(
                    transcript, _event_time.strftime("%d %B, %Y")))
                for j, fact in enumerate(facts):
                    batch.append({
                        "agent_id":   agent_id,
                        "content":    fact,
                        # facts sort just after their transcript's raw turns
                        "event_time": (_event_time + timedelta(
                            seconds=len(messages) + 1 + j)).isoformat(),
                        "source":     f"{source}:distilled" if source else "distilled",
                        "subject_id": subject_id,
                        "metadata":   {**_meta_base, "derived": True,
                                       "distilled": True},
                        "importance": importance,
                    })

        if not batch:
            return {"added": 0, "memories": []}
        return self.batch_add(batch)

    # â”€â”€ Point-in-time convenience â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

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
        call site â€” use for audit questions rather than present-time queries.
        """
        return self.recall(agent_id=agent_id, query=query, k=k, as_of=as_of, filters=filters)

    # â”€â”€ Supersession review â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

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
        from src.lians.memory_service import get_pending_supersessions
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
        Reject a supersession â€” the engine was wrong.

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
        from src.lians.schemas import SupersessionAction
        from src.lians.memory_service import apply_supersession_action
        body = SupersessionAction(action=action, reviewer_note=reviewer_note)
        async with self._session_factory() as db:
            result = await apply_supersession_action(
                db, self._namespace, UUID(memory_id), body
            )
        return result.model_dump(mode="json")

    # â”€â”€ Snapshot / compliance â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

    def snapshot(
        self,
        agent_id: str,
        as_of: datetime,
        limit: int = 1000,
    ) -> dict:
        """
        Return agent's complete knowledge state at *as_of* (exhaustive, no ANN).

        Unlike ``recall()``, this is not ranked â€” it returns every memory that
        was valid at that instant, ordered by event_time ascending.  Use for
        compliance audit: "what did the agent know on date X?"
        """
        return self._run(self._async_snapshot(agent_id=agent_id, as_of=as_of, limit=limit))

    async def _async_snapshot(self, agent_id: str, as_of: datetime, limit: int) -> dict:
        from src.lians.memory_service import get_knowledge_snapshot
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
        from src.lians.backtest import check_contamination
        from src.lians.schemas import ContaminationFlagOut, ContaminationReportOut
        async with self._session_factory() as db:
            report = await check_contamination(db, self._namespace, agent_id, simulation_as_of)
        # check_contamination returns a plain dataclass; project it onto the
        # pydantic schema so the dict shape matches the HTTP API exactly.
        out = ContaminationReportOut(
            agent_id=report.agent_id,
            namespace=report.namespace,
            simulation_as_of=report.simulation_as_of,
            memories_checked=report.memories_checked,
            flags=[
                ContaminationFlagOut(
                    memory_id=f.memory_id,
                    event_time=f.event_time,
                    ingestion_time=f.ingestion_time,
                    contamination_type=f.contamination_type,
                    delta_days=f.delta_days,
                    content_preview=f.content_preview,
                    source=f.source,
                    metadata=f.metadata,
                )
                for f in report.flags
            ],
            contamination_rate=report.contamination_rate,
            is_clean=report.is_clean,
        )
        return out.model_dump(mode="json")

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
        from src.lians.memory_service import list_conflicts
        async with self._session_factory() as db:
            result = await list_conflicts(db, self._namespace, status=status, limit=limit)
        return result.model_dump(mode="json")

    def memory_lineage(self, memory_id: str) -> dict:
        """Return every version in a memory's supersession lineage."""
        return self._run(self._async_memory_lineage(memory_id))

    async def _async_memory_lineage(self, memory_id: str) -> dict:
        from uuid import UUID
        from src.lians.memory_service import get_memory_lineage
        async with self._session_factory() as db:
            result = await get_memory_lineage(db, self._namespace, UUID(memory_id))
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
        from src.lians.schemas import ConflictResolveRequest
        from src.lians.memory_service import resolve_conflict
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

        Shows the full history of revisions â€” useful for understanding how a
        structured fact evolved over time (e.g., NVDA guidance across quarters).
        """
        return self._run(self._async_fact_history(
            agent_id=agent_id, ticker=ticker, metric=metric, limit=limit,
        ))

    async def _async_fact_history(
        self, agent_id: str, ticker: str, metric: str, limit: int
    ) -> list[dict]:
        from src.lians.adapters.finance import FinanceAdapter
        adapter = FinanceAdapter()
        async with self._session_factory() as db:
            items = await adapter.fact_history(db, self._namespace, agent_id, ticker, metric, limit)
        return [m.model_dump(mode="json") for m in items]

    def erasure_certificate(self, subject_id: str) -> dict:
        """
        Return a verifiable erasure certificate for a data subject.

        The certificate includes SHA-256 content hashes of erased memories
        and the audit chain status â€” suitable for regulatory filing.
        """
        return self._run(self._async_erasure_certificate(subject_id=subject_id))

    async def _async_erasure_certificate(self, subject_id: str) -> dict:
        from src.lians.memory_service import get_erasure_certificate
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

    # â”€â”€ Relationship graph â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

    def relate(
        self,
        agent_id: str,
        src_entity: str,
        rel_type: str,
        dst_entity: str,
        event_time: datetime,
        exclusive: bool = False,
        subject_id: Optional[str] = None,
        source: Optional[str] = None,
        metadata: Optional[dict[str, Any]] = None,
        normalize: bool = False,
    ) -> dict:
        """Assert a relationship edge: ``src_entity --rel_type--> dst_entity``."""
        return self._run(self._async_relate(
            agent_id=agent_id, src_entity=src_entity, rel_type=rel_type,
            dst_entity=dst_entity, event_time=event_time, exclusive=exclusive,
            subject_id=subject_id, source=source, metadata=metadata or {},
            normalize=normalize,
        ))

    async def _async_relate(self, **kwargs) -> dict:
        from src.lians import graph_service
        async with self._session_factory() as db:
            edge = await graph_service.relate(db, self._namespace, **kwargs)
            return {
                "id": str(edge.id),
                "src_entity": edge.src_entity,
                "rel_type": edge.rel_type,
                "dst_entity": edge.dst_entity,
                "event_time": edge.event_time.isoformat(),
                "valid_to": edge.valid_to.isoformat() if edge.valid_to else None,
            }

    def unrelate(
        self,
        agent_id: str,
        src_entity: str,
        rel_type: str,
        dst_entity: str,
        event_time: Optional[datetime] = None,
        normalize: bool = False,
    ) -> dict:
        """Invalidate a live edge (sets ``valid_to``). Returns ``{"invalidated": N}``."""
        return self._run(self._async_unrelate(
            agent_id=agent_id, src_entity=src_entity, rel_type=rel_type,
            dst_entity=dst_entity, event_time=event_time, normalize=normalize,
        ))

    async def _async_unrelate(self, **kwargs) -> dict:
        from src.lians import graph_service
        async with self._session_factory() as db:
            count = await graph_service.unrelate(db, self._namespace, **kwargs)
            return {"invalidated": count}

    def neighbors(
        self,
        agent_id: str,
        entity: str,
        depth: int = 1,
        as_of: Optional[datetime] = None,
        rel_types: Optional[list[str]] = None,
        direction: str = "any",
        normalize: bool = False,
    ) -> dict:
        """Entities within ``depth`` hops of ``entity`` (optional point-in-time ``as_of``)."""
        return self._run(self._async_neighbors(
            agent_id=agent_id, entity=entity, depth=depth, as_of=as_of,
            rel_types=rel_types, direction=direction, normalize=normalize,
        ))

    async def _async_neighbors(self, **kwargs) -> dict:
        from src.lians import graph_service
        async with self._session_factory() as db:
            return await graph_service.neighbors(db, self._namespace, **kwargs)

    def path(
        self,
        agent_id: str,
        src_entity: str,
        dst_entity: str,
        max_depth: int = 4,
        as_of: Optional[datetime] = None,
        rel_types: Optional[list[str]] = None,
        normalize: bool = False,
    ) -> dict:
        """
        Shortest connection between two entities â€” the conflict-of-interest /
        related-party reachability query. ``{"connected": False}`` is the clean result.
        """
        return self._run(self._async_path(
            agent_id=agent_id, src_entity=src_entity, dst_entity=dst_entity,
            max_depth=max_depth, as_of=as_of, rel_types=rel_types, normalize=normalize,
        ))

    async def _async_path(self, **kwargs) -> dict:
        from src.lians import graph_service
        async with self._session_factory() as db:
            return await graph_service.path(db, self._namespace, **kwargs)

    def recall_near(
        self,
        agent_id: str,
        query: str,
        near_entity: str,
        near_key: str = "ticker",
        k: int = 5,
        as_of: Optional[datetime] = None,
        filters: Optional[dict[str, Any]] = None,
    ) -> dict:
        """
        Recall with graph-proximity reranking: results about entities near
        ``near_entity`` in the relationship graph are boosted. ``near_key`` is the
        metadata field holding each memory's entity (default ``ticker``).
        """
        merged = dict(filters or {})
        merged["_near_entity"] = near_entity
        merged["_near_key"] = near_key
        return self.recall(agent_id=agent_id, query=query, k=k, as_of=as_of, filters=merged)

    def register_webhook(self, *args: Any, **kwargs: Any) -> dict:  # noqa: ARG002
        """Not available in local mode â€” webhooks require an HTTP server."""
        raise NotImplementedError(
            "Webhooks require a server deployment. "
            "Use AgentMemClient (HTTP) instead of LocalAgentMemClient."
        )

    def list_webhooks(self, *args: Any, **kwargs: Any) -> list:  # noqa: ARG002
        """Not available in local mode â€” webhooks require an HTTP server."""
        raise NotImplementedError(
            "Webhooks require a server deployment. "
            "Use AgentMemClient (HTTP) instead of LocalAgentMemClient."
        )

    def update_webhook(self, *args: Any, **kwargs: Any) -> dict:  # noqa: ARG002
        """Not available in local mode â€” webhooks require an HTTP server."""
        raise NotImplementedError(
            "Webhooks require a server deployment. "
            "Use AgentMemClient (HTTP) instead of LocalAgentMemClient."
        )

    def delete_webhook(self, *args: Any, **kwargs: Any) -> None:  # noqa: ARG002
        """Not available in local mode â€” webhooks require an HTTP server."""
        raise NotImplementedError(
            "Webhooks require a server deployment. "
            "Use AgentMemClient (HTTP) instead of LocalAgentMemClient."
        )

    def webhook_deliveries(self, *args: Any, **kwargs: Any) -> list:  # noqa: ARG002
        """Not available in local mode â€” webhooks require an HTTP server."""
        raise NotImplementedError(
            "Webhooks require a server deployment. "
            "Use AgentMemClient (HTTP) instead of LocalAgentMemClient."
        )
