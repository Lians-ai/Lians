"""
AgentMem Python SDK — async HTTP client for the REST API.
"""
from __future__ import annotations
from datetime import datetime
from typing import Any, Optional
import httpx


class AsyncAgentMemClient:
    """
    Async HTTP client for the AgentMem REST API.

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
    ):
        self._base = base_url.rstrip("/")
        self._headers = {"X-API-Key": api_key, "Content-Type": "application/json"}
        self._admin_headers = {"X-Admin-Secret": admin_secret, "Content-Type": "application/json"}
        self._timeout = timeout

    # ── Internal ──────────────────────────────────────────────────────────────

    async def _req(
        self,
        method: str,
        path: str,
        *,
        json: Any = None,
        params: Optional[dict] = None,
        admin: bool = False,
    ) -> dict:
        headers = self._admin_headers if admin else self._headers
        async with httpx.AsyncClient(timeout=self._timeout) as client:
            resp = await client.request(
                method,
                f"{self._base}{path}",
                headers=headers,
                json=json,
                params={k: v for k, v in (params or {}).items() if v is not None},
            )
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
    ) -> dict:
        """Store a financial fact.  Returns the created MemoryOut as a dict."""
        return await self._req("POST", "/v1/memories", json={
            "agent_id": agent_id,
            "content": content,
            "event_time": event_time.isoformat(),
            "source": source,
            "subject_id": subject_id,
            "metadata": metadata or {},
            "importance": importance,
        })

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

    # ── Read ──────────────────────────────────────────────────────────────────

    async def recall(
        self,
        agent_id: str,
        query: str,
        k: int = 5,
        as_of: Optional[datetime] = None,
        filters: Optional[dict[str, Any]] = None,
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
        })

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

        This is the compliance differentiator — neither mem0 nor Zep support
        this.  Use for audit queries: *"What guidance did we have on 2026-03-01?"*
        """
        return await self.recall(agent_id=agent_id, query=query, k=k, as_of=as_of, filters=filters)

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
        Neither mem0 nor Zep can answer this question.

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
