"""
LiansClient — synchronous wrapper around AsyncLiansClient.

For scripts, CLIs, and any non-async context.  In async code (FastAPI
handlers, Jupyter with a running loop) use AsyncLiansClient directly.

Usage::

    from lians import LiansClient
    from datetime import datetime, timezone

    with LiansClient(base_url="http://localhost:8000", api_key="...") as client:
        client.add(
            agent_id="my-agent",
            content="NVDA guidance $36B",
            event_time=datetime(2026, 5, 10, tzinfo=timezone.utc),
            metadata={"ticker": "NVDA", "metric": "guidance"},
        )
        result = client.recall(agent_id="my-agent", query="NVDA guidance")
        for mem in result["memories"]:
            print(mem["content"])
"""
from __future__ import annotations

import asyncio
from datetime import datetime
from typing import Any, Optional

from .client import AsyncLiansClient


class LiansClient:
    """Synchronous HTTP client for the Lians REST API."""

    def __init__(
        self,
        base_url: str = "http://localhost:8000",
        api_key: str = "",
        admin_secret: str = "",
        timeout: float = 30.0,
    ):
        self._async = AsyncLiansClient(
            base_url=base_url,
            api_key=api_key,
            admin_secret=admin_secret,
            timeout=timeout,
        )
        self._loop = asyncio.new_event_loop()

    # ── Lifecycle ─────────────────────────────────────────────────────────────

    def __enter__(self) -> "LiansClient":
        return self

    def __exit__(self, *_: Any) -> None:
        self.close()

    def close(self) -> None:
        self._loop.close()

    # ── Write ─────────────────────────────────────────────────────────────────

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
        return self._loop.run_until_complete(
            self._async.add(
                agent_id=agent_id,
                content=content,
                event_time=event_time,
                source=source,
                subject_id=subject_id,
                metadata=metadata,
                importance=importance,
            )
        )

    def batch_add(self, memories: list[dict[str, Any]]) -> dict:
        """
        Add multiple memories in a single request.

        Returns a MemoryBatchResult dict with ``added`` count and ``memories`` list.
        Items are processed sequentially so later items can supersede earlier ones.
        """
        return self._loop.run_until_complete(self._async.batch_add(memories))

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
    ) -> dict:
        """
        Extract and store facts from a conversation message list.

        Accepts the standard OpenAI / LangChain messages format:
        ``[{"role": "user", "content": "..."}, {"role": "assistant", "content": "..."}]``

        Each message whose role matches *roles* (default: ``["assistant"]``) is
        stored as a separate memory with full supersession, bitemporal tracking,
        and audit-chain writes — the same pipeline as ``add()``.

        This is the equivalent of ``mem0.add(messages=[...])``, with the addition
        of bitemporal event time and compliance audit writes.

        Parameters
        ----------
        messages:
            List of ``{"role": str, "content": str}`` dicts.
        event_time:
            Timestamp to assign to all extracted memories. Defaults to now().
        roles:
            Roles to extract from. Defaults to ``["assistant"]``.
        source, subject_id, metadata, importance:
            Same as ``add()``.

        Returns
        -------
        MemoryBatchResult dict: ``{"added": N, "memories": [...]}``.
        """
        return self._loop.run_until_complete(
            self._async.add_from_messages(
                agent_id=agent_id,
                messages=messages,
                event_time=event_time,
                source=source,
                subject_id=subject_id,
                metadata=metadata,
                importance=importance,
                roles=roles,
            )
        )

    # ── Read ──────────────────────────────────────────────────────────────────

    def recall(
        self,
        agent_id: str,
        query: str,
        k: int = 5,
        as_of: Optional[datetime] = None,
        filters: Optional[dict[str, Any]] = None,
    ) -> dict:
        """Recall memories. Returns RecallResult as a dict."""
        return self._loop.run_until_complete(
            self._async.recall(
                agent_id=agent_id,
                query=query,
                k=k,
                as_of=as_of,
                filters=filters,
            )
        )

    def context(
        self,
        agent_id: str,
        query: str,
        k: int = 10,
        as_of: Optional[datetime] = None,
        max_tokens: int = 1500,
        header: Optional[str] = None,
        mmr: bool = False,
    ) -> dict:
        """Build a token-budgeted, ready-to-inject context block. Returns a dict
        ``{context, memories, token_estimate, truncated}``."""
        return self._loop.run_until_complete(
            self._async.context(
                agent_id=agent_id, query=query, k=k, as_of=as_of,
                max_tokens=max_tokens, header=header, mmr=mmr,
            )
        )

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
        return self._loop.run_until_complete(
            self._async.recall_at(
                agent_id=agent_id,
                query=query,
                as_of=as_of,
                k=k,
                filters=filters,
            )
        )

    def reconstruct(
        self,
        agent_id: str,
        as_of: datetime,
        query: Optional[str] = None,
    ) -> dict:
        """Audit reconstruction. Returns AuditReconstructResult as a dict."""
        return self._loop.run_until_complete(
            self._async.reconstruct(agent_id=agent_id, as_of=as_of, query=query)
        )

    # ── Compliance ────────────────────────────────────────────────────────────

    def erase(self, subject_id: str, request_ref: str) -> dict:
        """GDPR / CCPA crypto-shred. Returns EraseResult as a dict."""
        return self._loop.run_until_complete(
            self._async.erase(subject_id=subject_id, request_ref=request_ref)
        )

    # ── Supersession review ───────────────────────────────────────────────────

    def review_supersessions(
        self,
        threshold: Optional[float] = None,
        limit: int = 50,
    ) -> dict:
        """
        Return supersession events whose confidence is below *threshold*.

        Returns a SupersessionReviewResult dict with an ``items`` list.
        """
        return self._loop.run_until_complete(
            self._async.review_supersessions(threshold=threshold, limit=limit)
        )

    def confirm_supersession(
        self,
        memory_id: str,
        reviewer_note: Optional[str] = None,
    ) -> dict:
        """Confirm a supersession was correct. Returns SupersessionActionResult."""
        return self._loop.run_until_complete(
            self._async.confirm_supersession(memory_id=memory_id, reviewer_note=reviewer_note)
        )

    def reject_supersession(
        self,
        memory_id: str,
        reviewer_note: Optional[str] = None,
    ) -> dict:
        """Reject a supersession — restores the old memory as valid."""
        return self._loop.run_until_complete(
            self._async.reject_supersession(memory_id=memory_id, reviewer_note=reviewer_note)
        )

    # ── Admin / Audit chain ───────────────────────────────────────────────────

    def verify_chain(self, namespace: str) -> dict:
        """
        Verify the SEC 17a-4 hash chain for *namespace*.

        Returns ``{"status": "ok"}`` or ``{"status": "tampered", "violations": [...]}``
        Requires ``admin_secret`` to be set on the client.
        """
        return self._loop.run_until_complete(self._async.verify_chain(namespace=namespace))

    def audit_export(
        self,
        namespace: str,
        from_dt: Optional[datetime] = None,
        to_dt: Optional[datetime] = None,
        limit: int = 100_000,
        verify: bool = False,
    ) -> dict:
        """
        Export the full audit log for *namespace*.

        Pass ``verify=True`` to include a tamper-evidence chain verification
        report alongside the event rows.  Requires ``admin_secret``.
        """
        return self._loop.run_until_complete(
            self._async.audit_export(
                namespace=namespace,
                from_dt=from_dt,
                to_dt=to_dt,
                limit=limit,
                verify=verify,
            )
        )

    # ── Snapshot (audit reconstruction) ───────────────────────────────────────

    def snapshot(
        self,
        agent_id: str,
        as_of: datetime,
        limit: int = 1000,
    ) -> dict:
        """
        Reconstruct the complete knowledge state of *agent_id* at *as_of*.

        Returns every fact that was valid at that timestamp — exhaustive, no
        relevance filter.  The one-call compliance demo that closes deals with
        risk committees and regulators.

        Returns a KnowledgeSnapshot dict: ``{agent_id, namespace, as_of, total, items}``.
        """
        return self._loop.run_until_complete(
            self._async.snapshot(agent_id=agent_id, as_of=as_of, limit=limit)
        )

    # ── Backtest contamination ─────────────────────────────────────────────────

    def backtest_check(
        self,
        agent_id: str,
        simulation_as_of: datetime,
    ) -> dict:
        """
        Detect lookahead bias in a backtest simulation.

        ``is_clean: True`` is the proof a risk committee needs before trusting
        a backtest result.  Returns a ContaminationReport dict.
        """
        return self._loop.run_until_complete(
            self._async.backtest_check(agent_id=agent_id, simulation_as_of=simulation_as_of)
        )

    # ── Relationship graph ──────────────────────────────────────────────────────

    def relate(self, agent_id, src_entity, rel_type, dst_entity, event_time,
               exclusive=False, subject_id=None, source=None, metadata=None,
               normalize=False) -> dict:
        """Assert a relationship edge ``src_entity --rel_type--> dst_entity``."""
        return self._loop.run_until_complete(self._async.relate(
            agent_id=agent_id, src_entity=src_entity, rel_type=rel_type,
            dst_entity=dst_entity, event_time=event_time, exclusive=exclusive,
            subject_id=subject_id, source=source, metadata=metadata, normalize=normalize,
        ))

    def unrelate(self, agent_id, src_entity, rel_type, dst_entity,
                 event_time=None, normalize=False) -> dict:
        """Invalidate a live edge (sets ``valid_to``)."""
        return self._loop.run_until_complete(self._async.unrelate(
            agent_id=agent_id, src_entity=src_entity, rel_type=rel_type,
            dst_entity=dst_entity, event_time=event_time, normalize=normalize,
        ))

    def neighbors(self, agent_id, entity, depth=1, as_of=None, rel_types=None,
                  direction="any", normalize=False) -> dict:
        """Entities within ``depth`` hops of ``entity`` (optional ``as_of``)."""
        return self._loop.run_until_complete(self._async.neighbors(
            agent_id=agent_id, entity=entity, depth=depth, as_of=as_of,
            rel_types=rel_types, direction=direction, normalize=normalize,
        ))

    def path(self, agent_id, src_entity, dst_entity, max_depth=4, as_of=None,
             rel_types=None, normalize=False) -> dict:
        """Shortest connection between two entities — the COI / related-party query."""
        return self._loop.run_until_complete(self._async.path(
            agent_id=agent_id, src_entity=src_entity, dst_entity=dst_entity,
            max_depth=max_depth, as_of=as_of, rel_types=rel_types, normalize=normalize,
        ))

    def recall_near(self, agent_id, query, near_entity, near_key="ticker",
                    k=5, as_of=None, filters=None) -> dict:
        """Recall with graph-proximity reranking around ``near_entity``."""
        return self._loop.run_until_complete(self._async.recall_near(
            agent_id=agent_id, query=query, near_entity=near_entity,
            near_key=near_key, k=k, as_of=as_of, filters=filters,
        ))

    # ── Conflicts ──────────────────────────────────────────────────────────────

    def list_conflicts(
        self,
        status: Optional[str] = "open",
        limit: int = 50,
    ) -> dict:
        """List detected fact contradictions. Returns ConflictListResult."""
        return self._loop.run_until_complete(
            self._async.list_conflicts(status=status, limit=limit)
        )

    def resolve_conflict(
        self,
        conflict_id: str,
        resolution: str,
        note: Optional[str] = None,
    ) -> dict:
        """
        Resolve a conflict flag.

        *resolution*: ``"accept_a"``, ``"accept_b"``, or ``"dismiss"``.
        Returns ConflictResolveResult.
        """
        return self._loop.run_until_complete(
            self._async.resolve_conflict(conflict_id=conflict_id, resolution=resolution, note=note)
        )

    # ── Fact history ───────────────────────────────────────────────────────────

    def fact_history(
        self,
        agent_id: str,
        ticker: str,
        metric: str,
        limit: int = 100,
    ) -> dict:
        """
        Return all recorded versions of a structured fact ordered by event_time.

        Returns a FactHistoryResult dict: ``{ticker, metric, agent_id, namespace, total, items}``.
        """
        return self._loop.run_until_complete(
            self._async.fact_history(agent_id=agent_id, ticker=ticker, metric=metric, limit=limit)
        )

    # ── Compliance report ──────────────────────────────────────────────────────

    def compliance_report(
        self,
        from_dt: Optional[datetime] = None,
        to_dt: Optional[datetime] = None,
        verify_chain: bool = False,
    ) -> dict:
        """Generate a compliance report for the caller's namespace."""
        return self._loop.run_until_complete(
            self._async.compliance_report(from_dt=from_dt, to_dt=to_dt, verify_chain=verify_chain)
        )

    # ── Erasure certificate ────────────────────────────────────────────────────

    def erasure_certificate(self, subject_id: str) -> dict:
        """
        Retrieve the cryptographic proof-of-erasure certificate.

        Returns an ErasureCertificate dict.  Returns 404 if no erasure recorded.
        """
        return self._loop.run_until_complete(self._async.erasure_certificate(subject_id=subject_id))

    # ── Webhooks ───────────────────────────────────────────────────────────────

    def register_webhook(
        self,
        url: str,
        events: list[str],
        secret: Optional[str] = None,
        description: Optional[str] = None,
    ) -> dict:
        """Register a webhook endpoint. Returns WebhookRegisterResult (secret shown once)."""
        return self._loop.run_until_complete(
            self._async.register_webhook(url=url, events=events, secret=secret, description=description)
        )

    def list_webhooks(self) -> list:
        """List all webhook endpoints for the caller's namespace."""
        return self._loop.run_until_complete(self._async.list_webhooks())

    def update_webhook(
        self,
        endpoint_id: str,
        enabled: Optional[bool] = None,
        events: Optional[list[str]] = None,
        description: Optional[str] = None,
    ) -> dict:
        """Update an endpoint's enabled state, events, or description."""
        return self._loop.run_until_complete(
            self._async.update_webhook(
                endpoint_id=endpoint_id, enabled=enabled, events=events, description=description
            )
        )

    def delete_webhook(self, endpoint_id: str) -> None:
        """Remove a webhook endpoint permanently."""
        self._loop.run_until_complete(self._async.delete_webhook(endpoint_id=endpoint_id))

    def webhook_deliveries(self, endpoint_id: str, limit: int = 50) -> dict:
        """Return recent delivery attempts for a webhook endpoint."""
        return self._loop.run_until_complete(
            self._async.webhook_deliveries(endpoint_id=endpoint_id, limit=limit)
        )
