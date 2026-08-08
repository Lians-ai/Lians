"""
Lians Python SDK — async HTTP client for the REST API.
"""

from __future__ import annotations

import asyncio
import math
import random
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
from typing import Any, Literal, Optional, cast

import httpx

from .platform_types import (
    AttestedClosure,
    ClosureAttestation,
    ClosureAttestationCreate,
    CompatibilityListPage,
    DecisionDependencyChangeType,
    DecisionDependencyKind,
    DecisionImpactResult,
    DecisionInvestigationReport,
    DecisionOut,
    EvidenceArtifactOut,
    ExhaustiveImpactAssessmentResults,
    ExhaustiveImpactAssessmentStatus,
    FirstReceiptReadiness,
    GateApprovalAttestation,
    GateApprovalAttestationCreate,
    GateApprovalAttestationSupersede,
    GateDecision,
    GateEvaluationRequest,
    GateEvaluationResult,
    GateExecutionPermitConsume,
    GateExecutionPermitConsumption,
    GatePolicySet,
    GatePolicySetCreate,
    InvestigationCase,
    InvestigationCaseCreate,
    InvestigationCaseUpdate,
    InvestigatorQueue,
    IssuerCreate,
    LiansDiscovery,
    LedgerEventOut,
    MeteringEvent,
    MeteringInventory,
    MeteringReplayRequest,
    MeteringStatus,
    PlatformCapabilities,
    PlatformReadiness,
    Principal,
    ReceiptIssuer,
    RecorderBatchResult,
    RecorderEnvelope,
    RecorderEvidenceIndexJob,
    RecorderEvent,
    RecorderIngestResult,
    RecorderRunReadiness,
    ScimTenantReconciliation,
    RemediationTask,
    RemediationTaskCreate,
    RemediationTaskUpdate,
    SupersessionActionResult,
    TrustedKeyCreate,
    TrustedKeyRotate,
    TrustedReceiptKey,
    WorkloadCredential,
    WorkloadCredentialCreate,
    WorkloadCredentialCreated,
    WorkloadCredentialRotate,
)

SDK_VERSION = "0.5.0"
USER_AGENT = f"lians-python-sdk/{SDK_VERSION}"
_RETRYABLE_STATUS_CODES = frozenset({408, 425, 429, 500, 502, 503, 504})


def _required_page_header(response: httpx.Response, name: str) -> str:
    value = response.headers.get(name)
    if value is None:
        raise ValueError(f"Lians pagination response is missing required header {name}")
    return value


def _page_int(response: httpx.Response, name: str) -> int:
    raw = _required_page_header(response, name)
    try:
        value = int(raw)
    except ValueError as exc:
        raise ValueError(f"Lians pagination header {name} is not an integer") from exc
    if value < 0:
        raise ValueError(f"Lians pagination header {name} cannot be negative")
    return value


def _page_bool(response: httpx.Response, name: str) -> bool:
    raw = _required_page_header(response, name).lower()
    if raw not in {"true", "false"}:
        raise ValueError(f"Lians pagination header {name} is not a boolean")
    return raw == "true"


def _compatibility_list_page(
    response: httpx.Response,
    payload: object,
    *,
    cursor_names: tuple[str, str],
) -> dict[str, Any]:
    """Decode the additive header contract without inventing missing values."""

    if not isinstance(payload, list):
        raise ValueError("Lians pagination response body must be a JSON array")
    total = _page_int(response, "X-Lians-Total-Count")
    limit = _page_int(response, "X-Lians-Page-Limit")
    returned = _page_int(response, "X-Lians-Page-Returned")
    has_more = _page_bool(response, "X-Lians-Has-More")
    page_complete = _page_bool(response, "X-Lians-Page-Complete")
    collection_complete = _page_bool(response, "X-Lians-Collection-Complete")
    if (
        returned != len(payload)
        or returned > limit
        or total < returned
        or page_complete == has_more
        or (collection_complete and has_more)
    ):
        raise ValueError("Lians pagination response headers are inconsistent")

    next_cursor: dict[str, str] | None = None
    if has_more:
        next_cursor = {}
        for name in cursor_names:
            suffix = "-".join(part.capitalize() for part in name.split("_"))
            next_cursor[name] = _required_page_header(
                response,
                f"X-Lians-Next-{suffix}",
            )
    return {
        "items": payload,
        "total": total,
        "limit": limit,
        "returned": returned,
        "has_more": has_more,
        "page_complete": page_complete,
        "collection_complete": collection_complete,
        "next_cursor": next_cursor,
    }


def _parse_retry_after(value: str | None) -> float | None:
    """Parse HTTP Retry-After delta-seconds or HTTP-date without raising."""

    if value is None:
        return None
    raw = value.strip()
    if not raw:
        return None
    try:
        seconds = float(raw)
    except ValueError:
        try:
            retry_at = parsedate_to_datetime(raw)
        except (TypeError, ValueError, OverflowError):
            return None
        if retry_at.tzinfo is None:
            retry_at = retry_at.replace(tzinfo=UTC)
        seconds = (retry_at.astimezone(UTC) - datetime.now(UTC)).total_seconds()
    if not math.isfinite(seconds):
        return None
    return max(0.0, seconds)


