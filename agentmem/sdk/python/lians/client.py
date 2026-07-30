"""
Lians Python SDK — async HTTP client for the REST API.
"""
from __future__ import annotations
import asyncio
import random
from datetime import datetime
from typing import Any, Optional
import httpx


class AsyncLiansClient:
    """
    Async HTTP client for the Lians REST API.

    Parameters
    ----------
    base_url:
        Server base URL, e.g. ``"https://agentmem.example.com"``.
    api_key:
        Namespace-scoped API key (``X-API-Key`` header).
    admin_secret:
        Admin secret for compliance/admin endpoints (``X-Admin-Secret`` header).
        Only required when calling ``audit_export`` or ``verify_chain``.
    timeout:
        HTTP request timeout in seconds.
    """

    def __init__(
        self,
        base_url: str = "http://localhost:8000",
        api_key: str = "",
        admin_secret: str = "",
        timeout: float = 30.0,
        max_retries: int = 2,
        backoff_factor: float = 0.5,
    ):
        self._base = base_url.rstrip("/")
        self._headers = {"X-API-Key": api_key, "Content-Type": "application/json"}
        self._admin_headers = {"X-Admin-Secret": admin_secret, "Content-Type": "application/json"}
        self._timeout = timeout
        self._max_retries = max_retries
        self._backoff_factor = backoff_factor

    # ── Internal ──────────────────────────────────────────────────────────────

    async def _sleep_backoff(self, attempt: int) -> None:
        # Exponential backoff with jitter: base * 2^attempt * (1 .. 1.25)
        delay = self._backoff_factor * (2 ** attempt) * (1.0 + random.random() * 0.25)
        await asyncio.sleep(delay)

    async def _req(
        self,
        method: str,
        path: str,
        *,
        json: Any = None,
        params: Optional[dict] = None,
        admin: bool = False,
        extra_headers: Optional[dict] = None,
    ) -> dict:
        headers = dict(self._admin_headers if admin else self._headers)
        if extra_headers:
            headers.update(extra_headers)
        url = f"{self._base}{path}"
        clean_params = {k: v for k, v in (params or {}).items() if v is not None}

        attempt = 0
        while True:
            try:
                async with httpx.AsyncClient(timeout=self._timeout) as client:
                    resp = await client.request(
                        method, url, headers=headers, json=json, params=clean_params,
                    )
            except httpx.TransportError:
                # Connection/read/timeout error — safe to retry (writes carry an
                # Idempotency-Key, so a retried POST won't double-write).
                if attempt >= self._max_retries:
                    raise
                await self._sleep_backoff(attempt)
                attempt += 1
                continue

            # Retry transient server errors / throttling.
            if resp.status_code >= 500 or resp.status_code == 429:
                if attempt < self._max_retries:
                    await self._sleep_backoff(attempt)
                    attempt += 1
                    continue

            resp.raise_for_status()
            return resp.json()

    # ── Write ─────────────────────────────────────────────────────────────────

    async def add(
        self,
        agent_id: str,
        content: str,
        event_time: datetime,
        source: Optional[str] = None,
        subject_id: Optional[str] = None,
        metadata: Optional[dict[str, Any]] = None,
        importance: float = 0.5,
        idempotency_key: Optional[str] = None,
    ) -> dict:
        """
        Store a financial fact.  Returns the created MemoryOut as a dict.

        A stable ``Idempotency-Key`` is generated automatically (or pass your own)
        so that an automatic retry after a network blip cannot create a duplicate.
        """
        import uuid as _uuid
        key = idempotency_key or str(_uuid.uuid4())
        return await self._req("POST", "/v1/memories", json={
            "agent_id": agent_id,
            "content": content,
            "event_time": event_time.isoformat(),
            "source": source,
            "subject_id": subject_id,
            "metadata": metadata or {},
            "importance": importance,
        }, extra_headers={"Idempotency-Key": key})

    async def batch_add(self, memories: list[dict[str, Any]]) -> dict:
        """
        Add multiple memories in a single request.

        Each item in *memories* is a dict with the same keys as ``add()``.
        Items are processed sequentially so a later item can supersede an earlier
        one within the same batch (useful when loading a time-series of revisions).

        Returns a MemoryBatchResult dict with ``added`` count and ``memories`` list.
        """
        serialized = []
        for m in memories:
            row = dict(m)
            if isinstance(row.get("event_time"), datetime):
                row["event_time"] = row["event_time"].isoformat()
            serialized.append(row)
        return await self._req("POST", "/v1/memories/batch", json={"memories": serialized})

    async def add_from_messages(
        self,
        agent_id: str,
        messages: list[dict[str, Any]],
        event_time: Optional[datetime] = None,
        source: Optional[str] = "conversation",
        subject_id: Optional[str] = None,
        metadata: Optional[dict[str, Any]] = None,
        importance: float = 0.5,
        roles: Optional[list[str]] = None,
    ) -> dict:
        """
        Extract and store facts from a conversation message list.

        Accepts the standard OpenAI / LangChain messages format:
        ``[{"role": "user", "content": "..."}, {"role": "assistant", "content": "..."}]``

        Each message whose role matches *roles* (default: ``["assistant"]``) is
        stored as a separate memory with supersession applied automatically. This
        is the same pattern as ``mem0.add(messages=[...])``, but with bitemporal
        event time, structured supersession, and an audit-chain write per message.

        Parameters
        ----------
        messages:
            List of ``{"role": str, "content": str}`` dicts. Supports ``role``
            values: ``"user"``, ``"assistant"``, ``"system"``, ``"tool"``.
        event_time:
            Timestamp to assign to all extracted memories. Defaults to now().
            Use a past timestamp when replaying historical conversation logs.
        roles:
            Which roles to extract memories from. Defaults to ``["assistant"]``.
            Pass ``["user", "assistant"]`` to store both sides of the conversation.
        source:
            Source label for all extracted memories. Defaults to ``"conversation"``.
        subject_id:
            Data-subject ID (for GDPR crypto-shred targeting — typically the user ID).
        metadata:
            Base metadata dict applied to all extracted memories. Role and message
            index are merged in automatically.
        importance:
            Salience score 0.0–1.0 applied to all extracted memories.

        Returns
        -------
        MemoryBatchResult dict: ``{"added": N, "memories": [...]}``.

        Example
        -------
        ::

            from datetime import datetime, timezone
            messages = [
                {"role": "user",      "content": "What did NVDA say about guidance?"},
                {"role": "assistant", "content": "NVDA raised FY2026 revenue guidance to $40B on Nov 19 2025."},
                {"role": "user",      "content": "And what's the PE?"},
                {"role": "assistant", "content": "NVDA trades at ~35x forward earnings as of June 2026."},
            ]
            result = await client.add_from_messages(
                agent_id="equity-desk",
                messages=messages,
                event_time=datetime(2026, 6, 22, tzinfo=timezone.utc),
                metadata={"ticker": "NVDA"},
            )
            # result["added"] == 2  (two assistant turns stored)
        """
        return await self._req("POST", "/v1/memories/messages", json={
            "agent_id": agent_id,
            "messages": messages,
            "event_time": event_time.isoformat() if event_time else None,
            "source": source,
            "subject_id": subject_id,
            "metadata": metadata or {},
            "importance": importance,
            "roles": roles or ["assistant"],
        })

    # ── Read ──────────────────────────────────────────────────────────────────

    async def recall(
        self,
        agent_id: str,
        query: str,
        k: int = 5,
        as_of: Optional[datetime] = None,
        filters: Optional[dict[str, Any]] = None,
        include_context: bool = False,
        strategy: str = "standard",
        max_query_variants: int = 4,
        mode: str = "fast",
        decision_envelope_id: Optional[str] = None,
    ) -> dict:
        """
        Retrieve the most relevant *current* memories for a query.

        Superseded facts are excluded at the database level — only the latest
        valid value is returned.  Pass ``as_of`` for point-in-time recall.
        """
        return await self._req("POST", "/v1/recall", json={
            "agent_id": agent_id,
            "query": query,
            "k": k,
            "as_of": as_of.isoformat() if as_of else None,
            "filters": filters or {},
            "include_context": include_context,
            "strategy": strategy,
            "max_query_variants": max_query_variants,
            "mode": mode,
            "decision_envelope_id": decision_envelope_id,
        })

    async def context(
        self,
        agent_id: str,
        query: str,
        k: int = 10,
        as_of: Optional[datetime] = None,
        max_tokens: int = 1500,
        header: Optional[str] = None,
        mmr: bool = False,
        surface_conflicts: bool = True,
        max_conflicts: int = 5,
        strategy: str = "adaptive",
        max_query_variants: int = 4,
        mode: str = "deep",
        decision_envelope_id: Optional[str] = None,
    ) -> dict:
        """
        Build a token-budgeted, ready-to-inject context block from recall.

        Returns ``{context, memories, token_estimate, truncated}``. The block is
        bitemporal — never contains stale facts. Pass ``as_of`` for point-in-time
        context and ``mmr=True`` for diversity reranking. Open conflicts ride at
        the top of the block until adjudicated; ``surface_conflicts=False`` opts
        out per call and ``max_conflicts`` bounds them (overflow is an explicit
        "+N more" line).
        """
        body: dict[str, Any] = {
            "agent_id": agent_id, "query": query, "k": k,
            "max_tokens": max_tokens, "mmr": mmr,
            "surface_conflicts": surface_conflicts, "max_conflicts": max_conflicts,
            "strategy": strategy, "max_query_variants": max_query_variants,
            "mode": mode,
            "decision_envelope_id": decision_envelope_id,
        }
        if as_of:
            body["as_of"] = as_of.isoformat()
        if header:
            body["header"] = header
        return await self._req("POST", "/v1/context", json=body)

    async def recall_at(
        self,
        agent_id: str,
        query: str,
        as_of: datetime,
        k: int = 5,
        filters: Optional[dict[str, Any]] = None,
    ) -> dict:
        """
        Convenience wrapper: recall memories that were valid at *as_of*.

        Use for audit queries: *"What guidance did we have on 2026-03-01?"*
        mem0 has no bitemporal model. Graphiti/Zep has temporal graph queries but
        no compliance audit stack (hash chain, crypto-shred, information barriers).
        """
        return await self.recall(agent_id=agent_id, query=query, k=k, as_of=as_of, filters=filters)

    async def feedback(self, memory_id: str, *, agent_id: str, signal: str,
                       weight: float = 1.0, outcome: Optional[str] = None,
                       query: Optional[str] = None, source: Optional[str] = None,
                       note: Optional[str] = None) -> dict:
        """Record whether a recalled memory helped, was wrong, or is stale."""
        return await self._req("POST", f"/v1/memories/{memory_id}/feedback", json={
            "agent_id": agent_id, "signal": signal, "weight": weight,
            "outcome": outcome, "query": query, "source": source, "note": note,
        })

    async def learning_summary(self, agent_id: Optional[str] = None) -> dict:
        return await self._req(
            "GET", "/v1/memory-learning/summary", params={"agent_id": agent_id},
        )

    async def create_experience(
        self,
        *,
        agent_id: str,
        task: str,
        decision: dict[str, Any],
        context_memory_ids: list[str],
        metadata: Optional[dict[str, Any]] = None,
    ) -> dict:
        """Record a decision episode before its outcome is known."""
        return await self._req("POST", "/v1/experiences", json={
            "agent_id": agent_id,
            "task": task,
            "decision": decision,
            "context_memory_ids": context_memory_ids,
            "metadata": metadata or {},
        })

    async def record_experience_outcome(
        self,
        experience_id: str,
        *,
        outcome: dict[str, Any],
        reward: float,
        reviewer_feedback: Optional[str] = None,
    ) -> dict:
        """Attach a reviewed outcome that may influence future context ranking."""
        return await self._req(
            "PATCH",
            f"/v1/experiences/{experience_id}/outcome",
            json={
                "outcome": outcome,
                "reward": reward,
                "reviewer_feedback": reviewer_feedback,
            },
        )

    async def list_experiences(
        self,
        *,
        agent_id: Optional[str] = None,
        status: Optional[str] = None,
        limit: int = 100,
    ) -> dict:
        return await self._req("GET", "/v1/experiences", params={
            "agent_id": agent_id,
            "status": status,
            "limit": limit,
        })

    async def generate_reflections(
        self,
        *,
        agent_id: str,
        minimum_support: int = 2,
        minimum_reward: float = 0.6,
    ) -> dict:
        return await self._req("POST", "/v1/reflections/generate", json={
            "agent_id": agent_id,
            "minimum_support": minimum_support,
            "minimum_reward": minimum_reward,
        })

    async def list_reflections(self, status: str = "pending") -> dict:
        return await self._req(
            "GET",
            "/v1/reflections",
            params={"status": status},
        )

    async def review_reflection(
        self,
        proposal_id: str,
        *,
        action: str,
        reviewer: str,
        note: Optional[str] = None,
    ) -> dict:
        return await self._req(
            "PATCH",
            f"/v1/reflections/{proposal_id}",
            json={"action": action, "reviewer": reviewer, "note": note},
        )

    async def resolve_memory_review(
        self, memory_id: str, *, agent_id: str, action: str,
        reviewer: str, note: Optional[str] = None,
        correction: Optional[str] = None,
    ) -> dict:
        return await self._req("POST", f"/v1/memories/{memory_id}/review", json={
            "agent_id": agent_id, "action": action,
            "reviewer": reviewer, "note": note, "correction": correction,
        })

    async def run_learning_maintenance(
        self, *, dry_run: bool = True, min_signals: int = 3,
    ) -> dict:
        return await self._req("POST", "/v1/memory-learning/maintenance", params={
            "dry_run": dry_run, "min_signals": min_signals,
        })

    async def reconstruct(
        self,
        agent_id: str,
        as_of: datetime,
        query: Optional[str] = None,
    ) -> dict:
        """
        Reconstruct the full memory state and event trail at *as_of*.

        Returns every memory that was valid at that timestamp plus the
        timestamped, content-hashed event log for regulatory audit submissions.
        """
        params = {"agent_id": agent_id, "as_of": as_of.isoformat()}
        if query:
            params["query"] = query
        return await self._req("GET", "/v1/audit/reconstruct", params=params)

    # ── Compliance / Erasure ──────────────────────────────────────────────────

    async def erase(self, subject_id: str, request_ref: str) -> dict:
        """
        GDPR Art. 17 / CCPA crypto-shred.

        Destroys the data subject's per-subject encryption key — all their
        memories become permanently unreadable.  The audit trail (content hashes,
        timestamps) is preserved to prove the erasure occurred.
        """
        return await self._req("POST", "/v1/erase", json={
            "subject_id": subject_id,
            "request_ref": request_ref,
        })

    # ── Supersession review ───────────────────────────────────────────────────

    async def review_supersessions(
        self,
        threshold: Optional[float] = None,
        limit: int = 50,
    ) -> dict:
        """
        Return supersession events whose confidence is below *threshold*.

        In finance a wrong silent supersession — dropping a real number — is a
        compliance failure.  Poll this to surface uncertain events for human review
        before treating the old fact as stale.

        Returns a SupersessionReviewResult dict with an ``items`` list.
        """
        return await self._req("GET", "/v1/supersessions/review", params={
            "threshold": threshold,
            "limit": limit,
        })

    async def confirm_supersession(
        self,
        memory_id: str,
        reviewer_note: Optional[str] = None,
    ) -> dict:
        """
        Confirm that a supersession was correct.

        Writes an immutable audit event with the reviewer's note; the superseded
        memory remains closed.  Returns a SupersessionActionResult dict.
        """
        return await self._req("PATCH", f"/v1/supersessions/{memory_id}", json={
            "action": "confirm",
            "reviewer_note": reviewer_note,
        })

    async def reject_supersession(
        self,
        memory_id: str,
        reviewer_note: Optional[str] = None,
    ) -> dict:
        """
        Reject a supersession — the engine was wrong.

        Restores the old memory as currently valid (``valid_to = NULL``) and
        writes an immutable audit event.  Both memories are now additive.
        Returns a SupersessionActionResult dict.
        """
        return await self._req("PATCH", f"/v1/supersessions/{memory_id}", json={
            "action": "reject",
            "reviewer_note": reviewer_note,
        })

    # ── Admin / Audit chain ───────────────────────────────────────────────────

    async def verify_chain(self, namespace: str) -> dict:
        """
        Verify the SEC 17a-4 tamper-evidence hash chain for *namespace*.

        Returns ``{"status": "ok", "rows_checked": N}`` or
        ``{"status": "tampered", "violations": [...]}`` with details on every
        broken link.  Requires ``admin_secret`` to be set on the client.
        """
        return await self._req(
            "GET", "/v1/admin/audit/verify",
            params={"namespace": namespace},
            admin=True,
        )

    async def audit_export(
        self,
        namespace: str,
        from_dt: Optional[datetime] = None,
        to_dt: Optional[datetime] = None,
        limit: int = 100_000,
        verify: bool = False,
    ) -> dict:
        """
        Export the full audit log for *namespace* (SEC/FINRA/CFTC examiners).

        Pass ``verify=True`` to include a chain-verification report alongside
        the event rows.  Requires ``admin_secret`` to be set on the client.
        """
        return await self._req(
            "GET", "/v1/admin/audit/export",
            params={
                "namespace": namespace,
                "from_": from_dt.isoformat() if from_dt else None,
                "to": to_dt.isoformat() if to_dt else None,
                "limit": limit,
                "verify_chain": verify,
            },
            admin=True,
        )

    # ── Snapshot (audit reconstruction) ───────────────────────────────────────

    async def snapshot(
        self,
        agent_id: str,
        as_of: datetime,
        limit: int = 1000,
    ) -> dict:
        """
        Reconstruct the complete knowledge state of *agent_id* at *as_of*.

        Returns every memory that was valid (``valid_from ≤ as_of < valid_to``)
        at the given timestamp — exhaustive, no relevance filter.

        This is the "audit reconstruction as a product surface" from SCALE.md §4:
        *"Show me the agent's complete knowledge state as of T. One call."*
        The compliance demo that closes deals with risk committees and regulators.
        mem0 has no temporal model.  Graphiti/Zep has temporal graph queries but
        no tamper-evident hash chain or compliance export API.

        Returns a KnowledgeSnapshot dict: ``{agent_id, namespace, as_of, total, items}``.
        """
        return await self._req(
            "GET", "/v1/snapshot",
            params={
                "agent_id": agent_id,
                "as_of": as_of.isoformat(),
                "limit": limit,
            },
        )

    # ── Backtest contamination ─────────────────────────────────────────────────

    async def backtest_check(
        self,
        agent_id: str,
        simulation_as_of: datetime,
    ) -> dict:
        """
        Detect lookahead bias in a backtest simulation.

        Scans the agent's memory store and flags every fact it couldn't have
        known at *simulation_as_of*.  Two contamination types:

        - ``future_event``  — ``event_time > simulation_as_of`` (clear lookahead)
        - ``late_revision`` — ``ingestion_time > simulation_as_of`` but
          ``event_time <= simulation_as_of`` (the revised figure hadn't arrived yet)

        ``is_clean: True`` is the proof a risk committee needs before trusting
        a backtest.  This is the "thin open-sourceable primitive" from SCALE.md §6
        — a differentiator no other memory store provides.

        Returns a ContaminationReport dict:
        ``{is_clean, contamination_rate, memories_checked, flags}``.
        """
        return await self._req("POST", "/v1/backtest/check", json={
            "agent_id": agent_id,
            "simulation_as_of": simulation_as_of.isoformat(),
        })

    # ── Relationship graph ──────────────────────────────────────────────────────

    async def relate(
        self,
        agent_id: str,
        src_entity: str,
        rel_type: str,
        dst_entity: str,
        event_time: datetime,
        exclusive: bool = False,
        subject_id: Optional[str] = None,
        source: Optional[str] = None,
        metadata: Optional[dict] = None,
        normalize: bool = False,
    ) -> dict:
        """Assert a relationship edge ``src_entity --rel_type--> dst_entity``."""
        return await self._req("POST", "/v1/graph/relate", json={
            "agent_id": agent_id, "src_entity": src_entity, "rel_type": rel_type,
            "dst_entity": dst_entity, "event_time": event_time.isoformat(),
            "exclusive": exclusive, "subject_id": subject_id, "source": source,
            "metadata": metadata or {}, "normalize": normalize,
        })

    async def unrelate(
        self,
        agent_id: str,
        src_entity: str,
        rel_type: str,
        dst_entity: str,
        event_time: Optional[datetime] = None,
        normalize: bool = False,
    ) -> dict:
        """Invalidate a live edge (sets ``valid_to``). Returns ``{"invalidated": N}``."""
        return await self._req("POST", "/v1/graph/unrelate", json={
            "agent_id": agent_id, "src_entity": src_entity, "rel_type": rel_type,
            "dst_entity": dst_entity,
            "event_time": event_time.isoformat() if event_time else None,
            "normalize": normalize,
        })

    async def neighbors(
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
        return await self._req("GET", "/v1/graph/neighbors", params={
            "entity": entity, "agent_id": agent_id, "depth": depth,
            "direction": direction, "normalize": normalize,
            "as_of": as_of.isoformat() if as_of else None,
            "rel_type": rel_types,
        })

    async def path(
        self,
        agent_id: str,
        src_entity: str,
        dst_entity: str,
        max_depth: int = 4,
        as_of: Optional[datetime] = None,
        rel_types: Optional[list[str]] = None,
        normalize: bool = False,
    ) -> dict:
        """Shortest connection between two entities — the COI / related-party query."""
        return await self._req("GET", "/v1/graph/path", params={
            "src": src_entity, "dst": dst_entity, "agent_id": agent_id,
            "max_depth": max_depth, "normalize": normalize,
            "as_of": as_of.isoformat() if as_of else None,
            "rel_type": rel_types,
        })

    async def recall_near(
        self,
        agent_id: str,
        query: str,
        near_entity: str,
        near_key: str = "ticker",
        k: int = 5,
        as_of: Optional[datetime] = None,
        filters: Optional[dict] = None,
    ) -> dict:
        """Recall with graph-proximity reranking around ``near_entity``."""
        merged = dict(filters or {})
        merged["_near_entity"] = near_entity
        merged["_near_key"] = near_key
        return await self.recall(agent_id=agent_id, query=query, k=k, as_of=as_of, filters=merged)

    # ── Conflicts ──────────────────────────────────────────────────────────────

    async def list_conflicts(
        self,
        status: Optional[str] = "open",
        limit: int = 50,
    ) -> dict:
        """
        List same-time fact contradictions detected by the supersession engine.

        Two sources reporting different values for the same structured fact
        (same ticker/metric) at the same event_time generate a conflict flag.
        Both memories remain valid until a human resolves the flag.

        *status* filters by resolution state: ``"open"`` (default), ``"accept_a"``,
        ``"accept_b"``, or ``"dismissed"``.

        Returns a ConflictListResult dict: ``{conflicts, total, status_filter}``.
        """
        return await self._req("GET", "/v1/conflicts", params={"status": status, "limit": limit})

    async def resolve_conflict(
        self,
        conflict_id: str,
        resolution: str,
        note: Optional[str] = None,
    ) -> dict:
        """
        Resolve a conflict flag.

        *resolution* must be one of:

        - ``"accept_a"`` — the pre-existing memory (A) is authoritative; B is invalidated
        - ``"accept_b"`` — the newly-ingested memory (B) is authoritative; A is invalidated
        - ``"dismiss"``  — both memories remain live (sources legitimately differ)

        Every resolution writes an immutable ``conflict_resolved`` event to the
        audit chain.  Returns a ConflictResolveResult dict.
        """
        return await self._req(
            "POST", f"/v1/conflicts/{conflict_id}/resolve",
            json={"resolution": resolution, "note": note},
        )

    # ── Fact history ───────────────────────────────────────────────────────────

    async def fact_history(
        self,
        agent_id: str,
        ticker: str,
        metric: str,
        limit: int = 100,
    ) -> dict:
        """
        Return every recorded version of a structured fact ordered by event_time.

        Query by *ticker* + *metric* instead of a memory_id — ideal for time-series
        views such as *"show me how AAPL EPS evolved over the last four quarters"*.
        Superseded versions are included so analysts can see the full revision history.

        Entity normalization is automatic: ``"Apple Inc."``, ``"US0378331005"``
        (ISIN), ``"037833100"`` (CUSIP), and ``"AAPL"`` all resolve to the same series.

        Returns a FactHistoryResult dict: ``{ticker, metric, agent_id, namespace, total, items}``.
        """
        return await self._req("GET", "/v1/facts/history", params={
            "agent_id": agent_id,
            "ticker": ticker,
            "metric": metric,
            "limit": limit,
        })

    # ── Compliance report ──────────────────────────────────────────────────────

    async def compliance_report(
        self,
        from_dt: Optional[datetime] = None,
        to_dt: Optional[datetime] = None,
        verify_chain: bool = False,
    ) -> dict:
        """
        Generate a compliance report for the caller's namespace.

        Covers: memory counts, audit chain status (SEC 17a-4), erasure records,
        open conflicts, supersession statistics, and retention policy snapshot.

        Pass ``verify_chain=True`` to run the hash-chain tamper check (adds ~50 ms
        per 10k events).

        Returns a ComplianceReport dict covering the requested window.
        """
        return await self._req("GET", "/v1/compliance/report", params={
            "from": from_dt.isoformat() if from_dt else None,
            "to": to_dt.isoformat() if to_dt else None,
            "verify": verify_chain,
        })

    async def record_decision(
        self,
        *,
        agent_id: str,
        decision_type: str,
        outcome: str,
        decided_at: datetime,
        reason_codes: Optional[list[str]] = None,
        **fields: Any,
    ) -> dict:
        """Append a consequential AI decision to the dispute ledger."""
        return await self._req("POST", "/v1/decisions", json={
            "agent_id": agent_id,
            "decision_type": decision_type,
            "outcome": outcome,
            "decided_at": decided_at.isoformat(),
            "reason_codes": reason_codes or [],
            **fields,
        })

    async def open_decision_envelope(
        self,
        *,
        agent_id: str,
        decision_type: str,
        knowledge_as_of: Optional[datetime] = None,
        completeness_profile: str = "standard",
        **fields: Any,
    ) -> dict:
        """Open the evidence correlation boundary before an agent acts."""
        return await self._req("POST", "/v1/decision-envelopes", json={
            "agent_id": agent_id,
            "decision_type": decision_type,
            "knowledge_as_of": knowledge_as_of.isoformat() if knowledge_as_of else None,
            "completeness_profile": completeness_profile,
            **fields,
        })

    async def decision_envelope(self, envelope_id: str) -> dict:
        return await self._req("GET", f"/v1/decision-envelopes/{envelope_id}")

    async def add_decision_evidence(
        self,
        envelope_id: str,
        evidence: list[dict[str, Any]],
    ) -> list[dict]:
        """Append evidence edges before or after the decision is sealed."""
        normalized = []
        for item in evidence:
            normalized.append({
                key: value.isoformat() if isinstance(value, datetime) else value
                for key, value in item.items()
            })
        return await self._req(
            "POST",
            f"/v1/decision-envelopes/{envelope_id}/evidence",
            json={"evidence": normalized},
        )

    async def seal_decision_envelope(
        self,
        envelope_id: str,
        *,
        outcome: str,
        decided_at: datetime,
        reason_codes: Optional[list[str]] = None,
        **fields: Any,
    ) -> dict:
        """Seal an envelope and return its Recorded-to-Replayable grade."""
        normalized_fields = {
            key: value.isoformat() if isinstance(value, datetime) else value
            for key, value in fields.items()
        }
        return await self._req(
            "POST",
            f"/v1/decision-envelopes/{envelope_id}/seal",
            json={
                "outcome": outcome,
                "decided_at": decided_at.isoformat(),
                "reason_codes": reason_codes or [],
                **normalized_fields,
            },
        )

    async def decision_completeness(self, decision_id: str) -> dict:
        return await self._req(
            "GET", f"/v1/decisions/{decision_id}/completeness"
        )

    async def reconstruct_decision(self, decision_id: str) -> dict:
        return await self._req(
            "GET", f"/v1/decisions/{decision_id}/reconstruction"
        )

    async def blast_radius(
        self,
        *,
        evidence_type: str,
        source_id: str,
        source_version: Optional[str] = None,
        artifact_hash: Optional[str] = None,
        limit: int = 100,
    ) -> dict:
        """Find decisions exposed to a changed source, version, or artifact."""
        return await self._req("GET", "/v1/evidence/blast-radius", params={
            "evidence_type": evidence_type,
            "source_id": source_id,
            "source_version": source_version,
            "artifact_hash": artifact_hash,
            "limit": limit,
        })

    async def record_evidence_change(
        self,
        *,
        evidence_type: str,
        source_id: str,
        change_kind: str,
        changed_at: datetime,
        **fields: Any,
    ) -> dict:
        """Record a source change and return the immediate blast radius."""
        normalized_fields = {
            key: value.isoformat() if isinstance(value, datetime) else value
            for key, value in fields.items()
        }
        return await self._req("POST", "/v1/evidence/changes", json={
            "evidence_type": evidence_type,
            "source_id": source_id,
            "change_kind": change_kind,
            "changed_at": changed_at.isoformat(),
            **normalized_fields,
        })

    async def decisions(self, **filters: Any) -> list[dict]:
        """List decision records, optionally filtered by agent, subject, or regime."""
        return await self._req("GET", "/v1/decisions", params=filters)

    async def review_decision(self, decision_id: str, status: str, reviewer: str,
                              note: Optional[str] = None) -> dict:
        """Record an authenticated human review of a decision."""
        return await self._req("POST", f"/v1/decisions/{decision_id}/review", json={
            "status": status, "reviewer": reviewer, "note": note,
        })

    async def evidence_pack(
        self,
        decision_id: str,
        verify: bool = True,
        version: str = "v1",
    ) -> dict:
        """Export an Evidence Pack; v2 adds completeness and optional Ed25519 signing."""
        return await self._req("GET", f"/v1/decisions/{decision_id}/evidence-pack",
                               params={"verify": verify, "version": version})

    async def record_event(self, event_type: str, agent_id: str, occurred_at: datetime,
                           **fields: Any) -> dict:
        """Append a first-class system-of-record event."""
        return await self._req("POST", "/v1/records/events", json={
            "event_type": event_type, "agent_id": agent_id,
            "occurred_at": occurred_at.isoformat(), **fields,
        })

    async def record_events(self, **filters: Any) -> list[dict]:
        return await self._req("GET", "/v1/records/events", params=filters)

    # ── Erasure certificate ────────────────────────────────────────────────────

    async def erasure_certificate(self, subject_id: str) -> dict:
        """
        Retrieve the cryptographic proof-of-erasure certificate for a data subject.

        The certificate proves: (1) N memories had their encrypted content
        permanently destroyed; (2) SHA-256 content_hashes are preserved — the
        erasure is auditable but the content is irrecoverable; (3) the audit chain
        remained intact after erasure (``chain_status = "ok"``).

        Compliance officers buy proofs, not promises.  This is the proof.

        Returns 404 if no erasure has been recorded for *subject_id*.
        Returns an ErasureCertificate dict: ``{certificate_id, erased_at, memories_erased, ...}``.
        """
        return await self._req("GET", f"/v1/erase/{subject_id}/certificate")

    # ── Webhooks ───────────────────────────────────────────────────────────────

    async def register_webhook(
        self,
        url: str,
        events: list[str],
        secret: Optional[str] = None,
        description: Optional[str] = None,
    ) -> dict:
        """
        Register a webhook endpoint for the caller's namespace.

        Every delivery is HMAC-SHA256-signed with the returned *secret*:
        ``X-AgentMem-Signature: sha256=<hex>``.  Store the secret securely —
        it is returned **exactly once** and cannot be recovered.

        Supported event types:
          ``"memory.superseded"``   — a memory was invalidated by a newer fact
          ``"memory.conflict"``     — same-time contradiction detected
          ``"memory.erased"``       — a subject's DEK was destroyed (GDPR Art. 17)
          ``"supersession.rejected"`` — a human reviewer rejected a supersession

        Returns a WebhookRegisterResult dict: ``{endpoint, secret}``.
        """
        body: dict[str, Any] = {"url": url, "events": events}
        if secret is not None:
            body["secret"] = secret
        if description is not None:
            body["description"] = description
        return await self._req("POST", "/v1/webhooks", json=body)

    async def list_webhooks(self) -> list:
        """List all webhook endpoints registered for the caller's namespace."""
        return await self._req("GET", "/v1/webhooks")  # type: ignore[return-value]

    async def update_webhook(
        self,
        endpoint_id: str,
        enabled: Optional[bool] = None,
        events: Optional[list[str]] = None,
        description: Optional[str] = None,
    ) -> dict:
        """Update an endpoint's enabled state, subscribed events, or description."""
        body: dict[str, Any] = {}
        if enabled is not None:
            body["enabled"] = enabled
        if events is not None:
            body["events"] = events
        if description is not None:
            body["description"] = description
        return await self._req("PATCH", f"/v1/webhooks/{endpoint_id}", json=body)

    async def delete_webhook(self, endpoint_id: str) -> None:
        """Remove a webhook endpoint permanently."""
        await self._req("DELETE", f"/v1/webhooks/{endpoint_id}")

    async def webhook_deliveries(self, endpoint_id: str, limit: int = 50) -> dict:
        """Return recent delivery attempts for a webhook endpoint."""
        return await self._req(
            "GET", f"/v1/webhooks/{endpoint_id}/deliveries",
            params={"limit": limit},
        )
