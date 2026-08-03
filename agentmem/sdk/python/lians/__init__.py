"""
Lians Python SDK — financial-grade AI memory with compliance built in.

Three client modes for core memory workflows:

    LiansClient        — synchronous HTTP client (scripts, CLIs)
    AsyncLiansClient   — async HTTP client (FastAPI, async frameworks)
    LocalLiansClient   — zero-setup local SQLite mode (prototyping, CI)

Core convenience methods shared by all three clients::

    client.add(agent_id, content, event_time, metadata=...)
    client.add_from_messages(agent_id, messages=[{"role": "assistant", "content": "..."}])
    client.recall(agent_id, query, k=5)
    client.recall_at(agent_id, query, as_of=datetime(...))   # point-in-time / compliance
    client.snapshot(agent_id, as_of=datetime(...))           # full knowledge state at T
    client.backtest_check(agent_id, simulation_as_of=...)    # lookahead-bias detection
    client.erase(subject_id, request_ref)                    # GDPR crypto-shred

Framework integrations (optional extras)::

    # LangChain (chat history + StructuredTools)
    from lians.langchain_integration import LiansChatHistory, build_tools

    # LangGraph (node factory functions)
    from lians.langgraph_integration import create_recall_node, create_remember_node

    # CrewAI (BaseTool wrappers)
    from lians.crewai_integration import build_crewai_tools

    # OpenAI Agents SDK (FunctionTool wrappers)
    from lians.openai_agents_integration import build_openai_agent_tools

    # AutoGen v0.4 (FunctionTool) / v0.2 (ConversableAgent)
    from lians.autogen_integration import build_autogen_tools, build_autogen_functions

Install with extras::

    pip install lians-sdk[langchain]       # LangChain chat history + tools
    pip install lians-sdk[langgraph]       # LangGraph node factories
    pip install lians-sdk[crewai]          # CrewAI BaseTool wrappers
    pip install lians-sdk[openai-agents]   # OpenAI Agents SDK FunctionTools
    pip install lians-sdk[autogen]         # AutoGen v0.4 FunctionTools
    pip install lians-sdk[anthropic]       # Anthropic Recorder middleware
    pip install lians-sdk[google-adk]      # Google ADK Recorder plugin
    pip install lians-sdk[local]           # LocalLiansClient (SQLite)
    pip install lians-sdk[all]             # Everything
"""
from .anthropic_recorder import (
    anthropic_managed_agents_webhook_event,
    build_anthropic_recorder_middleware,
)
from .client import SDK_VERSION as __version__
from .client import AsyncLiansClient
from .crewai_recorder import (
    CrewRunId,
    CrewSourceFilter,
    build_crewai_recorder_listener,
)
from .google_adk_recorder import build_google_adk_recorder_plugin
from .harness import (
    CompactionGuard,
    LiansMemoryHarness,
    MemoryClient,
    RecalledMemory,
    TurnResult,
)
from .langchain_recorder import build_langchain_recorder_handler
from .openai_agents_recorder import (
    build_openai_agents_recorder_processor,
    install_openai_agents_recorder,
)
from .platform_types import (
    AttestedClosure,
    ClosureAttestation,
    ClosureAttestationCreate,
    CompatibilityListPage,
    DecisionDependency,
    DecisionDependencyChange,
    DecisionDependencyChangeType,
    DecisionDependencyKind,
    DecisionImpactAnalysisMode,
    DecisionImpactItem,
    DecisionImpactResult,
    DecisionInvestigationReport,
    DecisionOut,
    EvidenceArtifactOut,
    ExhaustiveImpactAssessmentAdvance,
    ExhaustiveImpactAssessmentCreate,
    ExhaustiveImpactAssessmentMatch,
    ExhaustiveImpactAssessmentResults,
    ExhaustiveImpactAssessmentState,
    ExhaustiveImpactAssessmentStatus,
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
    LiansDiscovery,
    LedgerEventOut,
    PlatformCapabilities,
    PlatformReadiness,
    Principal,
    ReceiptIssuer,
    RecorderEvidenceIndexJob,
    RecorderEnvelope,
    RemediationTask,
    RemediationTaskCreate,
    RemediationTaskUpdate,
    SupersessionActionResult,
    MeteringEvent,
    MeteringInventory,
    ScimTenantReconciliation,
    MeteringReplayRequest,
    MeteringStatus,
    TrustedKeyCreate,
    TrustedKeyRotate,
    TrustedReceiptKey,
    WorkloadCredential,
    WorkloadCredentialCreate,
    WorkloadCredentialCreated,
    WorkloadCredentialRotate,
)
from .recorder import a2a_event, lians_event, mcp_jsonrpc_event, otlp_genai_span
from .recorder_sink import (
    AsyncRecorderClient,
    AsyncRecorderSink,
    BackpressurePolicy,
    DeliveryFailurePolicy,
    RecorderAttribution,
    RecorderBufferFull,
    RecorderCaptureGap,
    RecorderDeliveryError,
    RecorderEnvelopeValidationError,
    RecorderIdentityError,
    RecorderSinkClosed,
    RecorderSinkConfig,
    RecorderSinkError,
    RecorderSinkStats,
    RecorderSubmission,
    recorder_content_hash,
    stabilize_recorder_envelope,
    validate_recorder_envelope,
)
from .sync_client import LiansClient

