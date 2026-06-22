"""
AgentMem Python SDK — async HTTP client.

AgentMem is a financial-grade AI memory layer providing:
  - Bitemporal recall (SEC 17a-4 / FINRA / CFTC audit-ready)
  - Automatic supersession (stale-fact exclusion, 0 contamination)
  - Crypto-shred erasure (GDPR Art. 17 / CCPA)
  - Tamper-evident SHA-256 hash chain
  - Backtest-contamination detection (lookahead-bias proof)
  - Audit reconstruction snapshot (complete knowledge state at T)

Requires: httpx>=0.27, pydantic>=2.0

Example::

    import asyncio
    from agentmem_sdk import AgentMemClient

    async def main():
        async with AgentMemClient(
            base_url="https://mem.yourfirm.internal",
            api_key=os.environ["AGENTMEM_API_KEY"],
        ) as client:
            mem = await client.add_memory(
                agent_id="equity-desk",
                content="AAPL Q1 EPS: $1.52",
                event_time="2026-01-28T00:00:00Z",
                metadata={"ticker": "AAPL", "metric": "eps"},
            )
            result = await client.recall(
                agent_id="equity-desk",
                query="Apple earnings",
            )
"""
from __future__ import annotations

import json
from datetime import datetime
from typing import Any, Optional
from urllib.parse import urlencode

import httpx

from .types import (
    MemoryOut, MemoryBatchResult, RecallResult,
    EraseResult, ErasureCertificate,
    MemoryLineageResult, FactHistoryResult, KnowledgeSnapshot,
    ContaminationReport, ConflictListResult, ConflictResolveResult,
    SupersessionReviewResult, AuditExportResult, ComplianceReport,
    WebhookEndpoint, WebhookRegisterResult, WebhookDeliveryListResult,
)


class AgentMemError(Exception):
    """Raised when the server returns a non-2xx response."""
    def __init__(self, status: int, body: str, message: str) -> None:
        super().__init__(message)
        self.status = status
        self.body = body


