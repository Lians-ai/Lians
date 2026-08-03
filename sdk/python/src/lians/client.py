"""
Lians Python SDK — async HTTP client.

Lians is decision-evidence and AI control infrastructure providing:
  - Bitemporal recall (SEC 17a-4 / FINRA / CFTC audit-ready)
  - Automatic supersession (stale-fact exclusion, 0 contamination)
  - Crypto-shred erasure (GDPR Art. 17 / CCPA)
  - Tamper-evident SHA-256 hash chain
  - Backtest-contamination detection over recorded, visible Lians data
  - Audit reconstruction snapshot (complete knowledge state at T)

Requires: httpx>=0.27, pydantic>=2.0

Example::

    import asyncio
    from lians import LiansClient

    async def main():
        async with LiansClient(
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

from datetime import datetime
from typing import Any, Literal, Optional, cast
from urllib.parse import urlencode
from uuid import UUID

import httpx

from .types import (
    AttestedClosure,
    AuditChainVerifyResult,
    AuditExportResult,
    ClosureAttestation,
    ClosureAttestationCreate,
    ComplianceReport,
    CompatibilityListPage,
    ConflictListResult,
    ConflictResolveResult,
    ContaminationReport,
    DecisionDependencyChangeType,
    DecisionDependencyKind,
    DecisionEvidenceGraphResult,
    DecisionImpactResult,
    DecisionInvestigationReport,
    DecisionOut,
    DecisionReviewHistoryResult,
    EraseResult,
    ErasureCertificate,
    EvidenceArtifactOut,
    ExhaustiveImpactAssessmentResults,
    ExhaustiveImpactAssessmentStatus,
    FactHistoryResult,
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
    KnowledgeSnapshot,
    LedgerEventOut,
    LiansDiscovery,
    MemoryBatchResult,
    MemoryLineageResult,
    MemoryOut,
    PlatformCapabilities,
    PlatformReadiness,
    Principal,
    RecallResult,
    ReceiptIssuer,
    RecorderBatchResult,
    RecorderEnvelope,
    RecorderEvent,
    RecorderEvidenceIndexJob,
    RecorderIngestResult,
    RecorderRunReadiness,
    RemediationTask,
    RemediationTaskCreate,
    RemediationTaskUpdate,
    SupersessionActionResult,
    SupersessionReviewResult,
    TrustedKeyCreate,
    TrustedKeyRotate,
    TrustedReceiptKey,
    WebhookDeliveryListResult,
    WebhookEndpoint,
    WebhookRegisterResult,
    WorkloadCredential,
    WorkloadCredentialCreate,
    WorkloadCredentialCreated,
    WorkloadCredentialRotate,
)

SDK_VERSION = "0.5.0"
USER_AGENT = f"lians-python-compat/{SDK_VERSION}"


class LiansError(Exception):
    """Raised when the server returns a non-2xx response."""
    def __init__(self, status: int, body: str, message: str) -> None:
        super().__init__(message)
        self.status = status
        self.body = body


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


class LiansClient:
    """
    Async HTTP client for the Lians REST API.

    Use as an async context manager to manage the underlying httpx session::

        async with LiansClient(base_url=..., api_key=...) as client:
            await client.add_memory(...)

    Or manage the lifecycle manually::

        client = LiansClient(base_url=..., api_key=...)
        await client.add_memory(...)
        await client.aclose()
    """

    def __init__(
        self,
        base_url: str,
        api_key: str = "",
        *,
        access_token: str = "",
        admin_secret: Optional[str] = None,
        timeout: float = 30.0,
        http2: bool = True,
    ) -> None:
        if api_key and access_token:
            raise ValueError("Supply api_key or access_token, not both")
        self._base_url = base_url.rstrip("/")
        self._api_key = api_key
        self._admin_secret = admin_secret
        auth_headers: dict[str, str] = {"User-Agent": USER_AGENT}
        if api_key:
            auth_headers["X-API-Key"] = api_key
        if access_token:
            auth_headers["Authorization"] = f"Bearer {access_token}"
        self._http = httpx.AsyncClient(
            timeout=timeout,
            http2=http2,
            headers=auth_headers,
        )

    async def aclose(self) -> None:
        await self._http.aclose()

    async def __aenter__(self) -> LiansClient:
        return self

    async def __aexit__(self, *_: Any) -> None:
        await self.aclose()

    # ── Internal ──────────────────────────────────────────────────────────────

    def _url(self, path: str, params: dict[str, Any] | None = None) -> str:
        url = f"{self._base_url}{path}"
        if params:
            filtered = {
                k: (str(v).lower() if isinstance(v, bool) else v)
                for k, v in params.items()
                if v is not None
            }
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
        extra_headers: dict[str, str] | None = None,
        list_cursor_names: tuple[str, str] | None = None,
    ) -> Any:
        headers: dict[str, str] = {}
        if admin and self._admin_secret:
            headers["X-Admin-Secret"] = self._admin_secret
        if extra_headers:
            headers.update(extra_headers)

        response = await self._http.request(
            method,
            self._url(path, params),
            json=json_body,
            headers=headers,
        )
        if not response.is_success:
            body = response.text
            raise LiansError(
                response.status_code,
                body,
                f"Lians {method} {path} → {response.status_code}: {body}",
            )
        if response.status_code == 204:
            return None
        payload = response.json()
        if list_cursor_names is not None:
            return _compatibility_list_page(
                response,
                payload,
                cursor_names=list_cursor_names,
            )
        return payload

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
        idempotency_key: Optional[str] = None,
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
        data = await self._req(
            "POST",
            "/v1/memories",
            json_body=body,
            extra_headers=(
                {"Idempotency-Key": idempotency_key}
                if idempotency_key is not None
                else None
            ),
        )
        return MemoryOut.model_validate(data)

    async def batch_add(
        self,
        memories: list[dict[str, Any]],
        *,
        idempotency_key: Optional[str] = None,
    ) -> MemoryBatchResult:
        """Add multiple memories in a single request."""
        serialized: list[dict[str, Any]] = []
        for memory in memories:
            item = dict(memory)
            if isinstance(item.get("event_time"), datetime):
                item["event_time"] = item["event_time"].isoformat()
            serialized.append(item)
        data = await self._req(
            "POST",
            "/v1/memories/batch",
            json_body={"memories": serialized},
            extra_headers=(
                {"Idempotency-Key": idempotency_key}
                if idempotency_key is not None
                else None
            ),
        )
        return MemoryBatchResult.model_validate(data)

    async def record_decision(
        self,
        *,
        agent_id: str,
        decision_type: str,
        outcome: str,
        decided_at: str | datetime,
        reason_codes: Optional[list[str]] = None,
        knowledge_as_of: Optional[str | datetime] = None,
        knowledge_recorded_as_of: Optional[str | datetime] = None,
        idempotency_key: Optional[str] = None,
        **evidence: Any,
    ) -> DecisionOut:
        """Append a consequential decision and its evidence-boundary fields."""
        body = {
            "agent_id": agent_id,
            "decision_type": decision_type,
            "outcome": outcome,
            "decided_at": (
                decided_at.isoformat() if isinstance(decided_at, datetime) else decided_at
            ),
            "reason_codes": reason_codes or [],
            "knowledge_as_of": (
                knowledge_as_of.isoformat()
                if isinstance(knowledge_as_of, datetime)
                else knowledge_as_of
            ),
            "knowledge_recorded_as_of": (
                knowledge_recorded_as_of.isoformat()
                if isinstance(knowledge_recorded_as_of, datetime)
                else knowledge_recorded_as_of
            ),
            **evidence,
        }
        data = await self._req(
            "POST",
            "/v1/decisions",
            json_body=body,
            extra_headers=(
                {"Idempotency-Key": idempotency_key}
                if idempotency_key is not None
                else None
            ),
        )
        # DecisionOut is a TypedDict so callers retain ordinary mapping
        # semantics, matching the canonical SDK.  It intentionally has no
        # Pydantic ``model_validate`` method.
        return cast(DecisionOut, data)

    async def decisions_page(
        self,
        *,
        agent_id: str | None = None,
        subject_id: str | None = None,
        regime: str | None = None,
        limit: int = 100,
        before_decided_at: str | datetime | None = None,
        before_id: str | None = None,
    ) -> CompatibilityListPage[DecisionOut]:
        """List decisions with exact cardinality and a paired next keyset."""

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

    async def record_events_page(
        self,
        *,
        event_type: str | None = None,
        agent_id: str | None = None,
        decision_id: str | None = None,
        limit: int = 100,
        before_occurred_at: str | datetime | None = None,
        before_id: str | None = None,
    ) -> CompatibilityListPage[LedgerEventOut]:
        """List system-of-record events with exact totals and continuation."""

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

    async def evidence_artifacts_page(
        self,
        *,
        kind: str | None = None,
        identifier: str | None = None,
        version: str | None = None,
        coordinate: str | None = None,
        artifact_hash: str | None = None,
        limit: int = 100,
        before_recorded_at: str | datetime | None = None,
        before_id: str | None = None,
    ) -> CompatibilityListPage[EvidenceArtifactOut]:
        """List evidence artifacts with exact totals and continuation."""

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

    async def decision_receipt(
        self,
        decision_id: str,
        *,
        verify: bool = True,
        include_source_content: bool = False,
    ) -> dict[str, Any]:
        """Export a completeness-scored, optionally signed Decision Receipt v0.1."""
        return await self._req(
            "GET",
            f"/v1/decisions/{decision_id}/receipt",
            params={
                "verify": verify,
                "include_source_content": include_source_content,
            },
        )

    async def decision_evidence_graph(
        self,
        decision_id: str,
        *,
        limit: int = 500,
        after_relation: Literal["direct", "reachable"] | None = None,
        after_link_id: str | None = None,
    ) -> DecisionEvidenceGraphResult:
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
    ) -> DecisionReviewHistoryResult:
        """Read an internally verified review-chain page."""
        return await self._req(
            "GET",
            f"/v1/decisions/{decision_id}/review-history",
            params={
                "include_notes": include_notes,
                "after_sequence": after_sequence,
                "limit": limit,
            },
        )

    async def verify_decision_receipt(
        self,
        receipt: dict[str, Any],
        *,
        trusted_public_key: Optional[str] = None,
        require_signature: bool = False,
    ) -> dict[str, Any]:
        """Verify a receipt hash/signature through the Lians API."""
        return await self._req(
            "POST",
            "/v1/receipts/verify",
            json_body={
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
        occurred_at: Optional[str | datetime] = None,
        note: Optional[str] = None,
        agent_id: str = "lians-impact-monitor",
        limit: int = 100,
        record_event: bool = True,
    ) -> DecisionImpactResult:
        """Find direct and reachable decisions after an evidence dependency change."""
        return await self._req(
            "POST",
            "/v1/decisions/impact",
            json_body={
                "dependency_kind": dependency_kind,
                "dependency_value": dependency_value,
                "change_type": change_type,
                "occurred_at": (
                    occurred_at.isoformat()
                    if isinstance(occurred_at, datetime)
                    else occurred_at
                ),
                "note": note,
                "agent_id": agent_id,
                "limit": limit,
                "record_event": record_event,
            },
        )

    async def start_exhaustive_impact_assessment(
        self,
        *,
        idempotency_key: str,
        dependency_kind: DecisionDependencyKind,
        dependency_value: str,
        change_type: DecisionDependencyChangeType = "changed",
        occurred_at: Optional[str | datetime] = None,
        note: Optional[str] = None,
        record_event: bool = True,
    ) -> ExhaustiveImpactAssessmentStatus:
        """Freeze a decision/evidence snapshot for exhaustive impact analysis."""
        return await self._req(
            "POST",
            "/v1/decisions/impact-assessments",
            json_body={
                "idempotency_key": idempotency_key,
                "dependency_kind": dependency_kind,
                "dependency_value": dependency_value,
                "change_type": change_type,
                "occurred_at": (
                    occurred_at.isoformat()
                    if isinstance(occurred_at, datetime)
                    else occurred_at
                ),
                "note": note,
                "record_event": record_event,
            },
        )

    async def get_exhaustive_impact_assessment(
        self,
        assessment_id: str,
    ) -> ExhaustiveImpactAssessmentStatus:
        """Read durable progress for an exhaustive impact assessment."""
        return await self._req(
            "GET",
            f"/v1/decisions/impact-assessments/{assessment_id}",
        )

    async def advance_exhaustive_impact_assessment(
        self,
        assessment_id: str,
        *,
        page_size: int = 250,
        max_pages: int = 1,
    ) -> ExhaustiveImpactAssessmentStatus:
        """Resume a durable assessment by a bounded number of keyset pages."""
        return await self._req(
            "POST",
            f"/v1/decisions/impact-assessments/{assessment_id}/advance",
            json_body={"page_size": page_size, "max_pages": max_pages},
        )

    async def list_exhaustive_impact_assessment_results(
        self,
        assessment_id: str,
        *,
        after: int = 0,
        limit: int = 100,
    ) -> ExhaustiveImpactAssessmentResults:
        """Read a keyset-paginated page of persisted assessment matches."""
        return await self._req(
            "GET",
            f"/v1/decisions/impact-assessments/{assessment_id}/results",
            params={"after": after, "limit": limit},
        )

    # ── Universal Recorder ──────────────────────────────────────────────────

    async def ingest_recorder_event(
        self,
        envelope: RecorderEnvelope,
        *,
        idempotency_key: Optional[str] = None,
    ) -> RecorderIngestResult:
        """Ingest one event; builders default to hash-only persistence."""
        body = envelope.model_dump(mode="json", exclude_none=True)
        if idempotency_key is not None:
            body["idempotency_key"] = idempotency_key
        data = await self._req("POST", "/v1/recorder/events", json_body=body)
        return RecorderIngestResult.model_validate(data)

    async def ingest_recorder_batch(
        self,
        events: list[RecorderEnvelope],
        *,
        atomic: bool = True,
    ) -> RecorderBatchResult:
        data = await self._req(
            "POST",
            "/v1/recorder/batch",
            json_body={
                "events": [event.model_dump(mode="json", exclude_none=True) for event in events],
                "atomic": atomic,
            },
        )
        return RecorderBatchResult.model_validate(data)

    async def recorder_run_readiness(self, run_id: str) -> RecorderRunReadiness:
        data = await self._req("GET", f"/v1/recorder/runs/{run_id}/readiness")
        return RecorderRunReadiness.model_validate(data)

    async def recorder_run_events(
        self, run_id: str, *, limit: int = 500
    ) -> list[RecorderEvent]:
        data = await self._req(
            "GET", f"/v1/recorder/runs/{run_id}/events", params={"limit": limit}
        )
        return [RecorderEvent.model_validate(item) for item in data]

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
        data = await self._req(
            "GET",
            "/v1/recorder/readiness",
            params={"agent_id": agent_id, "limit": limit},
        )
        return FirstReceiptReadiness.model_validate(data)

    async def recorder_evidence_index_job(
        self, job_id: str | UUID
    ) -> RecorderEvidenceIndexJob:
        data = await self._req(
            "GET", f"/v1/recorder/indexing/jobs/{job_id}"
        )
        return RecorderEvidenceIndexJob.model_validate(data)

    async def recorder_evidence_index_job_for_decision(
        self, decision_id: str | UUID
    ) -> RecorderEvidenceIndexJob:
        data = await self._req(
            "GET", f"/v1/recorder/indexing/decisions/{decision_id}"
        )
        return RecorderEvidenceIndexJob.model_validate(data)

    async def retry_recorder_evidence_index_job(
        self, job_id: str | UUID
    ) -> RecorderEvidenceIndexJob:
        data = await self._req(
            "POST", f"/v1/recorder/indexing/jobs/{job_id}/retry"
        )
        return RecorderEvidenceIndexJob.model_validate(data)

    # ── Runtime Gate and investigations ─────────────────────────────────────

    async def create_receipt_issuer(self, issuer: IssuerCreate) -> ReceiptIssuer:
        data = await self._req(
            "POST",
            "/v1/control/trust/issuers",
            json_body=issuer.model_dump(mode="json", exclude_none=True),
        )
        return ReceiptIssuer.model_validate(data)

    async def receipt_issuers(
        self, *, include_revoked: bool = False
    ) -> list[ReceiptIssuer]:
        data = await self._req(
            "GET",
            "/v1/control/trust/issuers",
            params={"include_revoked": include_revoked},
        )
        return [ReceiptIssuer.model_validate(item) for item in data]

    async def revoke_receipt_issuer(
        self,
        issuer_id: str,
        *,
        reason: str,
        actor_id: Optional[str] = None,
    ) -> ReceiptIssuer:
        data = await self._req(
            "POST",
            f"/v1/control/trust/issuers/{issuer_id}/revoke",
            json_body={"reason": reason, "actor_id": actor_id},
        )
        return ReceiptIssuer.model_validate(data)

    async def register_trusted_receipt_key(
        self, issuer_id: str, key: TrustedKeyCreate
    ) -> TrustedReceiptKey:
        data = await self._req(
            "POST",
            f"/v1/control/trust/issuers/{issuer_id}/keys",
            json_body=key.model_dump(mode="json", exclude_none=True),
        )
        return TrustedReceiptKey.model_validate(data)

    async def trusted_receipt_keys(
        self, issuer_id: str, *, include_revoked: bool = False
    ) -> list[TrustedReceiptKey]:
        data = await self._req(
            "GET",
            f"/v1/control/trust/issuers/{issuer_id}/keys",
            params={"include_revoked": include_revoked},
        )
        return [TrustedReceiptKey.model_validate(item) for item in data]

    async def resolve_trusted_receipt_key(
        self, key_id: str, *, at: Optional[str | datetime] = None
    ) -> TrustedReceiptKey:
        timestamp = at.isoformat() if isinstance(at, datetime) else at
        data = await self._req(
            "GET", f"/v1/control/trust/keys/{key_id}", params={"at": timestamp}
        )
        return TrustedReceiptKey.model_validate(data)

    async def rotate_trusted_receipt_key(
        self, issuer_id: str, key_id: str, replacement: TrustedKeyRotate
    ) -> TrustedReceiptKey:
        data = await self._req(
            "POST",
            f"/v1/control/trust/issuers/{issuer_id}/keys/{key_id}/rotate",
            json_body=replacement.model_dump(mode="json", exclude_none=True),
        )
        return TrustedReceiptKey.model_validate(data)

    async def revoke_trusted_receipt_key(
        self,
        issuer_id: str,
        key_id: str,
        *,
        reason: str,
        actor_id: Optional[str] = None,
    ) -> TrustedReceiptKey:
        data = await self._req(
            "POST",
            f"/v1/control/trust/issuers/{issuer_id}/keys/{key_id}/revoke",
            json_body={"reason": reason, "actor_id": actor_id},
        )
        return TrustedReceiptKey.model_validate(data)

    async def create_gate_policy(self, policy: GatePolicySetCreate) -> GatePolicySet:
        data = await self._req(
            "POST",
            "/v1/control/gate/policies",
            json_body=policy.model_dump(mode="json", exclude_none=True),
        )
        return GatePolicySet.model_validate(data)

    async def gate_policies(
        self,
        *,
        name: Optional[str] = None,
        status: Optional[str] = None,
        include_rules: bool = True,
        offset: int = 0,
        limit: int = 20,
    ) -> list[GatePolicySet]:
        data = await self._req(
            "GET",
            "/v1/control/gate/policies",
            params={
                "name": name,
                "status": status,
                "include_rules": include_rules,
                "offset": offset,
                "limit": limit,
            },
        )
        return [GatePolicySet.model_validate(item) for item in data]

    async def gate_policy(self, policy_id: str) -> GatePolicySet:
        data = await self._req("GET", f"/v1/control/gate/policies/{policy_id}")
        return GatePolicySet.model_validate(data)

    async def activate_gate_policy(
        self, policy_id: str, *, actor_id: Optional[str] = None
    ) -> GatePolicySet:
        data = await self._req(
            "POST",
            f"/v1/control/gate/policies/{policy_id}/activate",
            json_body={"actor_id": actor_id},
        )
        return GatePolicySet.model_validate(data)

    async def create_gate_approval(
        self, attestation: GateApprovalAttestationCreate
    ) -> GateApprovalAttestation:
        data = await self._req(
            "POST",
            "/v1/control/gate/approvals",
            json_body=attestation.model_dump(mode="json", exclude_none=True),
        )
        return GateApprovalAttestation.model_validate(data)

    async def supersede_gate_approval(
        self,
        approval_id: str,
        successor: GateApprovalAttestationSupersede,
    ) -> GateApprovalAttestation:
        data = await self._req(
            "POST",
            f"/v1/control/gate/approvals/{approval_id}/supersede",
            json_body=successor.model_dump(mode="json", exclude_none=True),
        )
        return GateApprovalAttestation.model_validate(data)

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
        data = await self._req(
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
        )
        return [GateApprovalAttestation.model_validate(item) for item in data]

    async def gate_approval(
        self, approval_id: str, *, include_statement: bool = False
    ) -> GateApprovalAttestation:
        data = await self._req(
            "GET",
            f"/v1/control/gate/approvals/{approval_id}",
            # Keep query booleans interoperable with OpenAPI clients and
            # strict HTTP mocks instead of relying on server-specific parsing
            # of Python's capitalized ``True``/``False`` strings.
            params={"include_statement": str(include_statement).lower()},
        )
        return GateApprovalAttestation.model_validate(data)

    async def evaluate_gate(self, request: GateEvaluationRequest) -> GateEvaluationResult:
        """Evaluate and receive a one-time permit only when the verdict is allow."""
        data = await self._req(
            "POST",
            "/v1/control/gate/evaluate",
            json_body=request.model_dump(
                mode="json", exclude_none=True, exclude_unset=True
            ),
        )
        return GateEvaluationResult.model_validate(data)

    async def consume_gate_execution_permit(
        self, request: GateExecutionPermitConsume
    ) -> GateExecutionPermitConsumption:
        """Redeem a permit as the exact policy-authorized mediator identity."""
        data = await self._req(
            "POST",
            "/v1/control/gate/permits/consume",
            json_body=request.model_dump(mode="json"),
        )
        return GateExecutionPermitConsumption.model_validate(data)

    async def gate_evaluations(
        self,
        *,
        disposition: Optional[str] = None,
        decision_id: Optional[str] = None,
        limit: int = 100,
    ) -> list[GateDecision]:
        data = await self._req(
            "GET",
            "/v1/control/gate/evaluations",
            params={
                "disposition": disposition,
                "decision_id": decision_id,
                "limit": limit,
            },
        )
        return [GateDecision.model_validate(item) for item in data]

    async def gate_evaluation(self, evaluation_id: str) -> GateDecision:
        data = await self._req("GET", f"/v1/control/gate/evaluations/{evaluation_id}")
        return GateDecision.model_validate(data)

    async def create_investigation_case(
        self, case: InvestigationCaseCreate
    ) -> InvestigationCase:
        data = await self._req(
            "POST",
            "/v1/control/investigations/cases",
            json_body=case.model_dump(mode="json", exclude_none=True),
        )
        return InvestigationCase.model_validate(data)

    async def investigation_cases(
        self,
        *,
        status: Optional[str] = None,
        owner_principal: Optional[str] = None,
        decision_id: Optional[str] = None,
        limit: int = 100,
    ) -> list[InvestigationCase]:
        data = await self._req(
            "GET",
            "/v1/control/investigations/cases",
            params={
                "status": status,
                "owner_principal": owner_principal,
                "decision_id": decision_id,
                "limit": limit,
            },
        )
        return [InvestigationCase.model_validate(item) for item in data]

    async def investigation_case(self, case_id: str) -> InvestigationCase:
        data = await self._req("GET", f"/v1/control/investigations/cases/{case_id}")
        return InvestigationCase.model_validate(data)

    async def update_investigation_case(
        self, case_id: str, update: InvestigationCaseUpdate
    ) -> InvestigationCase:
        data = await self._req(
            "PATCH",
            f"/v1/control/investigations/cases/{case_id}",
            # Preserve explicitly-set nulls; the API uses field presence to
            # distinguish "clear this owner/summary" from "leave unchanged".
            json_body=update.model_dump(mode="json", exclude_unset=True),
        )
        return InvestigationCase.model_validate(data)

    async def create_remediation_task(
        self, case_id: str, task: RemediationTaskCreate
    ) -> RemediationTask:
        data = await self._req(
            "POST",
            f"/v1/control/investigations/cases/{case_id}/tasks",
            json_body=task.model_dump(mode="json", exclude_none=True),
        )
        return RemediationTask.model_validate(data)

    async def remediation_tasks(
        self, case_id: str, *, status: Optional[str] = None
    ) -> list[RemediationTask]:
        data = await self._req(
            "GET",
            f"/v1/control/investigations/cases/{case_id}/tasks",
            params={"status": status},
        )
        return [RemediationTask.model_validate(item) for item in data]

    async def update_remediation_task(
        self, task_id: str, update: RemediationTaskUpdate
    ) -> RemediationTask:
        data = await self._req(
            "PATCH",
            f"/v1/control/investigations/tasks/{task_id}",
            # Preserve explicitly-set nulls for owner and due-date clearing.
            json_body=update.model_dump(mode="json", exclude_unset=True),
        )
        return RemediationTask.model_validate(data)

    async def close_remediation_task(
        self, task_id: str, attestation: ClosureAttestationCreate
    ) -> AttestedClosure:
        data = await self._req(
            "POST",
            f"/v1/control/investigations/tasks/{task_id}/close",
            json_body=attestation.model_dump(mode="json", exclude_none=True),
        )
        return AttestedClosure.model_validate(data)

    async def close_investigation_case(
        self, case_id: str, attestation: ClosureAttestationCreate
    ) -> AttestedClosure:
        data = await self._req(
            "POST",
            f"/v1/control/investigations/cases/{case_id}/close",
            json_body=attestation.model_dump(mode="json", exclude_none=True),
        )
        return AttestedClosure.model_validate(data)

    async def closure_attestation(
        self,
        resource_type: Literal["case", "task"],
        resource_id: str,
        *,
        include_statement: bool = False,
    ) -> ClosureAttestation:
        data = await self._req(
            "GET",
            f"/v1/control/investigations/{resource_type}/{resource_id}/attestation",
            params={"include_statement": str(include_statement).lower()},
        )
        return ClosureAttestation.model_validate(data)

    async def whoami(self) -> Principal:
        data = await self._req("GET", "/v1/identity/whoami")
        return Principal.model_validate(data)

    async def create_workload_credential(
        self, request: WorkloadCredentialCreate
    ) -> WorkloadCredentialCreated:
        """Issue one expiring tenant credential; ``secret`` is returned once."""
        data = await self._req(
            "POST",
            "/v1/identity/workload-credentials",
            json_body=request.model_dump(mode="json", exclude_none=True),
        )
        return WorkloadCredentialCreated.model_validate(data)

    async def workload_credentials(
        self,
        *,
        include_revoked: bool = False,
        include_expired: bool = False,
        limit: int = 100,
    ) -> list[WorkloadCredential]:
        data = await self._req(
            "GET",
            "/v1/identity/workload-credentials",
            params={
                "include_revoked": include_revoked,
                "include_expired": include_expired,
                "limit": limit,
            },
        )
        return [WorkloadCredential.model_validate(item) for item in data]

    async def workload_credential(self, credential_id: str) -> WorkloadCredential:
        data = await self._req(
            "GET", f"/v1/identity/workload-credentials/{credential_id}"
        )
        return WorkloadCredential.model_validate(data)

    async def rotate_workload_credential(
        self,
        credential_id: str,
        request: WorkloadCredentialRotate,
    ) -> WorkloadCredentialCreated:
        data = await self._req(
            "POST",
            f"/v1/identity/workload-credentials/{credential_id}/rotate",
            json_body=request.model_dump(mode="json"),
        )
        return WorkloadCredentialCreated.model_validate(data)

    async def revoke_workload_credential(
        self, credential_id: str, *, expected_version: int
    ) -> None:
        await self._req(
            "DELETE",
            f"/v1/identity/workload-credentials/{credential_id}",
            params={"expected_version": expected_version},
        )

    async def discovery(self) -> LiansDiscovery:
        data = await self._req("GET", "/.well-known/lians")
        return LiansDiscovery.model_validate(data)

    async def platform_capabilities(self) -> PlatformCapabilities:
        data = await self._req("GET", "/v1/platform/capabilities")
        return PlatformCapabilities.model_validate(data)

    async def platform_readiness(self) -> PlatformReadiness:
        """Inspect deployment configuration readiness; requires admin scope."""
        data = await self._req("GET", "/v1/platform/readiness")
        return PlatformReadiness.model_validate(data)

    async def investigator_queue(
        self, *, limit: int = 100, scan_limit: int = 500
    ) -> InvestigatorQueue:
        data = await self._req(
            "GET",
            "/v1/investigator/queue",
            params={"limit": limit, "scan_limit": scan_limit},
        )
        return InvestigatorQueue.model_validate(data)

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
        """Reconstruct one decision; sensitive disclosure is admin-only."""
        data = await self._req(
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
        )
        return DecisionInvestigationReport.model_validate(data)

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

    async def get_lineage(
        self,
        memory_id: str,
        *,
        max_nodes: int = 1000,
    ) -> MemoryLineageResult:
        """Return the full belief provenance chain for a memory."""
        data = await self._req(
            "GET",
            f"/v1/memories/{memory_id}/lineage",
            params={"max_nodes": max_nodes},
        )
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
        Return matches from a bounded structured-fact scan ordered by event_time.

        Query by ticker + metric — no memory_id needed.  Entity normalization is
        automatic: 'Apple Inc.', ISIN 'US0378331005', and 'AAPL' resolve to the
        same series. Inspect ``scan_complete`` before treating the page as
        exhaustive.
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
        after_event_time: str | datetime | None = None,
        after_id: UUID | str | None = None,
        recorded_as_of: str | datetime | None = None,
    ) -> KnowledgeSnapshot:
        """
        Read a deterministic page of an agent's knowledge state at a point in time.

        ``total`` is exact; only ``complete=True`` means the response includes
        the whole unranked snapshot. Otherwise continue with the returned cursor
        and retain its ``recorded_as_of`` transaction-time watermark.
        """
        ts = as_of.isoformat() if isinstance(as_of, datetime) else as_of
        cursor_time = (
            after_event_time.isoformat()
            if isinstance(after_event_time, datetime)
            else after_event_time
        )
        recorded_cutoff = (
            recorded_as_of.isoformat()
            if isinstance(recorded_as_of, datetime)
            else recorded_as_of
        )
        data = await self._req("GET", "/v1/snapshot", params={
            "agent_id": agent_id,
            "as_of": ts,
            "limit": limit,
            "after_event_time": cursor_time,
            "after_id": str(after_id) if after_id is not None else None,
            "recorded_as_of": recorded_cutoff,
        })
        return KnowledgeSnapshot.model_validate(data)

    # ── Backtest ──────────────────────────────────────────────────────────────

    async def backtest_check(
        self,
        agent_id: str,
        simulation_as_of: str | datetime,
        *,
        flag_limit: int = 1000,
        after_event_time: str | datetime | None = None,
        after_id: UUID | str | None = None,
    ) -> ContaminationReport:
        """
        Detect lookahead bias in a backtest simulation.

        Exact visible Lians-memory counts determine ``is_clean``; ``flags`` is
        a bounded page. This does not attest to unrecorded external inputs.
        """
        ts = simulation_as_of.isoformat() if isinstance(simulation_as_of, datetime) else simulation_as_of
        cursor_time = (
            after_event_time.isoformat()
            if isinstance(after_event_time, datetime)
            else after_event_time
        )
        data = await self._req("POST", "/v1/backtest/check", json_body={
            "agent_id": agent_id,
            "simulation_as_of": ts,
            "flag_limit": flag_limit,
            "after_event_time": cursor_time,
            "after_id": str(after_id) if after_id is not None else None,
        })
        return ContaminationReport.model_validate(data)

    # ── Compliance / Erasure ──────────────────────────────────────────────────

    async def erase_subject(
        self,
        subject_id: str,
        request_ref: str,
        *,
        idempotency_key: str | None = None,
    ) -> EraseResult:
        """
        GDPR Art. 17 / CCPA crypto-shred.

        Atomically destroys the subject DEK and returns the durable bounded
        derivative-store scrub job. Poll ``subject_erasure_status`` to completion.
        """
        data = await self._req(
            "POST",
            "/v1/erase",
            json_body={"subject_id": subject_id, "request_ref": request_ref},
            extra_headers=(
                {"Idempotency-Key": idempotency_key}
                if idempotency_key is not None
                else None
            ),
        )
        return EraseResult.model_validate(data)

    async def subject_erasure_status(self, job_id: UUID | str) -> EraseResult:
        data = await self._req("GET", f"/v1/erase/jobs/{job_id}")
        return EraseResult.model_validate(data)

    async def retry_subject_erasure(self, job_id: UUID | str) -> EraseResult:
        data = await self._req("POST", f"/v1/erase/jobs/{job_id}/retry")
        return EraseResult.model_validate(data)

    async def erasure_certificate(
        self,
        subject_id: str,
        *,
        limit: int = 100,
        after_memory_id: UUID | str | None = None,
    ) -> ErasureCertificate:
        """
        Retrieve a cryptographic proof-of-erasure certificate.

        Returns one exact, bounded evidence-hash page after the durable job has
        completed. Follow ``next_memory_id`` while ``has_more`` is true.
        """
        data = await self._req(
            "GET",
            f"/v1/erase/{subject_id}/certificate",
            params={
                "limit": limit,
                "after_memory_id": (
                    str(after_memory_id) if after_memory_id is not None else None
                ),
            },
        )
        return ErasureCertificate.model_validate(data)

    async def erasure_certificate_by_job(
        self,
        job_id: UUID | str,
        *,
        limit: int = 100,
        after_memory_id: UUID | str | None = None,
    ) -> ErasureCertificate:
        data = await self._req(
            "GET",
            f"/v1/erase/jobs/{job_id}/certificate",
            params={
                "limit": limit,
                "after_memory_id": (
                    str(after_memory_id) if after_memory_id is not None else None
                ),
            },
        )
        return ErasureCertificate.model_validate(data)

    async def compliance_report(
        self,
        *,
        from_: Optional[str | datetime] = None,
        to: Optional[str | datetime] = None,
        verify: bool = False,
        subject_id_limit: int = 1_000,
    ) -> ComplianceReport:
        """
        Generate a compliance report for the caller's namespace.

        Covers memory counts, audit chain status, erasures, open conflicts,
        supersession statistics, and retention policy snapshot.
        Ready for SEC/FINRA/CFTC examiners.
        """
        params: dict[str, Any] = {
            "verify": verify,
            "subject_id_limit": subject_id_limit,
        }
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
        after_detected_at: Optional[datetime | str] = None,
        after_id: Optional[str] = None,
    ) -> ConflictListResult:
        """List one exact-total, keyset-paginated conflict page."""
        data = await self._req("GET", "/v1/conflicts", params={
            "status": status,
            "limit": limit,
            "after_detected_at": (
                after_detected_at.isoformat()
                if isinstance(after_detected_at, datetime)
                else after_detected_at
            ),
            "after_id": after_id,
        })
        return ConflictListResult.model_validate(data)

    async def resolve_conflict(
        self,
        conflict_id: str,
        resolution: Literal["accept_a", "accept_b", "dismiss"],
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
        before_chain_position: Optional[int] = None,
    ) -> SupersessionReviewResult:
        """Return one exact-total, keyset-paginated unresolved review page."""
        data = await self._req("GET", "/v1/supersessions/review", params={
            "threshold": threshold,
            "limit": limit,
            "before_chain_position": before_chain_position,
        })
        return SupersessionReviewResult.model_validate(data)

    async def confirm_supersession(
        self,
        memory_id: str,
        *,
        expected_superseded_by: Optional[str],
        reviewer_note: Optional[str] = None,
    ) -> SupersessionActionResult:
        """Confirm using the review item's ``superseded_by`` version token."""
        data = await self._req(
            "PATCH",
            f"/v1/supersessions/{memory_id}",
            json_body={
                "action": "confirm",
                "expected_superseded_by": expected_superseded_by,
                "reviewer_note": reviewer_note,
            },
        )
        return SupersessionActionResult.model_validate(data)

    async def reject_supersession(
        self,
        memory_id: str,
        *,
        expected_superseded_by: Optional[str],
        reviewer_note: Optional[str] = None,
    ) -> SupersessionActionResult:
        """Reject using the review item's ``superseded_by`` version token."""
        data = await self._req(
            "PATCH",
            f"/v1/supersessions/{memory_id}",
            json_body={
                "action": "reject",
                "expected_superseded_by": expected_superseded_by,
                "reviewer_note": reviewer_note,
            },
        )
        return SupersessionActionResult.model_validate(data)

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
        expected_updated_at: datetime,
        enabled: Optional[bool] = None,
        events: Optional[list[str]] = None,
        description: Optional[str] = None,
    ) -> WebhookEndpoint:
        """Update a webhook endpoint's enabled state, events, or description."""
        body: dict[str, Any] = {
            "expected_updated_at": expected_updated_at.isoformat()
        }
        if enabled is not None:
            body["enabled"] = enabled
        if events is not None:
            body["events"] = events
        if description is not None:
            body["description"] = description
        data = await self._req("PATCH", f"/v1/webhooks/{endpoint_id}", json_body=body)
        return WebhookEndpoint.model_validate(data)

    async def delete_webhook(
        self,
        endpoint_id: str,
        *,
        expected_updated_at: datetime,
    ) -> None:
        """Remove a webhook endpoint permanently."""
        await self._req(
            "DELETE",
            f"/v1/webhooks/{endpoint_id}",
            params={"expected_updated_at": expected_updated_at.isoformat()},
        )

    async def webhook_deliveries(
        self,
        endpoint_id: str,
        *,
        limit: int = 50,
        after_created_at: Optional[datetime] = None,
        after_id: Optional[str] = None,
    ) -> WebhookDeliveryListResult:
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
        data = await self._req(
            "GET", f"/v1/webhooks/{endpoint_id}/deliveries", params=params
        )
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
        after_chain_position: int | None = None,
        through_chain_position: int | None = None,
    ) -> AuditExportResult:
        """
        Export one exact-count, keyset-paginated audit-log page.

        ``total_rows`` is exact before the cursor. Follow
        ``next_chain_position`` while ``has_more`` is true; only an uncursored
        result with ``complete=true`` contains the full filtered collection.
        Retain ``snapshot_max_chain_position`` as ``through_chain_position`` on
        every continuation request.
        Requires ``admin_secret`` to be set on the client.
        """
        params: dict[str, Any] = {
            "namespace": namespace,
            "limit": limit,
            "verify": verify,
            "after_chain_position": after_chain_position,
            "through_chain_position": through_chain_position,
        }
        if from_:
            params["from"] = from_.isoformat() if isinstance(from_, datetime) else from_
        if to:
            params["to"] = to.isoformat() if isinstance(to, datetime) else to
        data = await self._req("GET", "/v1/admin/audit/export", params=params, admin=True)
        return AuditExportResult.model_validate(data)

    async def verify_chain(self, namespace: str) -> AuditChainVerifyResult:
        """
        Verify the SEC 17a-4 tamper-evidence hash chain.

        Returns an ``ok``, ``partial``, or ``tampered`` status together with
        explicit truncation and bounded violation details.
        Requires ``admin_secret`` to be set on the client.
        """
        data = await self._req(
            "GET",
            "/v1/admin/audit/verify",
            params={"namespace": namespace},
            admin=True,
        )
        return AuditChainVerifyResult.model_validate(data)
