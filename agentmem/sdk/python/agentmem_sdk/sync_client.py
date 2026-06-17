"""
AgentMemClient — synchronous wrapper around AsyncAgentMemClient.

For scripts, CLIs, and any non-async context.  In async code (FastAPI
handlers, Jupyter with a running loop) use AsyncAgentMemClient directly.

Usage::

    from agentmem_sdk import AgentMemClient

    with AgentMemClient(base_url="http://localhost:8000", api_key="...") as client:
        client.add(agent_id="my-agent", content="NVDA guidance $36B",
                   event_time=datetime(2026, 5, 10, tzinfo=timezone.utc),
                   metadata={"ticker": "NVDA", "metric": "guidance"})

        result = client.recall(agent_id="my-agent", query="NVDA guidance")
        for mem in result["memories"]:
            print(mem["content"])
"""
from __future__ import annotations

import asyncio
from datetime import datetime
from typing import Any, Optional

from .client import AsyncAgentMemClient


class AgentMemClient:
    """Synchronous HTTP client for the AgentMem REST API."""

    def __init__(
        self,
        base_url: str = "http://localhost:8000",
        api_key: str = "",
        timeout: float = 30.0,
    ):
        self._async = AsyncAgentMemClient(base_url=base_url, api_key=api_key, timeout=timeout)
        self._loop = asyncio.new_event_loop()

    # ------------------------------------------------------------------
    # Context manager
    # ------------------------------------------------------------------

    def __enter__(self) -> "AgentMemClient":
        return self

    def __exit__(self, *_: Any) -> None:
        self.close()

    def close(self) -> None:
        self._loop.close()

    # ------------------------------------------------------------------
    # Public API (mirrors AsyncAgentMemClient)
    # ------------------------------------------------------------------

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

    def erase(self, subject_id: str, request_ref: str) -> dict:
        """GDPR erasure. Returns EraseResult as a dict."""
        return self._loop.run_until_complete(
            self._async.erase(subject_id=subject_id, request_ref=request_ref)
        )