# Backward-compatibility aliases
AgentMemClient = LiansClient
AsyncAgentMemClient = AsyncLiansClient


def __getattr__(name: str):
    # LocalLiansClient needs the optional [local] extra (sqlalchemy/aiosqlite).
    # Import it lazily so a plain `pip install lians-sdk` — whose only core
    # dependency is httpx — can `import lians` without crashing.
    if name in ("LocalLiansClient", "LocalAgentMemClient"):
        from .local_client import LocalLiansClient
        return LocalLiansClient
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")

__all__ = [  # noqa: RUF022 -- grouped by public capability, not alphabetically
    "__version__",
    "LiansClient",
    "AsyncLiansClient",
    "LocalLiansClient",
    # Agent harness
    "LiansMemoryHarness",
    "CompactionGuard",
    "RecalledMemory",
    "TurnResult",
    "MemoryClient",
    # Universal Recorder builders and typed control inputs
    "lians_event",
    "otlp_genai_span",
    "mcp_jsonrpc_event",
    "a2a_event",
    "RecorderEnvelope",
    "RecorderEvidenceIndexJob",
    "AsyncRecorderClient",
    "AsyncRecorderSink",
    "BackpressurePolicy",
    "DeliveryFailurePolicy",
    "RecorderAttribution",
    "RecorderSinkConfig",
    "RecorderSinkStats",
    "RecorderSubmission",
    "RecorderCaptureGap",
    "RecorderSinkError",
    "RecorderSinkClosed",
    "RecorderBufferFull",
    "RecorderDeliveryError",
    "RecorderEnvelopeValidationError",
    "RecorderIdentityError",
    "recorder_content_hash",
    "stabilize_recorder_envelope",
    "validate_recorder_envelope",
    "build_langchain_recorder_handler",
    "build_crewai_recorder_listener",
    "build_openai_agents_recorder_processor",
    "install_openai_agents_recorder",
    "build_anthropic_recorder_middleware",
    "anthropic_managed_agents_webhook_event",
    "build_google_adk_recorder_plugin",
    "CrewRunId",
    "CrewSourceFilter",
    # Decision impact analysis
    "DecisionDependencyKind",
    "DecisionDependencyChange",
    "DecisionDependencyChangeType",
    "DecisionImpactAnalysisMode",
    "DecisionOut",
    "CompatibilityListPage",
    "LedgerEventOut",
    "EvidenceArtifactOut",
    "DecisionDependency",
    "DecisionImpactItem",
    "DecisionImpactResult",
    "ExhaustiveImpactAssessmentCreate",
    "ExhaustiveImpactAssessmentAdvance",
    "ExhaustiveImpactAssessmentState",
    "ExhaustiveImpactAssessmentStatus",
    "ExhaustiveImpactAssessmentMatch",
    "ExhaustiveImpactAssessmentResults",
    "GatePolicySetCreate",
    "GatePolicySet",
    "GateEvaluationRequest",
    "GateEvaluationResult",
    "GateExecutionPermitIssued",
    "GateExecutionPermitConsume",
    "GateExecutionPermitConsumption",
    "GateApprovalAttestationCreate",
    "GateApprovalAttestationSupersede",
    "GateApprovalAttestation",
    "GateDecision",
    "InvestigationCaseCreate",
    "InvestigationCaseUpdate",
    "InvestigationCase",
    "InvestigatorQueue",
    "InvestigatorCollectionWindow",
    "InvestigatorReportCoverage",
    "DecisionInvestigationReport",
    "LiansDiscovery",
    "PlatformCapabilities",
    "PlatformReadiness",
    "RemediationTaskCreate",
    "RemediationTaskUpdate",
    "RemediationTask",
    "ClosureAttestationCreate",
    "ClosureAttestation",
    "AttestedClosure",
    "SupersessionActionResult",
    "IssuerCreate",
    "ReceiptIssuer",
    "TrustedKeyCreate",
    "TrustedKeyRotate",
    "TrustedReceiptKey",
    "Principal",
    "WorkloadCredentialCreate",
    "WorkloadCredentialRotate",
    "WorkloadCredential",
    "WorkloadCredentialCreated",
    "MeteringStatus",
    "MeteringInventory",
    "ScimTenantReconciliation",
    "MeteringEvent",
    "MeteringReplayRequest",
    # aliases
    "AgentMemClient",
    "AsyncAgentMemClient",
    "LocalAgentMemClient",
]
