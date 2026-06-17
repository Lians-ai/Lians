"""
AgentMem Python SDK — thin HTTP wrapper around the REST API.
Supports both hosted (base_url) and local mode (direct service import).
"""
from __future__ import annotations
from datetime import datetime
from typing import Any, Optional
import httpx


class AsyncAgentMemClient:
    def __init__(
        self,
        base_url: str = "http://localhost:8000",
        api_key: str = "",
        timeout: float = 30.0,
    ):
        self._base = base_url.rstrip("/")
        self._headers = {"X-API-Key": api_key, "Content-Type": "application/json"}
        self._timeout = timeout

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
        async with httpx.AsyncClient(timeout=self._timeout) as client:
            resp = await client.post(
                f"{self._base}/v1/memories",
                headers=self._headers,
                json={
                    "agent_id": agent_id,
                    "content": content,
                    "event_time": event_time.isoformat(),
                    "source": source,
                    "subject_id": subject_id,
                    "metadata": metadata or {},
                    "importance": importance,
                },
            )
            resp.raise_for_status()
            return resp.json()

    async def recall(
        self,
        agent_id: str,
        query: str,
        k: int = 5,
        as_of: Optional[datetime] = None,
        filters: Optional[dict[str, Any]] = None,
    ) -> dict:
        async with httpx.AsyncClient(timeout=self._timeout) as client:
            resp = await client.post(
                f"{self._base}/v1/recall",
                headers=self._headers,
                json={
                    "agent_id": agent_id,
                    "query": query,
                    "k": k,
                    "as_of": as_of.isoformat() if as_of else None,
                    "filters": filters or {},
                },
            )
            resp.raise_for_status()
            return resp.json()

    async def reconstruct(
        self,
        agent_id: str,
        as_of: datetime,
        query: Optional[str] = None,
    ) -> dict:
        async with httpx.AsyncClient(timeout=self._timeout) as client:
            params = {"agent_id": agent_id, "as_of": as_of.isoformat()}
            if query:
                params["query"] = query
            resp = await client.get(
                f"{self._base}/v1/audit/reconstruct",
                headers=self._headers,
                params=params,
            )
            resp.raise_for_status()
            return resp.json()

    async def erase(self, subject_id: str, request_ref: str) -> dict:
        async with httpx.AsyncClient(timeout=self._timeout) as client:
            resp = await client.post(
                f"{self._base}/v1/erase",
                headers=self._headers,
                json={"subject_id": subject_id, "request_ref": request_ref},
            )
            resp.raise_for_status()
            return resp.json()
