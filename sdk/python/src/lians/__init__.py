"""
lians - Python client for Lians, the financial-grade AI memory layer.

Install::

    pip install lians

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

            # Audit reconstruction - complete knowledge state at T
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
from .client import LiansClient, LiansError
from .webhooks import verify_webhook_signature, parse_webhook_payload
from .types import (
    MemoryOut,
    MemoryBatchResult,
    RecallResult,
    EraseResult,
    ErasureCertificate,
    MemoryLineageResult,
    FactHistoryResult,
    KnowledgeSnapshot,
    ContaminationFlag,
    ContaminationReport,
    ConflictFlagOut,
    ConflictListResult,
    ConflictResolveResult,
    SupersessionReviewResult,
    AuditExportResult,
    ComplianceReport,
    WebhookEndpoint,
    WebhookRegisterResult,
    WebhookDeliveryListResult,
)

__version__ = "0.1.0"
__all__ = [
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
    "MemoryLineageResult",
    "FactHistoryResult",
    "KnowledgeSnapshot",
    "ContaminationFlag",
    "ContaminationReport",
    "ConflictFlagOut",
    "ConflictListResult",
    "ConflictResolveResult",
    "SupersessionReviewResult",
    "AuditExportResult",
    "ComplianceReport",
    "WebhookEndpoint",
    "WebhookRegisterResult",
    "WebhookDeliveryListResult",
]