class AsyncLiansClient:
    """
    Async HTTP client for the Lians REST API.

    Parameters
    ----------
    base_url:
        Server base URL, e.g. ``"https://api.lians.example"``.
    api_key:
        Namespace-scoped API key (``X-API-Key`` header).
    access_token:
        OIDC/workload token (``Authorization: Bearer``). Supply this or
        ``api_key``, never both. Credentials and request bodies are not logged.
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
        max_retry_delay: float = 30.0,
        access_token: str = "",
    ):
        if api_key and access_token:
            raise ValueError("Supply api_key or access_token, not both")
        parsed_base = httpx.URL(base_url)
        if parsed_base.scheme not in {"http", "https"} or parsed_base.host is None:
            raise ValueError("base_url must be an absolute HTTP or HTTPS URL")
        if parsed_base.username or parsed_base.password:
            raise ValueError("base_url must not contain credentials")
        if parsed_base.query or parsed_base.fragment:
            raise ValueError("base_url must not contain a query string or fragment")
        if (
            isinstance(timeout, bool)
            or not isinstance(timeout, (int, float))
            or not math.isfinite(timeout)
            or not 0 < timeout <= 300
        ):
            raise ValueError("timeout must be greater than zero and at most 300 seconds")
        if (
            isinstance(max_retries, bool)
            or not isinstance(max_retries, int)
            or not 0 <= max_retries <= 10
        ):
            raise ValueError("max_retries must be an integer between 0 and 10")
        if (
            isinstance(backoff_factor, bool)
            or not isinstance(backoff_factor, (int, float))
            or not math.isfinite(backoff_factor)
            or not 0 <= backoff_factor <= 60
        ):
            raise ValueError("backoff_factor must be between 0 and 60 seconds")
        if (
            isinstance(max_retry_delay, bool)
            or not isinstance(max_retry_delay, (int, float))
            or not math.isfinite(max_retry_delay)
            or not 0 < max_retry_delay <= 300
        ):
            raise ValueError("max_retry_delay must be greater than zero and at most 300 seconds")
        self._base = base_url.rstrip("/")
        self._headers = {
            "Content-Type": "application/json",
            "User-Agent": USER_AGENT,
        }
        if api_key:
            self._headers["X-API-Key"] = api_key
        if access_token:
            self._headers["Authorization"] = f"Bearer {access_token}"
        self._admin_headers = {
            "Content-Type": "application/json",
            "User-Agent": USER_AGENT,
        }
        if admin_secret:
            self._admin_headers["X-Admin-Secret"] = admin_secret
        self._timeout = timeout
        self._max_retries = max_retries
        self._backoff_factor = backoff_factor
        self._max_retry_delay = max_retry_delay
        self._client: httpx.AsyncClient | None = None

    async def __aenter__(self) -> "AsyncLiansClient":
        return self

    async def __aexit__(self, *_exc: object) -> None:
        await self.aclose()

    async def aclose(self) -> None:
        """Close the shared connection pool owned by this SDK client."""
        if self._client is not None and not self._client.is_closed:
            await self._client.aclose()

    def _http_client(self) -> httpx.AsyncClient:
        if self._client is None or self._client.is_closed:
            self._client = httpx.AsyncClient(timeout=self._timeout)
        return self._client

    # ── Internal ──────────────────────────────────────────────────────────────

    def _retry_delay(self, attempt: int, retry_after: str | None = None) -> float | None:
        """Return a bounded delay, or ``None`` when the server floor is too long."""

        exponential = self._backoff_factor * (2**attempt)
        delay = min(
            self._max_retry_delay,
            exponential * (1.0 + random.random() * 0.25),
        )
        server_floor = _parse_retry_after(retry_after)
        if server_floor is None:
            return delay
        if server_floor > self._max_retry_delay:
            return None
        return max(delay, server_floor)

    async def _sleep_backoff(
        self,
        attempt: int,
        retry_after: str | None = None,
    ) -> bool:
        delay = self._retry_delay(attempt, retry_after)
        if delay is None:
            return False
        await asyncio.sleep(delay)
        return True

    async def _req(
        self,
        method: str,
        path: str,
        *,
        json: Any = None,
        params: Optional[dict] = None,
        admin: bool = False,
        extra_headers: Optional[dict] = None,
        retry_safe: bool | None = None,
        list_cursor_names: tuple[str, str] | None = None,
    ) -> Any:
        headers = dict(self._admin_headers if admin else self._headers)
        if extra_headers:
            headers.update(extra_headers)
        url = f"{self._base}{path}"
        clean_params = {
            k: (str(v).lower() if isinstance(v, bool) else v)
            for k, v in (params or {}).items()
            if v is not None
        }
        # Mutations are not retried unless the individual SDK operation opts in
        # after its server endpoint has a transactional idempotency contract.
        # A response can be lost after a successful commit, so HTTP status alone
        # cannot make an arbitrary POST/PATCH safe to replay.
        may_retry = (
            method.upper() in {"GET", "HEAD", "OPTIONS"} if retry_safe is None else retry_safe
        )

        attempt = 0
        while True:
            try:
                resp = await self._http_client().request(
                    method,
                    url,
                    headers=headers,
                    json=json,
                    params=clean_params,
                )
            except httpx.TransportError:
                # Capability issuance/redemption is explicitly marked unsafe to
                # retry: a response can be lost after the server committed.
                if not may_retry or attempt >= self._max_retries:
                    raise
                await self._sleep_backoff(attempt)
                attempt += 1
                continue

            # Retry transient server errors / throttling.
            if (
                may_retry
                and resp.status_code in _RETRYABLE_STATUS_CODES
                and attempt < self._max_retries
            ):
                if await self._sleep_backoff(attempt, resp.headers.get("Retry-After")):
                    attempt += 1
                    continue

            resp.raise_for_status()
            if resp.status_code in {204, 205}:
                return {}
            payload = resp.json()
            if list_cursor_names is not None:
                return _compatibility_list_page(
                    resp,
                    payload,
                    cursor_names=list_cursor_names,
                )
            return payload

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
        and reused across bounded transport/429/5xx retries. The server commits
        the resource and its body-bound completion claim atomically.
        """
        import uuid as _uuid

        key = idempotency_key or str(_uuid.uuid4())
        return await self._req(
            "POST",
            "/v1/memories",
            json={
                "agent_id": agent_id,
                "content": content,
                "event_time": event_time.isoformat(),
                "source": source,
                "subject_id": subject_id,
                "metadata": metadata or {},
                "importance": importance,
            },
            extra_headers={"Idempotency-Key": key},
            retry_safe=True,
        )

    async def batch_add(
        self,
        memories: list[dict[str, Any]],
        idempotency_key: Optional[str] = None,
    ) -> dict:
        """
        Add multiple memories in a single request.

        Each item in *memories* is a dict with the same keys as ``add()``.
        Items are processed sequentially so a later item can supersede an earlier
        one within the same batch (useful when loading a time-series of revisions).

        Returns a MemoryBatchResult dict with ``added`` count and ``memories`` list.
        One generated (or caller-supplied) idempotency key protects the ordered,
        atomic batch across bounded retries.
        """
        serialized = []
        for m in memories:
            row = dict(m)
            if isinstance(row.get("event_time"), datetime):
                row["event_time"] = row["event_time"].isoformat()
            serialized.append(row)
        import uuid as _uuid

        key = idempotency_key or str(_uuid.uuid4())
        return await self._req(
            "POST",
            "/v1/memories/batch",
            json={"memories": serialized},
            extra_headers={"Idempotency-Key": key},
            retry_safe=True,
        )

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
        from datetime import timezone as _tz

        _roles = set(roles) if roles is not None else {"assistant"}
        _event_time = event_time or datetime.now(_tz.utc)
        _meta_base = dict(metadata or {})

        batch = []
        for i, msg in enumerate(messages):
            role = (msg.get("role") or "").lower()
            content = (msg.get("content") or "").strip()
            if role not in _roles or not content:
                continue
            item_meta = {**_meta_base, "role": role, "message_index": i}
            batch.append(
                {
                    "agent_id": agent_id,
                    "content": content,
                    "event_time": _event_time.isoformat(),
                    "source": source,
                    "subject_id": subject_id,
                    "metadata": item_meta,
                    "importance": importance,
                }
            )

        if not batch:
            return {"added": 0, "memories": []}
        return await self.batch_add(batch)

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
        return await self._req(
            "POST",
            "/v1/recall",
            json={
                "agent_id": agent_id,
                "query": query,
                "k": k,
                "as_of": as_of.isoformat() if as_of else None,
                "filters": filters or {},
            },
        )

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
            "agent_id": agent_id,
            "query": query,
            "k": k,
            "max_tokens": max_tokens,
            "mmr": mmr,
            "surface_conflicts": surface_conflicts,
            "max_conflicts": max_conflicts,
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

    async def reconstruct(
        self,
        agent_id: str,
        as_of: datetime,
        query: Optional[str] = None,
        k: int = 20,
        memory_limit: int = 1000,
        event_limit: int = 5000,
    ) -> dict:
        """
        Reconstruct the full memory state and event trail at *as_of*.

        Returns every memory that was valid at that timestamp plus the
        timestamped, content-hashed event log for regulatory audit submissions.
        """
        params = {
            "agent_id": agent_id,
            "as_of": as_of.isoformat(),
            "k": k,
            "memory_limit": memory_limit,
            "event_limit": event_limit,
        }
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
        return await self._req(
            "POST",
            "/v1/erase",
            json={
                "subject_id": subject_id,
                "request_ref": request_ref,
            },
        )

    # ── Supersession review ───────────────────────────────────────────────────

    async def review_supersessions(
        self,
        threshold: Optional[float] = None,
        limit: int = 50,
        before_chain_position: Optional[int] = None,
    ) -> dict:
        """
        Return supersession events whose confidence is below *threshold*.

        In finance a wrong silent supersession — dropping a real number — is a
        compliance failure.  Poll this to surface uncertain events for human review
        before treating the old fact as stale.

        Returns exact ``total`` plus explicit page completeness and the next
        chain-position cursor when more unresolved items remain.
        """
        return await self._req(
            "GET",
            "/v1/supersessions/review",
            params={
                "threshold": threshold,
                "limit": limit,
                "before_chain_position": before_chain_position,
            },
        )

    async def confirm_supersession(
        self,
        memory_id: str,
        *,
        expected_superseded_by: Optional[str],
        reviewer_note: Optional[str] = None,
    ) -> SupersessionActionResult:
        """
        Confirm that a supersession was correct.

        Writes an immutable audit event with the reviewer's note; the superseded
        memory remains closed. Pass ``superseded_by`` from the review item as
        ``expected_superseded_by`` to reject stale reviewer actions.
        """
        return cast(
            SupersessionActionResult,
            await self._req(
                "PATCH",
                f"/v1/supersessions/{memory_id}",
                json={
                    "action": "confirm",
                    "expected_superseded_by": expected_superseded_by,
                    "reviewer_note": reviewer_note,
                },
            ),
        )

    async def reject_supersession(
        self,
        memory_id: str,
        *,
        expected_superseded_by: Optional[str],
        reviewer_note: Optional[str] = None,
    ) -> SupersessionActionResult:
        """
        Reject a supersession — the engine was wrong.

        Restores the old memory as currently valid (``valid_to = NULL``) and
        writes an immutable audit event. Pass ``superseded_by`` from the review
        item as ``expected_superseded_by`` to reject stale reviewer actions.
        Both memories are now additive.
        Returns a SupersessionActionResult dict.
        """
        return cast(
            SupersessionActionResult,
            await self._req(
                "PATCH",
                f"/v1/supersessions/{memory_id}",
                json={
                    "action": "reject",
                    "expected_superseded_by": expected_superseded_by,
                    "reviewer_note": reviewer_note,
                },
            ),
        )

    # ── Admin / Audit chain ───────────────────────────────────────────────────

    async def verify_chain(self, namespace: str) -> dict:
        """
        Verify the SEC 17a-4 tamper-evidence hash chain for *namespace*.

        Returns an ``ok``, ``partial``, or ``tampered`` status together with
        ``rows_checked``, ``truncated``, ``chain_tip``, and bounded violations.
        Requires ``admin_secret`` to be set on the client.
        """
        return await self._req(
            "GET",
            "/v1/admin/audit/verify",
            params={"namespace": namespace},
            admin=True,
        )

    async def audit_export(
        self,
        namespace: str,
        from_dt: Optional[datetime] = None,
        to_dt: Optional[datetime] = None,
        limit: int = 10_000,
        verify: bool = False,
        after_chain_position: Optional[int] = None,
        through_chain_position: Optional[int] = None,
    ) -> dict:
        """
        Export one exact-count, keyset-paginated audit-log page.

        ``total_rows`` is exact before the cursor. Follow
        ``next_chain_position`` while ``has_more`` is true; only an uncursored
        result with ``complete=true`` contains the full filtered collection.
        Retain ``snapshot_max_chain_position`` as ``through_chain_position`` on
        every continuation so concurrent appends cannot move the export.
        Pass ``verify=True`` to include a bounded chain-verification report.
        Requires ``admin_secret`` to be set on the client.
        """
        return await self._req(
            "GET",
            "/v1/admin/audit/export",
            params={
                "namespace": namespace,
                "from": from_dt.isoformat() if from_dt else None,
                "to": to_dt.isoformat() if to_dt else None,
                "limit": limit,
                "verify": verify,
                "after_chain_position": after_chain_position,
                "through_chain_position": through_chain_position,
            },
            admin=True,
        )

    # ── Snapshot (audit reconstruction) ───────────────────────────────────────

    async def snapshot(
        self,
        agent_id: str,
        as_of: datetime,
        limit: int = 1000,
        after_event_time: Optional[datetime] = None,
        after_id: Optional[str] = None,
        recorded_as_of: Optional[datetime] = None,
    ) -> dict:
        """
        Return one unranked, deterministic knowledge-state page at *as_of*.
        ``total`` is exact; only ``complete=True`` means the page is exhaustive.
        Retain the returned ``recorded_as_of`` on every continuation call.
        """
        return await self._req(
            "GET",
            "/v1/snapshot",
            params={
                "agent_id": agent_id,
                "as_of": as_of.isoformat(),
                "limit": limit,
                "after_event_time": (after_event_time.isoformat() if after_event_time else None),
                "after_id": after_id,
                "recorded_as_of": (recorded_as_of.isoformat() if recorded_as_of else None),
            },
        )

    # ── Backtest contamination ─────────────────────────────────────────────────

    async def backtest_check(
        self,
        agent_id: str,
        simulation_as_of: datetime,
        *,
        flag_limit: int = 1000,
        after_event_time: Optional[datetime] = None,
        after_id: Optional[str] = None,
    ) -> dict:
        """
        Detect lookahead bias in a backtest simulation.

        Exact counts determine cleanliness; detailed flags are a bounded page.
        Two contamination types:

        - ``future_event``  — ``event_time > simulation_as_of`` (clear lookahead)
        - ``late_revision`` — ``ingestion_time > simulation_as_of`` but
          ``event_time <= simulation_as_of`` (the revised figure hadn't arrived yet)

        ``is_clean`` is exact only for recorded memories visible inside the
        authenticated namespace/barrier; it does not attest to unrecorded input.

        Returns a ContaminationReport dict:
        ``{is_clean, flags_total, flags_complete, memories_checked, flags}``.
        """
        return await self._req(
            "POST",
            "/v1/backtest/check",
            json={
                "agent_id": agent_id,
                "simulation_as_of": simulation_as_of.isoformat(),
                "flag_limit": flag_limit,
                "after_event_time": (after_event_time.isoformat() if after_event_time else None),
                "after_id": after_id,
            },
        )

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
        return await self._req(
            "POST",
            "/v1/graph/relate",
            json={
                "agent_id": agent_id,
                "src_entity": src_entity,
                "rel_type": rel_type,
                "dst_entity": dst_entity,
                "event_time": event_time.isoformat(),
                "exclusive": exclusive,
                "subject_id": subject_id,
                "source": source,
                "metadata": metadata or {},
                "normalize": normalize,
            },
        )

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
        return await self._req(
            "POST",
            "/v1/graph/unrelate",
            json={
                "agent_id": agent_id,
                "src_entity": src_entity,
                "rel_type": rel_type,
                "dst_entity": dst_entity,
                "event_time": event_time.isoformat() if event_time else None,
                "normalize": normalize,
            },
        )

    async def neighbors(
        self,
        agent_id: str,
        entity: str,
        depth: int = 1,
        as_of: Optional[datetime] = None,
        rel_types: Optional[list[str]] = None,
        direction: str = "any",
        normalize: bool = False,
        max_nodes: int = 5000,
        max_edges: int = 20000,
    ) -> dict:
        """Entities within ``depth`` hops of ``entity`` (optional point-in-time ``as_of``)."""
        return await self._req(
            "GET",
            "/v1/graph/neighbors",
            params={
                "entity": entity,
                "agent_id": agent_id,
                "depth": depth,
                "direction": direction,
                "normalize": normalize,
                "as_of": as_of.isoformat() if as_of else None,
                "rel_type": rel_types,
                "max_nodes": max_nodes,
                "max_edges": max_edges,
            },
        )

    async def path(
        self,
        agent_id: str,
        src_entity: str,
        dst_entity: str,
        max_depth: int = 4,
        as_of: Optional[datetime] = None,
        rel_types: Optional[list[str]] = None,
        normalize: bool = False,
        max_nodes: int = 5000,
        max_edges: int = 20000,
    ) -> dict:
        """Shortest connection between two entities — the COI / related-party query."""
        return await self._req(
            "GET",
            "/v1/graph/path",
            params={
                "src": src_entity,
                "dst": dst_entity,
                "agent_id": agent_id,
                "max_depth": max_depth,
                "normalize": normalize,
                "as_of": as_of.isoformat() if as_of else None,
                "rel_type": rel_types,
                "max_nodes": max_nodes,
                "max_edges": max_edges,
            },
        )

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
        after_detected_at: Optional[str] = None,
        after_id: Optional[str] = None,
    ) -> dict:
        """
        List same-time fact contradictions detected by the supersession engine.

        Two sources reporting different values for the same structured fact
        (same ticker/metric) at the same event_time generate a conflict flag.
        Both memories remain valid until a human resolves the flag.

        *status* filters by resolution state: ``"open"`` (default), ``"accept_a"``,
        ``"accept_b"``, or ``"dismissed"``.

        Returns exact total and explicit keyset continuation fields.
        """
        return await self._req(
            "GET",
            "/v1/conflicts",
            params={
                "status": status,
                "limit": limit,
                "after_detected_at": after_detected_at,
                "after_id": after_id,
            },
        )

    async def resolve_conflict(
        self,
        conflict_id: str,
        resolution: Literal["accept_a", "accept_b", "dismiss"],
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
            "POST",
            f"/v1/conflicts/{conflict_id}/resolve",
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
        return await self._req(
            "GET",
            "/v1/facts/history",
            params={
                "agent_id": agent_id,
                "ticker": ticker,
                "metric": metric,
                "limit": limit,
            },
        )

    # ── Compliance report ──────────────────────────────────────────────────────

    async def compliance_report(
        self,
        from_dt: Optional[datetime] = None,
        to_dt: Optional[datetime] = None,
        verify_chain: bool = False,
        subject_id_limit: int = 1_000,
    ) -> dict:
        """
        Generate a compliance report for the caller's namespace.

        Covers: memory counts, audit chain status (SEC 17a-4), erasure records,
        open conflicts, supersession statistics, and retention policy snapshot.

        Pass ``verify_chain=True`` to run the hash-chain tamper check (adds ~50 ms
        per 10k events).

        Returns a ComplianceReport dict covering the requested window.
        """
        return await self._req(
            "GET",
            "/v1/compliance/report",
            params={
                "from": from_dt.isoformat() if from_dt else None,
                "to": to_dt.isoformat() if to_dt else None,
                "verify": verify_chain,
                "subject_id_limit": subject_id_limit,
            },
        )

    async def record_decision(
        self,
        *,
        agent_id: str,
        decision_type: str,
        outcome: str,
        decided_at: datetime,
        reason_codes: Optional[list[str]] = None,
        knowledge_as_of: Optional[datetime] = None,
        knowledge_recorded_as_of: Optional[datetime] = None,
        idempotency_key: Optional[str] = None,
        **fields: Any,
    ) -> DecisionOut:
        """Append a consequential decision through a body-bound retry claim."""
        import uuid as _uuid

        key = idempotency_key or str(_uuid.uuid4())
        return cast(
            DecisionOut,
            await self._req(
                "POST",
                "/v1/decisions",
                json={
                    "agent_id": agent_id,
                    "decision_type": decision_type,
                    "outcome": outcome,
                    "decided_at": decided_at.isoformat(),
                    "reason_codes": reason_codes or [],
                    "knowledge_as_of": knowledge_as_of.isoformat() if knowledge_as_of else None,
                    "knowledge_recorded_as_of": (
                        knowledge_recorded_as_of.isoformat() if knowledge_recorded_as_of else None
                    ),
                    **fields,
                },
                extra_headers={"Idempotency-Key": key},
                retry_safe=True,
            ),
        )

    async def decisions(self, **filters: Any) -> list[dict]:
        """List decision records, optionally filtered by agent, subject, or regime."""
        return await self._req("GET", "/v1/decisions", params=filters)

    async def decisions_page(
        self,
        *,
        agent_id: str | None = None,
        subject_id: str | None = None,
        regime: str | None = None,
        limit: int = 100,
        before_decided_at: datetime | str | None = None,
        before_id: str | None = None,
    ) -> CompatibilityListPage[DecisionOut]:
        """List decisions with exact cardinality and a truthful next keyset."""

        cursor_time = (
            before_decided_at.isoformat()
            if isinstance(before_decided_at, datetime)
            else before_decided_at
        )
        return await self._req(
            "GET",
            "/v1/decisions",
            params={
                "agent_id": agent_id,
                "subject_id": subject_id,
                "regime": regime,
                "limit": limit,
                "before_decided_at": cursor_time,
                "before_id": before_id,
            },
            list_cursor_names=("before_decided_at", "before_id"),
        )

    async def evidence_artifacts_page(
        self,
        *,
        kind: str | None = None,
        identifier: str | None = None,
        version: str | None = None,
        coordinate: str | None = None,
        artifact_hash: str | None = None,
        limit: int = 100,
        before_recorded_at: datetime | str | None = None,
        before_id: str | None = None,
    ) -> CompatibilityListPage[EvidenceArtifactOut]:
        """List evidence artifacts with exact totals and paired continuation."""

        cursor_time = (
            before_recorded_at.isoformat()
            if isinstance(before_recorded_at, datetime)
            else before_recorded_at
        )
        return await self._req(
            "GET",
            "/v1/decisions/evidence/artifacts",
            params={
                "kind": kind,
                "identifier": identifier,
                "version": version,
                "coordinate": coordinate,
                "artifact_hash": artifact_hash,
                "limit": limit,
                "before_recorded_at": cursor_time,
                "before_id": before_id,
            },
            list_cursor_names=("before_recorded_at", "before_id"),
        )

    async def review_decision(
        self,
        decision_id: str,
        status: Literal["requested", "affirmed", "overturned", "withdrawn"],
        reviewer: Optional[str] = None,
        note: Optional[str] = None,
        idempotency_key: Optional[str] = None,
    ) -> DecisionOut:
        """Record a review; the server derives the authenticated reviewer identity."""
        import uuid as _uuid

        key = idempotency_key or str(_uuid.uuid4())
        return cast(
            DecisionOut,
            await self._req(
                "POST",
                f"/v1/decisions/{decision_id}/review",
                json={"status": status, "reviewer": reviewer, "note": note},
                extra_headers={"Idempotency-Key": key},
                retry_safe=True,
            ),
        )

    async def evidence_pack(self, decision_id: str, verify: bool = True) -> dict:
        """Export a hash-anchored Evidence Pack v1 for a decision."""
        return await self._req(
            "GET", f"/v1/decisions/{decision_id}/evidence-pack", params={"verify": verify}
        )

    async def decision_evidence_graph(
        self,
        decision_id: str,
        *,
        limit: int = 500,
        after_relation: Optional[str] = None,
        after_link_id: Optional[str] = None,
    ) -> dict:
        """Read one bounded evidence-link page with exact graph counts."""
        return await self._req(
            "GET",
            f"/v1/decisions/{decision_id}/evidence-graph",
            params={
                "limit": limit,
                "after_relation": after_relation,
                "after_link_id": after_link_id,
            },
        )

    async def decision_review_history(
        self,
        decision_id: str,
        *,
        include_notes: bool = False,
        after_sequence: int = 0,
        limit: int = 500,
    ) -> dict:
        """Read one internally verified review-chain page."""
        return await self._req(
            "GET",
            f"/v1/decisions/{decision_id}/review-history",
            params={
                "include_notes": include_notes,
                "after_sequence": after_sequence,
                "limit": limit,
            },
        )

    async def decision_receipt(
        self,
        decision_id: str,
        verify: bool = True,
        include_source_content: bool = False,
    ) -> dict:
        """Export a completeness-scored, optionally Ed25519-signed Decision Receipt."""
        return await self._req(
            "GET",
            f"/v1/decisions/{decision_id}/receipt",
            params={
                "verify": verify,
                "include_source_content": include_source_content,
            },
        )

    async def verify_decision_receipt(
        self,
        receipt: dict,
        *,
        trusted_public_key: Optional[str] = None,
        require_signature: bool = False,
    ) -> dict:
        """Verify a portable receipt through the deployment's verifier endpoint."""
        return await self._req(
            "POST",
            "/v1/receipts/verify",
            json={
                "receipt": receipt,
                "trusted_public_key": trusted_public_key,
                "require_signature": require_signature,
            },
        )

    async def assess_decision_impact(
        self,
        dependency_kind: DecisionDependencyKind,
        dependency_value: str,
        *,
        change_type: DecisionDependencyChangeType = "changed",
        occurred_at: Optional[datetime | str] = None,
        note: Optional[str] = None,
        agent_id: str = "lians-impact-monitor",
        limit: int = 100,
        record_event: bool = True,
    ) -> DecisionImpactResult:
        """Return directly referenced and reachable decisions for a changed dependency."""
        result = await self._req(
            "POST",
            "/v1/decisions/impact",
            json={
                "dependency_kind": dependency_kind,
                "dependency_value": dependency_value,
                "change_type": change_type,
                "occurred_at": (
                    occurred_at.isoformat() if isinstance(occurred_at, datetime) else occurred_at
                ),
                "note": note,
                "agent_id": agent_id,
                "limit": limit,
                "record_event": record_event,
            },
        )
        return cast(DecisionImpactResult, result)

    async def start_exhaustive_impact_assessment(
        self,
        *,
        idempotency_key: str,
        dependency_kind: DecisionDependencyKind,
        dependency_value: str,
        change_type: DecisionDependencyChangeType = "changed",
        occurred_at: Optional[datetime | str] = None,
        note: Optional[str] = None,
        record_event: bool = True,
    ) -> ExhaustiveImpactAssessmentStatus:
        """Freeze a decision/evidence snapshot for exhaustive impact analysis."""
        result = await self._req(
            "POST",
            "/v1/decisions/impact-assessments",
            json={
                "idempotency_key": idempotency_key,
                "dependency_kind": dependency_kind,
                "dependency_value": dependency_value,
                "change_type": change_type,
                "occurred_at": (
                    occurred_at.isoformat() if isinstance(occurred_at, datetime) else occurred_at
                ),
                "note": note,
                "record_event": record_event,
            },
            retry_safe=True,
        )
        return cast(ExhaustiveImpactAssessmentStatus, result)

    async def get_exhaustive_impact_assessment(
        self,
        assessment_id: str,
    ) -> ExhaustiveImpactAssessmentStatus:
        """Read the durable status and frozen-snapshot progress for an assessment."""
        result = await self._req(
            "GET",
            f"/v1/decisions/impact-assessments/{assessment_id}",
        )
        return cast(ExhaustiveImpactAssessmentStatus, result)

    async def advance_exhaustive_impact_assessment(
        self,
        assessment_id: str,
        *,
        page_size: int = 250,
        max_pages: int = 1,
    ) -> ExhaustiveImpactAssessmentStatus:
        """Resume a durable assessment by a bounded number of keyset pages."""
        result = await self._req(
            "POST",
            f"/v1/decisions/impact-assessments/{assessment_id}/advance",
            json={"page_size": page_size, "max_pages": max_pages},
        )
        return cast(ExhaustiveImpactAssessmentStatus, result)

    async def list_exhaustive_impact_assessment_results(
        self,
        assessment_id: str,
        *,
        after: int = 0,
        limit: int = 100,
    ) -> ExhaustiveImpactAssessmentResults:
        """Read a keyset-paginated page of persisted assessment matches."""
        result = await self._req(
            "GET",
            f"/v1/decisions/impact-assessments/{assessment_id}/results",
            params={"after": after, "limit": limit},
        )
        return cast(ExhaustiveImpactAssessmentResults, result)

    # -- Universal Recorder -------------------------------------------------

    async def ingest_recorder_event(
        self,
        envelope: RecorderEnvelope,
        *,
        idempotency_key: Optional[str] = None,
    ) -> RecorderIngestResult:
        """Normalize and persist one Lians, OTLP GenAI, MCP, or A2A event.

        Builders in :mod:`lians.recorder` use ``hash_only`` capture by default.
        The optional idempotency key replaces the envelope value before send,
        making it easy to reuse a business-stable retry key.
        """
        body = dict(envelope)
        if idempotency_key is not None:
            body["idempotency_key"] = idempotency_key
        return await self._req(  # type: ignore[return-value]
            "POST", "/v1/recorder/events", json=body
        )

    async def ingest_recorder_batch(
        self,
        events: list[RecorderEnvelope],
        *,
        atomic: bool = True,
    ) -> RecorderBatchResult:
        """Ingest up to 500 mixed-protocol events in one transaction."""
        return await self._req(
            "POST",
            "/v1/recorder/batch",
            json={"events": events, "atomic": atomic},
        )  # type: ignore[return-value]

    async def recorder_run_readiness(self, run_id: str) -> RecorderRunReadiness:
        """Return Decision Receipt readiness for one correlated run boundary."""
        return await self._req("GET", f"/v1/recorder/runs/{run_id}/readiness")  # type: ignore[return-value]

    async def recorder_run_events(self, run_id: str, *, limit: int = 500) -> list[RecorderEvent]:
        """List normalized events for one correlated run boundary."""
        return await self._req("GET", f"/v1/recorder/runs/{run_id}/events", params={"limit": limit})  # type: ignore[return-value]

    async def recorder_run_events_page(
        self,
        run_id: str,
        *,
        limit: int = 500,
        before_recorded_at: str | datetime | None = None,
        before_id: str | None = None,
    ) -> CompatibilityListPage[RecorderEvent]:
        """Traverse a run in immutable ingestion order with exact cardinality."""

        cursor_time = (
            before_recorded_at.isoformat()
            if isinstance(before_recorded_at, datetime)
            else before_recorded_at
        )
        return await self._req(
            "GET",
            f"/v1/recorder/runs/{run_id}/events",
            params={
                "limit": limit,
                "before_recorded_at": cursor_time,
                "before_id": before_id,
            },
            list_cursor_names=("before_recorded_at", "before_id"),
        )

    async def recorder_readiness(
        self, *, agent_id: Optional[str] = None, limit: int = 50
    ) -> FirstReceiptReadiness:
        """Summarize capture gaps and time to the first receipt-ready run."""
        return await self._req(
            "GET",
            "/v1/recorder/readiness",
            params={"agent_id": agent_id, "limit": limit},
        )  # type: ignore[return-value]

    async def recorder_evidence_index_job(
        self,
        job_id: str,
    ) -> RecorderEvidenceIndexJob:
        return await self._req(
            "GET",
            f"/v1/recorder/indexing/jobs/{job_id}",
        )  # type: ignore[return-value]

    async def recorder_evidence_index_job_for_decision(
        self,
        decision_id: str,
    ) -> RecorderEvidenceIndexJob:
        return await self._req(
            "GET",
            f"/v1/recorder/indexing/decisions/{decision_id}",
        )  # type: ignore[return-value]

    async def retry_recorder_evidence_index_job(
        self,
        job_id: str,
    ) -> RecorderEvidenceIndexJob:
        return await self._req(
            "POST",
            f"/v1/recorder/indexing/jobs/{job_id}/retry",
            retry_safe=False,
        )  # type: ignore[return-value]

    # -- Governed agent improvement ---------------------------------------

    async def create_agent_definition(self, body: dict[str, Any]) -> dict[str, Any]:
        return await self._req("POST", "/v1/agents", json=body)

    async def agent_definition(self, agent_id: str) -> dict[str, Any]:
        return await self._req("GET", f"/v1/agents/{agent_id}")

    async def create_agent_version(self, agent_id: str, body: dict[str, Any]) -> dict[str, Any]:
        return await self._req("POST", f"/v1/agents/{agent_id}/versions", json=body)

    async def create_eval_case_from_decision(self, body: dict[str, Any]) -> dict[str, Any]:
        return await self._req("POST", "/v1/eval/cases/from-decision", json=body)

    async def create_eval_suite(self, body: dict[str, Any]) -> dict[str, Any]:
        return await self._req("POST", "/v1/eval/suites", json=body)

    async def create_eval_run(self, body: dict[str, Any]) -> dict[str, Any]:
        return await self._req("POST", "/v1/eval/runs", json=body)

    async def create_eval_comparison(self, body: dict[str, Any]) -> dict[str, Any]:
        return await self._req("POST", "/v1/eval/comparisons", json=body)

    async def create_evaluation_attestation(self, body: dict[str, Any]) -> dict[str, Any]:
        return await self._req("POST", "/v1/eval/attestations", json=body)

    async def verify_evaluation_attestation(
        self, attestation: dict[str, Any], *, trusted_public_key: str | None = None
    ) -> dict[str, Any]:
        return await self._req(
            "POST",
            "/v1/eval/attestations/verify",
            json={"attestation": attestation, "trusted_public_key": trusted_public_key},
        )

    async def create_optimization_study(self, body: dict[str, Any]) -> dict[str, Any]:
        return await self._req("POST", "/v1/optimization/studies", json=body)

    async def compile_context(self, body: dict[str, Any]) -> dict[str, Any]:
        return await self._req("POST", "/v1/context/compile", json=body)

    async def create_tool_registry(self, body: dict[str, Any]) -> dict[str, Any]:
        return await self._req("POST", "/v1/tools/registries", json=body)

    async def select_tools(self, body: dict[str, Any]) -> dict[str, Any]:
        return await self._req("POST", "/v1/tools/select", json=body)

    async def create_runtime_policy(self, body: dict[str, Any]) -> dict[str, Any]:
        return await self._req("POST", "/v1/runtime/policies", json=body)

    async def decide_route(self, body: dict[str, Any]) -> dict[str, Any]:
        return await self._req("POST", "/v1/routing/decide", json=body)

    async def decide_cache(self, body: dict[str, Any]) -> dict[str, Any]:
        return await self._req("POST", "/v1/cache/decide", json=body)

    async def create_concurrency_plan(self, body: dict[str, Any]) -> dict[str, Any]:
        return await self._req("POST", "/v1/runtime/concurrency/plan", json=body)

    async def record_outcome(self, body: dict[str, Any]) -> dict[str, Any]:
        return await self._req("POST", "/v1/outcomes", json=body)

    async def record_feedback(self, body: dict[str, Any]) -> dict[str, Any]:
        return await self._req("POST", "/v1/feedback", json=body)

    async def analyze_drift(self, body: dict[str, Any]) -> dict[str, Any]:
        return await self._req("POST", "/v1/drift/analyze", json=body)

    async def learning_proposals(self, *, limit: int = 100) -> list[dict[str, Any]]:
        return await self._req("GET", "/v1/learning/proposals", params={"limit": limit})

    async def create_release_candidate(self, body: dict[str, Any]) -> dict[str, Any]:
        return await self._req("POST", "/v1/releases", json=body)

    async def create_release_attestation(self, body: dict[str, Any]) -> dict[str, Any]:
        return await self._req("POST", "/v1/releases/attestations", json=body)

    async def verify_release_attestation(
        self, attestation: dict[str, Any], *, trusted_public_key: str | None = None
    ) -> dict[str, Any]:
        return await self._req(
            "POST",
            "/v1/releases/attestations/verify",
            json={"attestation": attestation, "trusted_public_key": trusted_public_key},
        )

    async def record_deployment(self, body: dict[str, Any]) -> dict[str, Any]:
        return await self._req("POST", "/v1/deployments", json=body)

    async def record_rollback(self, body: dict[str, Any]) -> dict[str, Any]:
        return await self._req("POST", "/v1/rollback", json=body)

    # -- Runtime Gate and investigations -----------------------------------

    async def create_receipt_issuer(self, issuer: IssuerCreate) -> ReceiptIssuer:
        return await self._req("POST", "/v1/control/trust/issuers", json=issuer)  # type: ignore[return-value]

    async def receipt_issuers(
        self,
        *,
        include_revoked: bool = False,
        offset: int = 0,
        limit: int = 100,
    ) -> list[ReceiptIssuer]:
        return await self._req(
            "GET",
            "/v1/control/trust/issuers",
            params={
                "include_revoked": include_revoked,
                "offset": offset,
                "limit": limit,
            },
        )  # type: ignore[return-value]

    async def revoke_receipt_issuer(
        self,
        issuer_id: str,
        *,
        reason: str,
        actor_id: Optional[str] = None,
    ) -> ReceiptIssuer:
        return await self._req(
            "POST",
            f"/v1/control/trust/issuers/{issuer_id}/revoke",
            json={"reason": reason, "actor_id": actor_id},
        )  # type: ignore[return-value]

    async def register_trusted_receipt_key(
        self, issuer_id: str, key: TrustedKeyCreate
    ) -> TrustedReceiptKey:
        return await self._req("POST", f"/v1/control/trust/issuers/{issuer_id}/keys", json=key)  # type: ignore[return-value]

    async def trusted_receipt_keys(
        self,
        issuer_id: str,
        *,
        include_revoked: bool = False,
        offset: int = 0,
        limit: int = 100,
    ) -> list[TrustedReceiptKey]:
        return await self._req(
            "GET",
            f"/v1/control/trust/issuers/{issuer_id}/keys",
            params={
                "include_revoked": include_revoked,
                "offset": offset,
                "limit": limit,
            },
        )  # type: ignore[return-value]

    async def resolve_trusted_receipt_key(
        self, key_id: str, *, at: Optional[datetime | str] = None
    ) -> TrustedReceiptKey:
        return await self._req(
            "GET",
            f"/v1/control/trust/keys/{key_id}",
            params={"at": at.isoformat() if isinstance(at, datetime) else at},
        )  # type: ignore[return-value]

    async def rotate_trusted_receipt_key(
        self, issuer_id: str, key_id: str, replacement: TrustedKeyRotate
    ) -> TrustedReceiptKey:
        return await self._req(
            "POST",
            f"/v1/control/trust/issuers/{issuer_id}/keys/{key_id}/rotate",
            json=replacement,
        )  # type: ignore[return-value]

    async def revoke_trusted_receipt_key(
        self,
        issuer_id: str,
        key_id: str,
        *,
        reason: str,
        actor_id: Optional[str] = None,
    ) -> TrustedReceiptKey:
        return await self._req(
            "POST",
            f"/v1/control/trust/issuers/{issuer_id}/keys/{key_id}/revoke",
            json={"reason": reason, "actor_id": actor_id},
        )  # type: ignore[return-value]

    async def create_gate_policy(self, policy: GatePolicySetCreate) -> GatePolicySet:
        return await self._req("POST", "/v1/control/gate/policies", json=policy)  # type: ignore[return-value]

    async def gate_policies(
        self,
        *,
        name: Optional[str] = None,
        status: Optional[str] = None,
        include_rules: bool = True,
        offset: int = 0,
        limit: int = 20,
    ) -> list[GatePolicySet]:
        return await self._req(
            "GET",
            "/v1/control/gate/policies",
            params={
                "name": name,
                "status": status,
                "include_rules": include_rules,
                "offset": offset,
                "limit": limit,
            },
        )  # type: ignore[return-value]

    async def gate_policy(self, policy_id: str) -> GatePolicySet:
        return await self._req("GET", f"/v1/control/gate/policies/{policy_id}")  # type: ignore[return-value]

    async def activate_gate_policy(
        self, policy_id: str, *, actor_id: Optional[str] = None
    ) -> GatePolicySet:
        return await self._req(
            "POST",
            f"/v1/control/gate/policies/{policy_id}/activate",
            json={"actor_id": actor_id},
        )  # type: ignore[return-value]

    async def create_gate_approval(
        self, attestation: GateApprovalAttestationCreate
    ) -> GateApprovalAttestation:
        """Append a role-bound approval for one exact Gate boundary."""
        return await self._req("POST", "/v1/control/gate/approvals", json=attestation)  # type: ignore[return-value]

    async def supersede_gate_approval(
        self,
        approval_id: str,
        successor: GateApprovalAttestationSupersede,
    ) -> GateApprovalAttestation:
        """Append an approval, rejection, or revocation to an attestation series."""
        return await self._req(
            "POST",
            f"/v1/control/gate/approvals/{approval_id}/supersede",
            json=successor,
        )  # type: ignore[return-value]

    async def gate_approvals(
        self,
        *,
        context_hash: Optional[str] = None,
        decision_id: Optional[str] = None,
        status: Optional[str] = None,
        only_current: bool = True,
        include_statement: bool = False,
        limit: int = 100,
    ) -> list[GateApprovalAttestation]:
        """List immutable approval attestations; statements require admin scope."""
        return await self._req(
            "GET",
            "/v1/control/gate/approvals",
            params={
                "context_hash": context_hash,
                "decision_id": decision_id,
                "status": status,
                "only_current": only_current,
                "include_statement": include_statement,
                "limit": limit,
            },
        )  # type: ignore[return-value]

    async def gate_approval(
        self, approval_id: str, *, include_statement: bool = False
    ) -> GateApprovalAttestation:
        return await self._req(
            "GET",
            f"/v1/control/gate/approvals/{approval_id}",
            params={"include_statement": include_statement},
        )  # type: ignore[return-value]

    async def evaluate_gate(self, request: GateEvaluationRequest) -> GateEvaluationResult:
        """Evaluate an action using identity derived from normal authentication.

        Omit ``principal_scopes`` and barrier assertions in ordinary use: the
        server derives them from the authenticated API key or workload token.
        A receipt ``document`` may be supplied for cryptographic verification;
        the Gate persists only its hash reference. An allow response carries one
        opaque permit that only the requested policy-authorized mediator can use.
        """
        return await self._req(
            "POST",
            "/v1/control/gate/evaluate",
            json=request,
            retry_safe=False,
        )  # type: ignore[return-value]

    async def consume_gate_execution_permit(
        self, request: GateExecutionPermitConsume
    ) -> GateExecutionPermitConsumption:
        """Redeem one permit as the exact mediator, with exact request bindings."""
        return await self._req(
            "POST",
            "/v1/control/gate/permits/consume",
            json=request,
            retry_safe=False,
        )  # type: ignore[return-value]

    async def gate_evaluations(
        self,
        *,
        disposition: Optional[str] = None,
        decision_id: Optional[str] = None,
        limit: int = 100,
    ) -> list[GateDecision]:
        return await self._req(
            "GET",
            "/v1/control/gate/evaluations",
            params={
                "disposition": disposition,
                "decision_id": decision_id,
                "limit": limit,
            },
        )  # type: ignore[return-value]

    async def gate_evaluation(self, evaluation_id: str) -> GateDecision:
        return await self._req("GET", f"/v1/control/gate/evaluations/{evaluation_id}")  # type: ignore[return-value]

    async def create_investigation_case(self, case: InvestigationCaseCreate) -> InvestigationCase:
        return await self._req("POST", "/v1/control/investigations/cases", json=case)  # type: ignore[return-value]

    async def investigation_cases(
        self,
        *,
        status: Optional[str] = None,
        owner_principal: Optional[str] = None,
        decision_id: Optional[str] = None,
        limit: int = 100,
    ) -> list[InvestigationCase]:
        return await self._req(
            "GET",
            "/v1/control/investigations/cases",
            params={
                "status": status,
                "owner_principal": owner_principal,
                "decision_id": decision_id,
                "limit": limit,
            },
        )  # type: ignore[return-value]

    async def investigation_case(self, case_id: str) -> InvestigationCase:
        return await self._req("GET", f"/v1/control/investigations/cases/{case_id}")  # type: ignore[return-value]

    async def update_investigation_case(
        self, case_id: str, update: InvestigationCaseUpdate
    ) -> InvestigationCase:
        return await self._req("PATCH", f"/v1/control/investigations/cases/{case_id}", json=update)  # type: ignore[return-value]

    async def create_remediation_task(
        self, case_id: str, task: RemediationTaskCreate
    ) -> RemediationTask:
        return await self._req(
            "POST", f"/v1/control/investigations/cases/{case_id}/tasks", json=task
        )  # type: ignore[return-value]

    async def remediation_tasks(
        self,
        case_id: str,
        *,
        status: Optional[str] = None,
        offset: int = 0,
        limit: int = 100,
    ) -> list[RemediationTask]:
        return await self._req(
            "GET",
            f"/v1/control/investigations/cases/{case_id}/tasks",
            params={"status": status, "offset": offset, "limit": limit},
        )  # type: ignore[return-value]

    async def update_remediation_task(
        self, task_id: str, update: RemediationTaskUpdate
    ) -> RemediationTask:
        return await self._req("PATCH", f"/v1/control/investigations/tasks/{task_id}", json=update)  # type: ignore[return-value]

    async def close_remediation_task(
        self, task_id: str, attestation: ClosureAttestationCreate
    ) -> AttestedClosure:
        return await self._req(
            "POST",
            f"/v1/control/investigations/tasks/{task_id}/close",
            json=attestation,
        )  # type: ignore[return-value]

    async def close_investigation_case(
        self, case_id: str, attestation: ClosureAttestationCreate
    ) -> AttestedClosure:
        return await self._req(
            "POST",
            f"/v1/control/investigations/cases/{case_id}/close",
            json=attestation,
        )  # type: ignore[return-value]

    async def closure_attestation(
        self,
        resource_type: Literal["case", "task"],
        resource_id: str,
        *,
        include_statement: bool = False,
    ) -> ClosureAttestation:
        if resource_type not in {"case", "task"}:
            raise ValueError("resource_type must be 'case' or 'task'")
        return await self._req(
            "GET",
            f"/v1/control/investigations/{resource_type}/{resource_id}/attestation",
            params={"include_statement": str(include_statement).lower()},
        )  # type: ignore[return-value]

    async def whoami(self) -> Principal:
        """Inspect the principal resolved from the client's normal credential."""
        return await self._req("GET", "/v1/identity/whoami")  # type: ignore[return-value]

    async def create_workload_credential(
        self, request: WorkloadCredentialCreate
    ) -> WorkloadCredentialCreated:
        """Issue one expiring credential; its plaintext secret is returned once."""
        return await self._req("POST", "/v1/identity/workload-credentials", json=request)  # type: ignore[return-value]

    async def workload_credentials(
        self,
        *,
        include_revoked: bool = False,
        include_expired: bool = False,
        limit: int = 100,
    ) -> list[WorkloadCredential]:
        """Return one compatibility array page; prefer workload_credentials_page."""
        return await self._req(
            "GET",
            "/v1/identity/workload-credentials",
            params={
                "include_revoked": include_revoked,
                "include_expired": include_expired,
                "limit": limit,
            },
        )  # type: ignore[return-value]

    async def workload_credentials_page(
        self,
        *,
        include_revoked: bool = False,
        include_expired: bool = False,
        limit: int = 100,
        before_created_at: str | None = None,
        before_id: str | None = None,
    ) -> CompatibilityListPage[WorkloadCredential]:
        """Return an exact page with a stable created-at/UUID continuation."""
        return await self._req(
            "GET",
            "/v1/identity/workload-credentials",
            params={
                "include_revoked": include_revoked,
                "include_expired": include_expired,
                "limit": limit,
                "before_created_at": before_created_at,
                "before_id": before_id,
            },
            list_cursor_names=("before_created_at", "before_id"),
        )  # type: ignore[return-value]

    async def metering_inventory(self, *, namespace: str | None = None) -> MeteringInventory:
        """Inspect the durable billing worker and exact backlog inventory."""
        return await self._req(
            "GET",
            "/v1/admin/billing-metering/status",
            params={"namespace": namespace},
            admin=True,
        )  # type: ignore[return-value]

    async def metering_events_page(
        self,
        *,
        status: MeteringStatus | None = None,
        namespace: str | None = None,
        limit: int = 100,
        before_updated_at: str | None = None,
        before_id: str | None = None,
    ) -> CompatibilityListPage[MeteringEvent]:
        """Traverse secret-free metering projections with exact page truth."""
        return await self._req(
            "GET",
            "/v1/admin/billing-metering/events",
            params={
                "status": status,
                "namespace": namespace,
                "limit": limit,
                "before_updated_at": before_updated_at,
                "before_id": before_id,
            },
            admin=True,
            list_cursor_names=("before_updated_at", "before_id"),
        )  # type: ignore[return-value]

    async def replay_metering_event(
        self,
        event_id: str,
        request: MeteringReplayRequest,
    ) -> MeteringEvent:
        return await self._req(
            "POST",
            f"/v1/admin/billing-metering/events/{event_id}/replay",
            json=request,
            admin=True,
        )  # type: ignore[return-value]

    async def scim_tenant_reconciliation(
        self,
        tenant_id: str,
        job_id: str,
    ) -> ScimTenantReconciliation:
        """Inspect exact progress for one tenant-version SCIM User snapshot."""
        return await self._req(
            "GET",
            f"/v1/admin/enterprise/scim/tenants/{tenant_id}/binding-reconciliations/{job_id}",
            admin=True,
        )  # type: ignore[return-value]

    async def retry_scim_tenant_reconciliation(
        self,
        tenant_id: str,
        job_id: str,
    ) -> ScimTenantReconciliation:
        return await self._req(
            "POST",
            f"/v1/admin/enterprise/scim/tenants/{tenant_id}/binding-reconciliations/{job_id}/retry",
            admin=True,
        )  # type: ignore[return-value]

    async def advance_scim_tenant_reconciliation(
        self,
        tenant_id: str,
        job_id: str,
    ) -> ScimTenantReconciliation:
        """Lease and advance at most one server-configured reconciliation page."""
        return await self._req(
            "POST",
            f"/v1/admin/enterprise/scim/tenants/{tenant_id}/"
            f"binding-reconciliations/{job_id}/advance",
            admin=True,
        )  # type: ignore[return-value]

    async def workload_credential(self, credential_id: str) -> WorkloadCredential:
        return await self._req("GET", f"/v1/identity/workload-credentials/{credential_id}")  # type: ignore[return-value]

    async def rotate_workload_credential(
        self, credential_id: str, request: WorkloadCredentialRotate
    ) -> WorkloadCredentialCreated:
        return await self._req(
            "POST",
            f"/v1/identity/workload-credentials/{credential_id}/rotate",
            json=request,
        )  # type: ignore[return-value]

    async def revoke_workload_credential(
        self, credential_id: str, *, expected_version: int
    ) -> None:
        await self._req(
            "DELETE",
            f"/v1/identity/workload-credentials/{credential_id}",
            params={"expected_version": expected_version},
        )

    async def discovery(self) -> LiansDiscovery:
        """Read the unauthenticated protocol discovery document."""
        return await self._req("GET", "/.well-known/lians")  # type: ignore[return-value]

    async def platform_capabilities(self) -> PlatformCapabilities:
        """Negotiate authenticated tenant capabilities and privacy defaults."""
        return await self._req("GET", "/v1/platform/capabilities")  # type: ignore[return-value]

    async def platform_readiness(self) -> PlatformReadiness:
        """Inspect deployment configuration readiness; requires admin scope."""
        return await self._req("GET", "/v1/platform/readiness")  # type: ignore[return-value]

    async def investigator_queue(
        self, *, limit: int = 100, scan_limit: int = 500
    ) -> InvestigatorQueue:
        """Prioritize recent decisions by evidence and control-plane signals."""
        return await self._req(
            "GET",
            "/v1/investigator/queue",
            params={"limit": limit, "scan_limit": scan_limit},
        )  # type: ignore[return-value]

    async def investigate_decision(
        self,
        decision_id: str,
        *,
        timeline_limit: int = 200,
        evidence_limit: int = 500,
        control_history_limit: int = 200,
        case_limit: int = 100,
        task_limit: int = 500,
        closure_limit: int = 500,
        include_sensitive: bool = False,
        verify_audit: bool = True,
    ) -> DecisionInvestigationReport:
        """Build one evidence/Gate/review/remediation report.

        ``include_sensitive`` is opt-in and requires admin scope.
        """
        return await self._req(
            "GET",
            f"/v1/investigator/decisions/{decision_id}",
            params={
                "timeline_limit": timeline_limit,
                "evidence_limit": evidence_limit,
                "control_history_limit": control_history_limit,
                "case_limit": case_limit,
                "task_limit": task_limit,
                "closure_limit": closure_limit,
                "include_sensitive": include_sensitive,
                "verify_audit": verify_audit,
            },
        )  # type: ignore[return-value]

    async def record_event(
        self,
        event_type: str,
        agent_id: str,
        occurred_at: datetime,
        idempotency_key: Optional[str] = None,
        **fields: Any,
    ) -> dict:
        """Append a first-class event through a body-bound retry claim."""
        import uuid as _uuid

        key = idempotency_key or str(_uuid.uuid4())
        return await self._req(
            "POST",
            "/v1/records/events",
            json={
                "event_type": event_type,
                "agent_id": agent_id,
                "occurred_at": occurred_at.isoformat(),
                **fields,
            },
            extra_headers={"Idempotency-Key": key},
            retry_safe=True,
        )

    async def record_events(self, **filters: Any) -> list[dict]:
        return await self._req("GET", "/v1/records/events", params=filters)

    async def record_events_page(
        self,
        *,
        event_type: str | None = None,
        agent_id: str | None = None,
        decision_id: str | None = None,
        limit: int = 100,
        before_occurred_at: datetime | str | None = None,
        before_id: str | None = None,
    ) -> CompatibilityListPage[LedgerEventOut]:
        """List ledger events with exact totals and paired continuation."""

        cursor_time = (
            before_occurred_at.isoformat()
            if isinstance(before_occurred_at, datetime)
            else before_occurred_at
        )
        return await self._req(
            "GET",
            "/v1/records/events",
            params={
                "event_type": event_type,
                "agent_id": agent_id,
                "decision_id": decision_id,
                "limit": limit,
                "before_occurred_at": cursor_time,
                "before_id": before_id,
            },
            list_cursor_names=("before_occurred_at", "before_id"),
        )

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
        ``X-Lians-Signature: sha256=<hex>`` (and the legacy
        ``X-AgentMem-Signature`` compatibility header). Store the secret securely —
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
        *,
        expected_updated_at: datetime | str,
        enabled: Optional[bool] = None,
        events: Optional[list[str]] = None,
        description: Optional[str] = None,
    ) -> dict:
        """Update an endpoint's enabled state, subscribed events, or description."""
        body: dict[str, Any] = {
            "expected_updated_at": (
                expected_updated_at.isoformat()
                if isinstance(expected_updated_at, datetime)
                else expected_updated_at
            )
        }
        if enabled is not None:
            body["enabled"] = enabled
        if events is not None:
            body["events"] = events
        if description is not None:
            body["description"] = description
        return await self._req("PATCH", f"/v1/webhooks/{endpoint_id}", json=body)

    async def delete_webhook(
        self,
        endpoint_id: str,
        *,
        expected_updated_at: datetime | str,
    ) -> None:
        """Remove a webhook endpoint permanently."""
        await self._req(
            "DELETE",
            f"/v1/webhooks/{endpoint_id}",
            params={
                "expected_updated_at": (
                    expected_updated_at.isoformat()
                    if isinstance(expected_updated_at, datetime)
                    else expected_updated_at
                )
            },
        )

    async def webhook_deliveries(
        self,
        endpoint_id: str,
        limit: int = 50,
        *,
        after_created_at: Optional[datetime] = None,
        after_id: Optional[str] = None,
    ) -> dict:
        """Return one stable keyset page of webhook delivery attempts."""
        if (after_created_at is None) != (after_id is None):
            raise ValueError("after_created_at and after_id must be supplied together")
        params: dict[str, Any] = {"limit": limit}
        if after_created_at is not None and after_id is not None:
            params.update(
                {
                    "after_created_at": after_created_at.isoformat(),
                    "after_id": after_id,
                }
            )
        return await self._req(
            "GET",
            f"/v1/webhooks/{endpoint_id}/deliveries",
            params=params,
        )
