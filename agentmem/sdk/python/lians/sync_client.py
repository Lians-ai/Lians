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
from typing import Any, Literal, Optional

from .client import AsyncLiansClient
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


class LiansClient:
    """Synchronous HTTP client for the Lians REST API."""

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
        self._async = AsyncLiansClient(
            base_url=base_url,
            api_key=api_key,
            access_token=access_token,
            admin_secret=admin_secret,
            timeout=timeout,
            max_retries=max_retries,
            backoff_factor=backoff_factor,
            max_retry_delay=max_retry_delay,
        )
        self._loop = asyncio.new_event_loop()

    # ── Lifecycle ─────────────────────────────────────────────────────────────

    def __enter__(self) -> "LiansClient":
        return self

    def __exit__(self, *_: Any) -> None:
        self.close()

    def close(self) -> None:
        if self._loop.is_closed():
            return
        self._loop.run_until_complete(self._async.aclose())
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
        idempotency_key: Optional[str] = None,
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
                idempotency_key=idempotency_key,
            )
        )

    def batch_add(
        self,
        memories: list[dict[str, Any]],
        idempotency_key: Optional[str] = None,
    ) -> dict:
        """
        Add multiple memories in a single request.

        Returns a MemoryBatchResult dict with ``added`` count and ``memories`` list.
        Items are processed sequentially so later items can supersede earlier ones.
        """
        return self._loop.run_until_complete(
            self._async.batch_add(memories, idempotency_key=idempotency_key)
        )

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
        surface_conflicts: bool = True,
        max_conflicts: int = 5,
        filters: Optional[dict[str, Any]] = None,
    ) -> dict:
        """Build a token-budgeted, ready-to-inject context block. Returns a dict
        ``{context, memories, token_estimate, truncated}``. Open conflicts ride
        at the top until adjudicated; ``surface_conflicts=False`` opts out."""
        return self._loop.run_until_complete(
            self._async.context(
                agent_id=agent_id, query=query, k=k, as_of=as_of, filters=filters,
                max_tokens=max_tokens, header=header, mmr=mmr,
                surface_conflicts=surface_conflicts, max_conflicts=max_conflicts,
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
        k: int = 20,
        memory_limit: int = 1000,
        event_limit: int = 5000,
    ) -> dict:
        """Audit reconstruction. Returns AuditReconstructResult as a dict."""
        return self._loop.run_until_complete(
            self._async.reconstruct(
                agent_id=agent_id,
                as_of=as_of,
                query=query,
                k=k,
                memory_limit=memory_limit,
                event_limit=event_limit,
            )
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
        before_chain_position: Optional[int] = None,
    ) -> dict:
        """
        Return supersession events whose confidence is below *threshold*.

        Returns a SupersessionReviewResult dict with an ``items`` list.
        """
        options: dict[str, object] = {"threshold": threshold, "limit": limit}
        if before_chain_position is not None:
            options["before_chain_position"] = before_chain_position
        return self._loop.run_until_complete(self._async.review_supersessions(**options))

    def confirm_supersession(
        self,
        memory_id: str,
        *,
        expected_superseded_by: Optional[str],
        reviewer_note: Optional[str] = None,
    ) -> SupersessionActionResult:
        """Confirm a supersession was correct. Returns SupersessionActionResult."""
        return self._loop.run_until_complete(
            self._async.confirm_supersession(
                memory_id=memory_id,
                expected_superseded_by=expected_superseded_by,
                reviewer_note=reviewer_note,
            )
        )

    def reject_supersession(
        self,
        memory_id: str,
        *,
        expected_superseded_by: Optional[str],
        reviewer_note: Optional[str] = None,
    ) -> SupersessionActionResult:
        """Reject a supersession — restores the old memory as valid."""
        return self._loop.run_until_complete(
            self._async.reject_supersession(
                memory_id=memory_id,
                expected_superseded_by=expected_superseded_by,
                reviewer_note=reviewer_note,
            )
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
        limit: int = 10_000,
        verify: bool = False,
        after_chain_position: Optional[int] = None,
        through_chain_position: Optional[int] = None,
    ) -> dict:
        """
        Export one exact-count, keyset-paginated audit-log page.

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
                after_chain_position=after_chain_position,
                through_chain_position=through_chain_position,
            )
        )

    # ── Snapshot (audit reconstruction) ───────────────────────────────────────

    def snapshot(
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
        return self._loop.run_until_complete(
            self._async.snapshot(
                agent_id=agent_id,
                as_of=as_of,
                limit=limit,
                after_event_time=after_event_time,
                after_id=after_id,
                recorded_as_of=recorded_as_of,
            )
        )

    # ── Backtest contamination ─────────────────────────────────────────────────

    def backtest_check(
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

        ``is_clean`` is exact for recorded memories in the authenticated scope;
        it does not attest to unrecorded external input.
        """
        return self._loop.run_until_complete(
            self._async.backtest_check(
                agent_id=agent_id,
                simulation_as_of=simulation_as_of,
                flag_limit=flag_limit,
                after_event_time=after_event_time,
                after_id=after_id,
            )
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
                  direction="any", normalize=False, max_nodes=5000,
                  max_edges=20000) -> dict:
        """Entities within ``depth`` hops of ``entity`` (optional ``as_of``)."""
        return self._loop.run_until_complete(self._async.neighbors(
            agent_id=agent_id, entity=entity, depth=depth, as_of=as_of,
            rel_types=rel_types, direction=direction, normalize=normalize,
            max_nodes=max_nodes, max_edges=max_edges,
        ))

    def path(self, agent_id, src_entity, dst_entity, max_depth=4, as_of=None,
             rel_types=None, normalize=False, max_nodes=5000,
             max_edges=20000) -> dict:
        """Shortest connection between two entities — the COI / related-party query."""
        return self._loop.run_until_complete(self._async.path(
            agent_id=agent_id, src_entity=src_entity, dst_entity=dst_entity,
            max_depth=max_depth, as_of=as_of, rel_types=rel_types, normalize=normalize,
            max_nodes=max_nodes, max_edges=max_edges,
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
        after_detected_at: Optional[str] = None,
        after_id: Optional[str] = None,
    ) -> dict:
        """List detected fact contradictions. Returns ConflictListResult."""
        options: dict[str, object] = {"status": status, "limit": limit}
        if after_detected_at is not None and after_id is not None:
            options.update(
                after_detected_at=after_detected_at,
                after_id=after_id,
            )
        return self._loop.run_until_complete(
            self._async.list_conflicts(**options)
        )

    def resolve_conflict(
        self,
        conflict_id: str,
        resolution: Literal["accept_a", "accept_b", "dismiss"],
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
        subject_id_limit: int = 1_000,
    ) -> dict:
        """Generate a compliance report for the caller's namespace."""
        return self._loop.run_until_complete(
            self._async.compliance_report(
                from_dt=from_dt,
                to_dt=to_dt,
                verify_chain=verify_chain,
                subject_id_limit=subject_id_limit,
            )
        )

    def record_decision(self, *, agent_id: str, decision_type: str, outcome: str,
                        decided_at: datetime, reason_codes: Optional[list[str]] = None,
                        knowledge_as_of: Optional[datetime] = None,
                        knowledge_recorded_as_of: Optional[datetime] = None,
                        idempotency_key: Optional[str] = None,
                        **fields: Any) -> DecisionOut:
        """Append a consequential AI decision to the dispute ledger."""
        return self._loop.run_until_complete(self._async.record_decision(
            agent_id=agent_id, decision_type=decision_type, outcome=outcome,
            decided_at=decided_at, reason_codes=reason_codes,
            knowledge_as_of=knowledge_as_of,
            knowledge_recorded_as_of=knowledge_recorded_as_of,
            idempotency_key=idempotency_key,
            **fields,
        ))

    def decisions(self, **filters: Any) -> list[dict]:
        return self._loop.run_until_complete(self._async.decisions(**filters))

    def decisions_page(self, **filters: Any) -> CompatibilityListPage[DecisionOut]:
        """List decisions with exact totals and a paired next cursor."""

        return self._loop.run_until_complete(self._async.decisions_page(**filters))

    def evidence_artifacts_page(
        self, **filters: Any
    ) -> CompatibilityListPage[EvidenceArtifactOut]:
        """List evidence artifacts with exact totals and a paired next cursor."""

        return self._loop.run_until_complete(
            self._async.evidence_artifacts_page(**filters)
        )

    def review_decision(
        self,
        decision_id: str,
        status: Literal["requested", "affirmed", "overturned", "withdrawn"],
        reviewer: Optional[str] = None,
        note: Optional[str] = None,
        idempotency_key: Optional[str] = None,
    ) -> DecisionOut:
        return self._loop.run_until_complete(
            self._async.review_decision(
                decision_id,
                status,
                reviewer,
                note,
                idempotency_key=idempotency_key,
            )
        )

    def evidence_pack(self, decision_id: str, verify: bool = True) -> dict:
        return self._loop.run_until_complete(self._async.evidence_pack(decision_id, verify))

    def decision_evidence_graph(
        self,
        decision_id: str,
        *,
        limit: int = 500,
        after_relation: Optional[str] = None,
        after_link_id: Optional[str] = None,
    ) -> dict:
        return self._loop.run_until_complete(
            self._async.decision_evidence_graph(
                decision_id,
                limit=limit,
                after_relation=after_relation,
                after_link_id=after_link_id,
            )
        )

    def decision_review_history(
        self,
        decision_id: str,
        *,
        include_notes: bool = False,
        after_sequence: int = 0,
        limit: int = 500,
    ) -> dict:
        return self._loop.run_until_complete(
            self._async.decision_review_history(
                decision_id,
                include_notes=include_notes,
                after_sequence=after_sequence,
                limit=limit,
            )
        )

    def decision_receipt(
        self,
        decision_id: str,
        verify: bool = True,
        include_source_content: bool = False,
    ) -> dict:
        return self._loop.run_until_complete(
            self._async.decision_receipt(
                decision_id,
                verify,
                include_source_content,
            )
        )

    def verify_decision_receipt(
        self,
        receipt: dict,
        *,
        trusted_public_key: Optional[str] = None,
        require_signature: bool = False,
    ) -> dict:
        return self._loop.run_until_complete(
            self._async.verify_decision_receipt(
                receipt,
                trusted_public_key=trusted_public_key,
                require_signature=require_signature,
            )
        )

    def assess_decision_impact(
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
        return self._loop.run_until_complete(
            self._async.assess_decision_impact(
                dependency_kind,
                dependency_value,
                change_type=change_type,
                occurred_at=occurred_at,
                note=note,
                agent_id=agent_id,
                limit=limit,
                record_event=record_event,
            )
        )

    def start_exhaustive_impact_assessment(
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
        return self._loop.run_until_complete(
            self._async.start_exhaustive_impact_assessment(
                idempotency_key=idempotency_key,
                dependency_kind=dependency_kind,
                dependency_value=dependency_value,
                change_type=change_type,
                occurred_at=occurred_at,
                note=note,
                record_event=record_event,
            )
        )

    def get_exhaustive_impact_assessment(
        self,
        assessment_id: str,
    ) -> ExhaustiveImpactAssessmentStatus:
        return self._loop.run_until_complete(
            self._async.get_exhaustive_impact_assessment(assessment_id)
        )

    def advance_exhaustive_impact_assessment(
        self,
        assessment_id: str,
        *,
        page_size: int = 250,
        max_pages: int = 1,
    ) -> ExhaustiveImpactAssessmentStatus:
        return self._loop.run_until_complete(
            self._async.advance_exhaustive_impact_assessment(
                assessment_id,
                page_size=page_size,
                max_pages=max_pages,
            )
        )

    def list_exhaustive_impact_assessment_results(
        self,
        assessment_id: str,
        *,
        after: int = 0,
        limit: int = 100,
    ) -> ExhaustiveImpactAssessmentResults:
        return self._loop.run_until_complete(
            self._async.list_exhaustive_impact_assessment_results(
                assessment_id,
                after=after,
                limit=limit,
            )
        )

    # -- Universal Recorder -------------------------------------------------

    def ingest_recorder_event(
        self,
        envelope: RecorderEnvelope,
        *,
        idempotency_key: Optional[str] = None,
    ) -> RecorderIngestResult:
        return self._loop.run_until_complete(
            self._async.ingest_recorder_event(
                envelope, idempotency_key=idempotency_key
            )
        )

    def ingest_recorder_batch(
        self,
        events: list[RecorderEnvelope],
        *,
        atomic: bool = True,
    ) -> RecorderBatchResult:
        return self._loop.run_until_complete(
            self._async.ingest_recorder_batch(events, atomic=atomic)
        )

    def recorder_run_readiness(self, run_id: str) -> RecorderRunReadiness:
        return self._loop.run_until_complete(
            self._async.recorder_run_readiness(run_id)
        )

    def recorder_run_events(
        self, run_id: str, *, limit: int = 500
    ) -> list[RecorderEvent]:
        return self._loop.run_until_complete(
            self._async.recorder_run_events(run_id, limit=limit)
        )

    def recorder_run_events_page(
        self,
        run_id: str,
        *,
        limit: int = 500,
        before_recorded_at: str | datetime | None = None,
        before_id: str | None = None,
    ) -> CompatibilityListPage[RecorderEvent]:
        return self._loop.run_until_complete(
            self._async.recorder_run_events_page(
                run_id,
                limit=limit,
                before_recorded_at=before_recorded_at,
                before_id=before_id,
            )
        )

    def recorder_readiness(
        self, *, agent_id: Optional[str] = None, limit: int = 50
    ) -> FirstReceiptReadiness:
        return self._loop.run_until_complete(
            self._async.recorder_readiness(agent_id=agent_id, limit=limit)
        )

    def recorder_evidence_index_job(
        self,
        job_id: str,
    ) -> RecorderEvidenceIndexJob:
        return self._loop.run_until_complete(
            self._async.recorder_evidence_index_job(job_id)
        )

    def recorder_evidence_index_job_for_decision(
        self,
        decision_id: str,
    ) -> RecorderEvidenceIndexJob:
        return self._loop.run_until_complete(
            self._async.recorder_evidence_index_job_for_decision(decision_id)
        )

    def retry_recorder_evidence_index_job(
        self,
        job_id: str,
    ) -> RecorderEvidenceIndexJob:
        return self._loop.run_until_complete(
            self._async.retry_recorder_evidence_index_job(job_id)
        )

    # -- Runtime Gate and investigations -----------------------------------

    def create_receipt_issuer(self, issuer: IssuerCreate) -> ReceiptIssuer:
        return self._loop.run_until_complete(
            self._async.create_receipt_issuer(issuer)
        )

    def receipt_issuers(
        self,
        *,
        include_revoked: bool = False,
        offset: int = 0,
        limit: int = 100,
    ) -> list[ReceiptIssuer]:
        return self._loop.run_until_complete(
            self._async.receipt_issuers(
                include_revoked=include_revoked,
                offset=offset,
                limit=limit,
            )
        )

    def revoke_receipt_issuer(
        self,
        issuer_id: str,
        *,
        reason: str,
        actor_id: Optional[str] = None,
    ) -> ReceiptIssuer:
        return self._loop.run_until_complete(
            self._async.revoke_receipt_issuer(
                issuer_id, reason=reason, actor_id=actor_id
            )
        )

    def register_trusted_receipt_key(
        self, issuer_id: str, key: TrustedKeyCreate
    ) -> TrustedReceiptKey:
        return self._loop.run_until_complete(
            self._async.register_trusted_receipt_key(issuer_id, key)
        )

    def trusted_receipt_keys(
        self,
        issuer_id: str,
        *,
        include_revoked: bool = False,
        offset: int = 0,
        limit: int = 100,
    ) -> list[TrustedReceiptKey]:
        return self._loop.run_until_complete(
            self._async.trusted_receipt_keys(
                issuer_id,
                include_revoked=include_revoked,
                offset=offset,
                limit=limit,
            )
        )

    def resolve_trusted_receipt_key(
        self, key_id: str, *, at: Optional[datetime | str] = None
    ) -> TrustedReceiptKey:
        return self._loop.run_until_complete(
            self._async.resolve_trusted_receipt_key(key_id, at=at)
        )

    def rotate_trusted_receipt_key(
        self, issuer_id: str, key_id: str, replacement: TrustedKeyRotate
    ) -> TrustedReceiptKey:
        return self._loop.run_until_complete(
            self._async.rotate_trusted_receipt_key(issuer_id, key_id, replacement)
        )

    def revoke_trusted_receipt_key(
        self,
        issuer_id: str,
        key_id: str,
        *,
        reason: str,
        actor_id: Optional[str] = None,
    ) -> TrustedReceiptKey:
        return self._loop.run_until_complete(
            self._async.revoke_trusted_receipt_key(
                issuer_id, key_id, reason=reason, actor_id=actor_id
            )
        )

    def create_gate_policy(self, policy: GatePolicySetCreate) -> GatePolicySet:
        return self._loop.run_until_complete(self._async.create_gate_policy(policy))

    def gate_policies(
        self,
        *,
        name: Optional[str] = None,
        status: Optional[str] = None,
        include_rules: bool = True,
        offset: int = 0,
        limit: int = 20,
    ) -> list[GatePolicySet]:
        return self._loop.run_until_complete(
            self._async.gate_policies(
                name=name,
                status=status,
                include_rules=include_rules,
                offset=offset,
                limit=limit,
            )
        )

    def gate_policy(self, policy_id: str) -> GatePolicySet:
        return self._loop.run_until_complete(self._async.gate_policy(policy_id))

    def activate_gate_policy(
        self, policy_id: str, *, actor_id: Optional[str] = None
    ) -> GatePolicySet:
        return self._loop.run_until_complete(
            self._async.activate_gate_policy(policy_id, actor_id=actor_id)
        )

    def create_gate_approval(
        self, attestation: GateApprovalAttestationCreate
    ) -> GateApprovalAttestation:
        return self._loop.run_until_complete(
            self._async.create_gate_approval(attestation)
        )

    def supersede_gate_approval(
        self,
        approval_id: str,
        successor: GateApprovalAttestationSupersede,
    ) -> GateApprovalAttestation:
        return self._loop.run_until_complete(
            self._async.supersede_gate_approval(approval_id, successor)
        )

    def gate_approvals(
        self,
        *,
        context_hash: Optional[str] = None,
        decision_id: Optional[str] = None,
        status: Optional[str] = None,
        only_current: bool = True,
        include_statement: bool = False,
        limit: int = 100,
    ) -> list[GateApprovalAttestation]:
        return self._loop.run_until_complete(
            self._async.gate_approvals(
                context_hash=context_hash,
                decision_id=decision_id,
                status=status,
                only_current=only_current,
                include_statement=include_statement,
                limit=limit,
            )
        )

    def gate_approval(
        self, approval_id: str, *, include_statement: bool = False
    ) -> GateApprovalAttestation:
        return self._loop.run_until_complete(
            self._async.gate_approval(
                approval_id, include_statement=include_statement
            )
        )

    def evaluate_gate(self, request: GateEvaluationRequest) -> GateEvaluationResult:
        return self._loop.run_until_complete(self._async.evaluate_gate(request))

    def consume_gate_execution_permit(
        self, request: GateExecutionPermitConsume
    ) -> GateExecutionPermitConsumption:
        return self._loop.run_until_complete(
            self._async.consume_gate_execution_permit(request)
        )

    def gate_evaluations(
        self,
        *,
        disposition: Optional[str] = None,
        decision_id: Optional[str] = None,
        limit: int = 100,
    ) -> list[GateDecision]:
        return self._loop.run_until_complete(
            self._async.gate_evaluations(
                disposition=disposition,
                decision_id=decision_id,
                limit=limit,
            )
        )

    def gate_evaluation(self, evaluation_id: str) -> GateDecision:
        return self._loop.run_until_complete(
            self._async.gate_evaluation(evaluation_id)
        )

    def create_investigation_case(
        self, case: InvestigationCaseCreate
    ) -> InvestigationCase:
        return self._loop.run_until_complete(
            self._async.create_investigation_case(case)
        )

    def investigation_cases(
        self,
        *,
        status: Optional[str] = None,
        owner_principal: Optional[str] = None,
        decision_id: Optional[str] = None,
        limit: int = 100,
    ) -> list[InvestigationCase]:
        return self._loop.run_until_complete(
            self._async.investigation_cases(
                status=status,
                owner_principal=owner_principal,
                decision_id=decision_id,
                limit=limit,
            )
        )

    def investigation_case(self, case_id: str) -> InvestigationCase:
        return self._loop.run_until_complete(
            self._async.investigation_case(case_id)
        )

    def update_investigation_case(
        self, case_id: str, update: InvestigationCaseUpdate
    ) -> InvestigationCase:
        return self._loop.run_until_complete(
            self._async.update_investigation_case(case_id, update)
        )

    def create_remediation_task(
        self, case_id: str, task: RemediationTaskCreate
    ) -> RemediationTask:
        return self._loop.run_until_complete(
            self._async.create_remediation_task(case_id, task)
        )

    def remediation_tasks(
        self,
        case_id: str,
        *,
        status: Optional[str] = None,
        offset: int = 0,
        limit: int = 100,
    ) -> list[RemediationTask]:
        return self._loop.run_until_complete(
            self._async.remediation_tasks(
                case_id,
                status=status,
                offset=offset,
                limit=limit,
            )
        )

    def update_remediation_task(
        self, task_id: str, update: RemediationTaskUpdate
    ) -> RemediationTask:
        return self._loop.run_until_complete(
            self._async.update_remediation_task(task_id, update)
        )

    def close_remediation_task(
        self, task_id: str, attestation: ClosureAttestationCreate
    ) -> AttestedClosure:
        return self._loop.run_until_complete(
            self._async.close_remediation_task(task_id, attestation)
        )

    def close_investigation_case(
        self, case_id: str, attestation: ClosureAttestationCreate
    ) -> AttestedClosure:
        return self._loop.run_until_complete(
            self._async.close_investigation_case(case_id, attestation)
        )

    def closure_attestation(
        self,
        resource_type: Literal["case", "task"],
        resource_id: str,
        *,
        include_statement: bool = False,
    ) -> ClosureAttestation:
        return self._loop.run_until_complete(
            self._async.closure_attestation(
                resource_type,
                resource_id,
                include_statement=include_statement,
            )
        )

    def whoami(self) -> Principal:
        return self._loop.run_until_complete(self._async.whoami())

    def create_workload_credential(
        self, request: WorkloadCredentialCreate
    ) -> WorkloadCredentialCreated:
        return self._loop.run_until_complete(
            self._async.create_workload_credential(request)
        )

    def workload_credentials(
        self,
        *,
        include_revoked: bool = False,
        include_expired: bool = False,
        limit: int = 100,
    ) -> list[WorkloadCredential]:
        return self._loop.run_until_complete(
            self._async.workload_credentials(
                include_revoked=include_revoked,
                include_expired=include_expired,
                limit=limit,
            )
        )

    def workload_credentials_page(
        self,
        *,
        include_revoked: bool = False,
        include_expired: bool = False,
        limit: int = 100,
        before_created_at: str | None = None,
        before_id: str | None = None,
    ) -> CompatibilityListPage[WorkloadCredential]:
        return self._loop.run_until_complete(
            self._async.workload_credentials_page(
                include_revoked=include_revoked,
                include_expired=include_expired,
                limit=limit,
                before_created_at=before_created_at,
                before_id=before_id,
            )
        )

    def metering_inventory(self, *, namespace: str | None = None) -> MeteringInventory:
        return self._loop.run_until_complete(
            self._async.metering_inventory(namespace=namespace)
        )

    def metering_events_page(
        self,
        *,
        status: MeteringStatus | None = None,
        namespace: str | None = None,
        limit: int = 100,
        before_updated_at: str | None = None,
        before_id: str | None = None,
    ) -> CompatibilityListPage[MeteringEvent]:
        return self._loop.run_until_complete(
            self._async.metering_events_page(
                status=status,
                namespace=namespace,
                limit=limit,
                before_updated_at=before_updated_at,
                before_id=before_id,
            )
        )

    def replay_metering_event(
        self,
        event_id: str,
        request: MeteringReplayRequest,
    ) -> MeteringEvent:
        return self._loop.run_until_complete(
            self._async.replay_metering_event(event_id, request)
        )

    def scim_tenant_reconciliation(
        self,
        tenant_id: str,
        job_id: str,
    ) -> ScimTenantReconciliation:
        return self._loop.run_until_complete(
            self._async.scim_tenant_reconciliation(tenant_id, job_id)
        )

    def retry_scim_tenant_reconciliation(
        self,
        tenant_id: str,
        job_id: str,
    ) -> ScimTenantReconciliation:
        return self._loop.run_until_complete(
            self._async.retry_scim_tenant_reconciliation(tenant_id, job_id)
        )

    def advance_scim_tenant_reconciliation(
        self,
        tenant_id: str,
        job_id: str,
    ) -> ScimTenantReconciliation:
        return self._loop.run_until_complete(
            self._async.advance_scim_tenant_reconciliation(tenant_id, job_id)
        )

    def workload_credential(self, credential_id: str) -> WorkloadCredential:
        return self._loop.run_until_complete(
            self._async.workload_credential(credential_id)
        )

    def rotate_workload_credential(
        self, credential_id: str, request: WorkloadCredentialRotate
    ) -> WorkloadCredentialCreated:
        return self._loop.run_until_complete(
            self._async.rotate_workload_credential(credential_id, request)
        )

    def revoke_workload_credential(
        self, credential_id: str, *, expected_version: int
    ) -> None:
        return self._loop.run_until_complete(
            self._async.revoke_workload_credential(
                credential_id, expected_version=expected_version
            )
        )

    def discovery(self) -> LiansDiscovery:
        return self._loop.run_until_complete(self._async.discovery())

    def platform_capabilities(self) -> PlatformCapabilities:
        return self._loop.run_until_complete(self._async.platform_capabilities())

    def platform_readiness(self) -> PlatformReadiness:
        return self._loop.run_until_complete(self._async.platform_readiness())

    def investigator_queue(
        self, *, limit: int = 100, scan_limit: int = 500
    ) -> InvestigatorQueue:
        return self._loop.run_until_complete(
            self._async.investigator_queue(limit=limit, scan_limit=scan_limit)
        )

    def investigate_decision(
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
        return self._loop.run_until_complete(
            self._async.investigate_decision(
                decision_id,
                timeline_limit=timeline_limit,
                evidence_limit=evidence_limit,
                control_history_limit=control_history_limit,
                case_limit=case_limit,
                task_limit=task_limit,
                closure_limit=closure_limit,
                include_sensitive=include_sensitive,
                verify_audit=verify_audit,
            )
        )

    def record_event(
        self,
        event_type: str,
        agent_id: str,
        occurred_at: datetime,
        idempotency_key: Optional[str] = None,
        **fields: Any,
    ) -> dict:
        return self._loop.run_until_complete(
            self._async.record_event(
                event_type,
                agent_id,
                occurred_at,
                idempotency_key=idempotency_key,
                **fields,
            )
        )

    def record_events(self, **filters: Any) -> list[dict]:
        return self._loop.run_until_complete(self._async.record_events(**filters))

    def record_events_page(
        self, **filters: Any
    ) -> CompatibilityListPage[LedgerEventOut]:
        """List ledger events with exact totals and a paired next cursor."""

        return self._loop.run_until_complete(self._async.record_events_page(**filters))

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
        *,
        expected_updated_at: datetime | str,
        enabled: Optional[bool] = None,
        events: Optional[list[str]] = None,
        description: Optional[str] = None,
    ) -> dict:
        """Update an endpoint's enabled state, events, or description."""
        return self._loop.run_until_complete(
            self._async.update_webhook(
                endpoint_id=endpoint_id,
                expected_updated_at=expected_updated_at,
                enabled=enabled,
                events=events,
                description=description,
            )
        )

    def delete_webhook(
        self,
        endpoint_id: str,
        *,
        expected_updated_at: datetime | str,
    ) -> None:
        """Remove a webhook endpoint permanently."""
        self._loop.run_until_complete(
            self._async.delete_webhook(
                endpoint_id=endpoint_id,
                expected_updated_at=expected_updated_at,
            )
        )

    def webhook_deliveries(
        self,
        endpoint_id: str,
        limit: int = 50,
        *,
        after_created_at: Optional[datetime] = None,
        after_id: Optional[str] = None,
    ) -> dict:
        """Return one stable keyset page of webhook delivery attempts."""
        return self._loop.run_until_complete(
            self._async.webhook_deliveries(
                endpoint_id=endpoint_id,
                limit=limit,
                after_created_at=after_created_at,
                after_id=after_id,
            )
        )
