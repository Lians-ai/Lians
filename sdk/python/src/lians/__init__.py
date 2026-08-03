"""
lians — source-only compatibility client for Lians.

For published releases, install the canonical distribution::

    pip install lians-sdk

This compatibility package is retained for conformance tests. Do not install it
alongside ``lians-sdk`` because both projects provide the ``lians`` import.

Quick start::

    import asyncio
    import os
    from lians import LiansClient

    async def main():
        async with LiansClient(
            base_url=os.environ["AGENTMEM_URL"],
            api_key=os.environ["AGENTMEM_API_KEY"],
        ) as client:
            # Store a fact
            mem = await client.add_memory(
                agent_id="equity-desk",
                content="AAPL Q1 EPS: $1.52",
                event_time="2026-01-28T00:00:00Z",
                metadata={"ticker": "AAPL", "metric": "eps"},
            )

            # Recall with semantic search
            result = await client.recall(agent_id="equity-desk", query="Apple earnings")

            # Audit reconstruction — complete knowledge state at T
            snapshot = await client.knowledge_snapshot(
                agent_id="equity-desk",
                as_of="2026-03-01T00:00:00Z",
            )

            # Backtest contamination check
            report = await client.backtest_check(
                agent_id="equity-desk",
                simulation_as_of="2026-01-01T00:00:00Z",
            )
            if report.is_clean:
                print("✓ No lookahead bias detected")

    asyncio.run(main())
"""
from .client import SDK_VERSION as __version__
from .client import LiansClient, LiansError
from .recorder import a2a_event, lians_event, mcp_jsonrpc_event, otlp_genai_span
from .types import (
    AttestedClosure,
    AuditChainVerifyResult,
    AuditChainViolation,
    AuditExportResult,
    ClosureAttestation,
    ClosureAttestationCreate,
    ComplianceReport,
    CompatibilityListPage,
    ConflictFlagOut,
    ConflictListResult,
    ConflictResolveResult,
    ContaminationFlag,
    ContaminationReport,
    DecisionDependency,
    DecisionDependencyChange,
    DecisionDependencyChangeType,
    DecisionDependencyKind,
    DecisionEvidenceGraphResult,
    DecisionImpactAnalysisMode,
    DecisionImpactItem,
    DecisionImpactResult,
    DecisionInvestigationReport,
    DecisionOut,
    DecisionReviewEvent,
    DecisionReviewHistoryResult,
    EraseResult,
    ErasureCertificate,
    ErasureMemoryHash,
    SubjectErasureProgress,
    SubjectErasureSnapshot,
    EvidenceArtifactOut,
    ExhaustiveImpactAssessmentAdvance,
    ExhaustiveImpactAssessmentCreate,
    ExhaustiveImpactAssessmentMatch,
    ExhaustiveImpactAssessmentResults,
    ExhaustiveImpactAssessmentState,
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
    GateExecutionPermitIssued,
    GatePolicySet,
    GatePolicySetCreate,
    InvestigationCase,
    InvestigationCaseCreate,
    InvestigationCaseUpdate,
    InvestigatorCollectionWindow,
    InvestigatorQueue,
    InvestigatorReportCoverage,
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
from .webhooks import parse_webhook_payload, verify_webhook_signature

__all__ = [
    "__version__",
    "LiansClient",
    "LiansError",
    "verify_webhook_signature",
    "parse_webhook_payload",
    # Types
    "MemoryOut",
    "MemoryBatchResult",
    "RecallResult",
    "EraseResult",
    "ErasureCertificate",
    "ErasureMemoryHash",
    "SubjectErasureProgress",
    "SubjectErasureSnapshot",
    "MemoryLineageResult",
    "FactHistoryResult",
    "KnowledgeSnapshot",
    "ContaminationFlag",
    "ContaminationReport",
    "ConflictFlagOut",
    "ConflictListResult",
    "ConflictResolveResult",
    "SupersessionReviewResult",
    "SupersessionActionResult",
    "AuditChainViolation",
    "AuditChainVerifyResult",
    "AuditExportResult",
    "ComplianceReport",
    "CompatibilityListPage",
    "WebhookEndpoint",
    "WebhookRegisterResult",
    "WebhookDeliveryListResult",
    # Decision impact analysis
    "DecisionDependencyKind",
    "DecisionDependencyChange",
    "DecisionDependencyChangeType",
    "DecisionImpactAnalysisMode",
    "DecisionOut",
    "LedgerEventOut",
    "EvidenceArtifactOut",
    "DecisionEvidenceGraphResult",
    "DecisionReviewEvent",
    "DecisionReviewHistoryResult",
    "DecisionDependency",
    "DecisionImpactItem",
    "DecisionImpactResult",
    "ExhaustiveImpactAssessmentCreate",
    "ExhaustiveImpactAssessmentAdvance",
    "ExhaustiveImpactAssessmentState",
    "ExhaustiveImpactAssessmentStatus",
    "ExhaustiveImpactAssessmentMatch",
    "ExhaustiveImpactAssessmentResults",
    # Universal Recorder and control plane
    "lians_event",
    "otlp_genai_span",
    "mcp_jsonrpc_event",
    "a2a_event",
    "RecorderEnvelope",
    "RecorderEvent",
    "RecorderEvidenceIndexJob",
    "RecorderIngestResult",
    "RecorderBatchResult",
    "RecorderRunReadiness",
    "FirstReceiptReadiness",
    "GatePolicySetCreate",
    "GatePolicySet",
    "GateEvaluationRequest",
    "GateDecision",
    "GateEvaluationResult",
    "GateExecutionPermitIssued",
    "GateExecutionPermitConsume",
    "GateExecutionPermitConsumption",
    "GateApprovalAttestationCreate",
    "GateApprovalAttestationSupersede",
    "GateApprovalAttestation",
    "InvestigationCaseCreate",
    "InvestigationCaseUpdate",
    "InvestigatorQueue",
    "InvestigatorCollectionWindow",
    "InvestigatorReportCoverage",
    "DecisionInvestigationReport",
    "LiansDiscovery",
    "PlatformCapabilities",
    "PlatformReadiness",
    "InvestigationCase",
    "RemediationTaskCreate",
    "RemediationTaskUpdate",
    "RemediationTask",
    "ClosureAttestationCreate",
    "ClosureAttestation",
    "AttestedClosure",
    "Principal",
    "IssuerCreate",
    "ReceiptIssuer",
    "TrustedKeyCreate",
    "TrustedKeyRotate",
    "TrustedReceiptKey",
    "WorkloadCredentialCreate",
    "WorkloadCredentialRotate",
    "WorkloadCredential",
    "WorkloadCredentialCreated",
]