class AgentMemClient:
    """
    Async HTTP client for the AgentMem REST API.

    Use as an async context manager to manage the underlying httpx session::

        async with AgentMemClient(base_url=..., api_key=...) as client:
            await client.add_memory(...)

    Or manage the lifecycle manually::

        client = AgentMemClient(base_url=..., api_key=...)
        await client.add_memory(...)
        await client.aclose()
    """

    def __init__(
        self,
        base_url: str,
        api_key: str,
        *,
        admin_secret: Optional[str] = None,
        timeout: float = 30.0,
        http2: bool = True,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._api_key = api_key
        self._admin_secret = admin_secret
        self._http = httpx.AsyncClient(
            timeout=timeout,
            http2=http2,
            headers={"X-API-Key": api_key},
        )

    async def aclose(self) -> None:
        await self._http.aclose()

    async def __aenter__(self) -> AgentMemClient:
        return self

    async def __aexit__(self, *_: Any) -> None:
        await self.aclose()

    # ── Internal ──────────────────────────────────────────────────────────────

    def _url(self, path: str, params: dict[str, Any] | None = None) -> str:
        url = f"{self._base_url}{path}"
        if params:
            filtered = {k: v for k, v in params.items() if v is not None}
            if filtered:
                url += "?" + urlencode(filtered)
        return url

    async def _req(
        self,
        method: str,
        path: str,
        *,
        json_body: Any = None,
        params: dict[str, Any] | None = None,
        admin: bool = False,
    ) -> Any:
        headers: dict[str, str] = {}
        if admin and self._admin_secret:
            headers["X-Admin-Secret"] = self._admin_secret

        response = await self._http.request(
            method,
            self._url(path, params),
            json=json_body,
            headers=headers,
        )
        if not response.is_success:
            body = response.text
            raise AgentMemError(
                response.status_code,
                body,
                f"AgentMem {method} {path} → {response.status_code}: {body}",
            )
        if response.status_code == 204:
            return None
        return response.json()

    # ── Write ─────────────────────────────────────────────────────────────────

    async def add_memory(
        self,
        agent_id: str,
        content: str,
        event_time: str | datetime,
        *,
        source: Optional[str] = None,
        subject_id: Optional[str] = None,
        metadata: dict[str, Any] | None = None,
        importance: float = 0.5,
    ) -> MemoryOut:
        """Store a financial fact, observation, or decision."""
        body: dict[str, Any] = {
            "agent_id": agent_id,
            "content": content,
            "event_time": event_time.isoformat() if isinstance(event_time, datetime) else event_time,
            "importance": importance,
        }
        if source:
            body["source"] = source
        if subject_id:
            body["subject_id"] = subject_id
        if metadata:
            body["metadata"] = metadata
        data = await self._req("POST", "/v1/memories", json_body=body)
        return MemoryOut.model_validate(data)

    async def batch_add(self, memories: list[dict[str, Any]]) -> MemoryBatchResult:
        """Add multiple memories in a single request."""
        data = await self._req("POST", "/v1/memories/batch", json_body={"memories": memories})
        return MemoryBatchResult.model_validate(data)

    # ── Read ──────────────────────────────────────────────────────────────────

    async def recall(
        self,
        agent_id: str,
        query: str,
        *,
        k: int = 5,
        as_of: Optional[str | datetime] = None,
        filters: dict[str, Any] | None = None,
    ) -> RecallResult:
        """
        Retrieve the most relevant current memories for a query.

        Pass ``as_of`` for point-in-time recall — the compliance differentiator
        vs. mem0 / Zep.  Neither competitor can answer "what did the agent know
        on this date?"
        """
        body: dict[str, Any] = {"agent_id": agent_id, "query": query, "k": k}
        if as_of:
            body["as_of"] = as_of.isoformat() if isinstance(as_of, datetime) else as_of
        if filters:
            body["filters"] = filters
        data = await self._req("POST", "/v1/recall", json_body=body)
        return RecallResult.model_validate(data)

    async def get_lineage(self, memory_id: str) -> MemoryLineageResult:
        """Return the full belief provenance chain for a memory."""
        data = await self._req("GET", f"/v1/memories/{memory_id}/lineage")
        return MemoryLineageResult.model_validate(data)

    async def fact_history(
        self,
        agent_id: str,
        ticker: str,
        metric: str,
        *,
        limit: int = 100,
    ) -> FactHistoryResult:
        """
        Return all recorded versions of a structured fact ordered by event_time.

        Query by ticker + metric — no memory_id needed.  Entity normalization is
        automatic: 'Apple Inc.', ISIN 'US0378331005', and 'AAPL' resolve to the
        same series.
        """
        data = await self._req("GET", "/v1/facts/history", params={
            "agent_id": agent_id,
            "ticker": ticker,
            "metric": metric,
            "limit": limit,
        })
        return FactHistoryResult.model_validate(data)

    async def knowledge_snapshot(
        self,
        agent_id: str,
        as_of: str | datetime,
        *,
        limit: int = 1000,
    ) -> KnowledgeSnapshot:
        """
        Reconstruct the complete knowledge state of an agent at a specific point in time.

        Returns every memory valid at ``as_of`` — exhaustive, no vector search.
        The one-call compliance demo that closes deals with regulators.
        Neither mem0 nor Zep can answer this question.
        """
        ts = as_of.isoformat() if isinstance(as_of, datetime) else as_of
        data = await self._req("GET", "/v1/snapshot", params={
            "agent_id": agent_id,
            "as_of": ts,
            "limit": limit,
        })
        return KnowledgeSnapshot.model_validate(data)

    # ── Backtest ──────────────────────────────────────────────────────────────

    async def backtest_check(
        self,
        agent_id: str,
        simulation_as_of: str | datetime,
    ) -> ContaminationReport:
        """
        Detect lookahead bias in a backtest simulation.

        Flags memories the agent possessed that it couldn't have known at
        ``simulation_as_of``.  A clean report (``is_clean=True``) is the
        proof a risk committee needs before trusting a backtest result.
        """
        ts = simulation_as_of.isoformat() if isinstance(simulation_as_of, datetime) else simulation_as_of
        data = await self._req("POST", "/v1/backtest/check", json_body={
            "agent_id": agent_id,
            "simulation_as_of": ts,
        })
        return ContaminationReport.model_validate(data)

    # ── Compliance / Erasure ──────────────────────────────────────────────────

    async def erase_subject(
        self,
        subject_id: str,
        request_ref: str,
    ) -> EraseResult:
        """
        GDPR Art. 17 / CCPA crypto-shred.

        Destroys the data subject's per-subject DEK so all their memories become
        permanently unreadable.  The audit trail is preserved as content hashes.
        """
        data = await self._req("POST", "/v1/erase", json_body={
            "subject_id": subject_id,
            "request_ref": request_ref,
        })
        return EraseResult.model_validate(data)

    async def erasure_certificate(self, subject_id: str) -> ErasureCertificate:
        """
        Retrieve a cryptographic proof-of-erasure certificate.

        Returns a stable ``certificate_id`` and preserved ``content_hashes``
        proving the content was destroyed while the audit trail remains intact.
        File this with GDPR supervisory authorities or CCPA deletion requests.
        """
        data = await self._req("GET", f"/v1/erase/{subject_id}/certificate")
        return ErasureCertificate.model_validate(data)

    async def compliance_report(
        self,
        *,
        from_: Optional[str | datetime] = None,
        to: Optional[str | datetime] = None,
        verify: bool = False,
    ) -> ComplianceReport:
        """
        Generate a compliance report for the caller's namespace.

        Covers memory counts, audit chain status, erasures, open conflicts,
        supersession statistics, and retention policy snapshot.
        Ready for SEC/FINRA/CFTC examiners.
        """
        params: dict[str, Any] = {"verify": verify}
        if from_:
            params["from"] = from_.isoformat() if isinstance(from_, datetime) else from_
        if to:
            params["to"] = to.isoformat() if isinstance(to, datetime) else to
        data = await self._req("GET", "/v1/compliance/report", params=params)
        return ComplianceReport.model_validate(data)

    # ── Conflicts ─────────────────────────────────────────────────────────────

    async def list_conflicts(
        self,
        *,
        status: Optional[str] = None,
        limit: int = 50,
    ) -> ConflictListResult:
        """List detected contradictions between memories."""
        data = await self._req("GET", "/v1/conflicts", params={"status": status, "limit": limit})
        return ConflictListResult.model_validate(data)

    async def resolve_conflict(
        self,
        conflict_id: str,
        resolution: str,
        *,
        note: Optional[str] = None,
    ) -> ConflictResolveResult:
        """Resolve a conflict by accepting one side or dismissing the flag."""
        data = await self._req("POST", f"/v1/conflicts/{conflict_id}/resolve", json_body={
            "resolution": resolution,
            "note": note,
        })
        return ConflictResolveResult.model_validate(data)

    # ── Supersession review ───────────────────────────────────────────────────

    async def review_supersessions(
        self,
        *,
        threshold: Optional[float] = None,
        limit: int = 50,
    ) -> SupersessionReviewResult:
        """Return supersession events below the confidence threshold for human review."""
        data = await self._req("GET", "/v1/supersessions/review", params={
            "threshold": threshold,
            "limit": limit,
        })
        return SupersessionReviewResult.model_validate(data)

    async def confirm_supersession(
        self,
        memory_id: str,
        reviewer_note: Optional[str] = None,
    ) -> dict[str, Any]:
        """Confirm a supersession — the engine was correct."""
        return await self._req("PATCH", f"/v1/supersessions/{memory_id}", json_body={
            "action": "confirm",
            "reviewer_note": reviewer_note,
        })

    async def reject_supersession(
        self,
        memory_id: str,
        reviewer_note: Optional[str] = None,
    ) -> dict[str, Any]:
        """Reject a supersession — restores the old memory as currently valid."""
        return await self._req("PATCH", f"/v1/supersessions/{memory_id}", json_body={
            "action": "reject",
            "reviewer_note": reviewer_note,
        })

    # ── Webhooks ──────────────────────────────────────────────────────────────

    async def register_webhook(
        self,
        url: str,
        events: list[str],
        *,
        secret: Optional[str] = None,
        description: Optional[str] = None,
    ) -> WebhookRegisterResult:
        """
        Register a webhook endpoint.

        The returned ``secret`` is shown exactly once — store it to verify
        HMAC-SHA256 signatures on all deliveries.
        """
        body: dict[str, Any] = {"url": url, "events": events}
        if secret:
            body["secret"] = secret
        if description:
            body["description"] = description
        data = await self._req("POST", "/v1/webhooks", json_body=body)
        return WebhookRegisterResult.model_validate(data)

    async def list_webhooks(self) -> list[WebhookEndpoint]:
        """List all webhook endpoints for the caller's namespace."""
        data = await self._req("GET", "/v1/webhooks")
        return [WebhookEndpoint.model_validate(e) for e in data]

    async def update_webhook(
        self,
        endpoint_id: str,
        *,
        enabled: Optional[bool] = None,
        events: Optional[list[str]] = None,
        description: Optional[str] = None,
    ) -> WebhookEndpoint:
        """Update a webhook endpoint's enabled state, events, or description."""
        body: dict[str, Any] = {}
        if enabled is not None:
            body["enabled"] = enabled
        if events is not None:
            body["events"] = events
        if description is not None:
            body["description"] = description
        data = await self._req("PATCH", f"/v1/webhooks/{endpoint_id}", json_body=body)
        return WebhookEndpoint.model_validate(data)

    async def delete_webhook(self, endpoint_id: str) -> None:
        """Remove a webhook endpoint permanently."""
        await self._req("DELETE", f"/v1/webhooks/{endpoint_id}")

    async def webhook_deliveries(
        self,
        endpoint_id: str,
        *,
        limit: int = 50,
    ) -> WebhookDeliveryListResult:
        """Return recent delivery attempts for a webhook endpoint."""
        data = await self._req("GET", f"/v1/webhooks/{endpoint_id}/deliveries", params={"limit": limit})
        return WebhookDeliveryListResult.model_validate(data)

    # ── Admin / Audit chain ───────────────────────────────────────────────────

    async def audit_export(
        self,
        namespace: str,
        *,
        from_: Optional[str | datetime] = None,
        to: Optional[str | datetime] = None,
        limit: int = 1000,
        verify: bool = False,
    ) -> AuditExportResult:
        """
        Export the full audit log for a namespace (SEC/FINRA/CFTC examiners).

        Requires ``admin_secret`` to be set on the client.
        """
        params: dict[str, Any] = {"namespace": namespace, "limit": limit, "verify_chain": verify}
        if from_:
            params["from_"] = from_.isoformat() if isinstance(from_, datetime) else from_
        if to:
            params["to"] = to.isoformat() if isinstance(to, datetime) else to
        data = await self._req("GET", "/v1/admin/audit/export", params=params, admin=True)
        return AuditExportResult.model_validate(data)

    async def verify_chain(self, namespace: str) -> dict[str, Any]:
        """
        Verify the SEC 17a-4 tamper-evidence hash chain.

        Returns ``{"status": "ok", "rows_checked": N}`` or details on broken links.
        Requires ``admin_secret`` to be set on the client.
        """
        return await self._req("GET", "/v1/admin/audit/verify", params={"namespace": namespace}, admin=True)
