"""
CrewAI integration for Lian.

Provides BaseTool subclasses compatible with crewai >= 0.28.

Install::

    pip install lian[crewai]

Usage::

    from lian import LocalLianClient
    from lian.crewai_integration import build_crewai_tools

    client = LocalLianClient()
    tools = build_crewai_tools(client, agent_id="research-agent")

    from crewai import Agent, Task, Crew
    analyst = Agent(
        role="Financial Analyst",
        goal="Analyse NVDA earnings and recall prior guidance",
        tools=tools,
        llm=my_llm,
    )

Three tools are returned:

- ``remember_fact``   — store a financial fact with its event timestamp
- ``recall_facts``    — retrieve current memories by semantic search
- ``recall_facts_at`` — retrieve memories valid at a specific past date (compliance)

The ``recall_facts_at`` tool queries memories as they were at a specific past date.
mem0 has no bitemporal model. Graphiti/Zep has temporal graph queries but no
compliance audit stack (hash chain, crypto-shred, information barriers), so
CrewAI agents backed by Lian get both point-in-time accuracy and SEC/FINRA-grade auditability.
"""
from __future__ import annotations

import json
from datetime import datetime
from typing import Any

try:
    from crewai.tools import BaseTool
    from pydantic import BaseModel, Field
    _CREWAI_AVAILABLE = True
except ImportError:  # pragma: no cover
    _CREWAI_AVAILABLE = False


def build_crewai_tools(client: Any, agent_id: str) -> list:
    """
    Return three CrewAI-compatible BaseTool instances wired to *client*.

    Parameters
    ----------
    client:
        Any synchronous Lian client — ``LocalLianClient`` or
        ``LianClient``.  (CrewAI runs tools synchronously.)
    agent_id:
        The agent namespace to read/write memories under.

    Returns
    -------
    A list of three ``BaseTool`` instances: ``[remember_fact, recall_facts,
    recall_facts_at]``.

    Raises
    ------
    ImportError
        If ``crewai`` is not installed.  Install with
        ``pip install lian[crewai]``.
    """
    if not _CREWAI_AVAILABLE:
        raise ImportError(
            "crewai is required for CrewAI integration.\n"
            "Install with: pip install lian[crewai]"
        )

    # ── Shared helpers ────────────────────────────────────────────────────────

    def _fmt(memories: list[dict]) -> str:
        if not memories:
            return "No relevant memories found."
        return "\n".join(
            f"[{(m.get('event_time') or '')[:10]}] {m.get('content') or '[erased]'}"
            for m in memories
        )

    # ── Tool definitions (inner classes close over client/agent_id) ───────────

    class _RememberInput(BaseModel):
        content: str = Field(
            ...,
            description="The financial fact, observation, or decision to store.",
        )
        event_time_iso: str = Field(
            ...,
            description=(
                "ISO 8601 timestamp for when the event occurred — NOT the current time. "
                "Example: '2026-05-10T00:00:00Z' for an earnings call on May 10."
            ),
        )
        metadata: str = Field(
            default="{}",
            description=(
                "JSON string of metadata tags for precision recall. "
                "Useful keys: ticker, metric, quarter, entity, source. "
                "Example: '{\"ticker\": \"NVDA\", \"metric\": \"guidance\"}'"
            ),
        )
        importance: float = Field(
            default=0.5,
            ge=0.0,
            le=1.0,
            description="Salience score from 0.0 (low) to 1.0 (high). Affects recall ranking.",
        )

    class RememberFact(BaseTool):
        name: str = "remember_fact"
        description: str = (
            "Store a financial fact, observation, or decision in Lian persistent memory. "
            "Always use the event_time_iso for when the event *occurred*, not when you are "
            "recording it.  Add ticker/metric metadata when relevant — it enables precision "
            "recall later without semantic search ambiguity."
        )
        args_schema: type[BaseModel] = _RememberInput

        def _run(
            self,
            content: str,
            event_time_iso: str,
            metadata: str = "{}",
            importance: float = 0.5,
        ) -> str:
            dt = datetime.fromisoformat(event_time_iso.replace("Z", "+00:00"))
            meta = json.loads(metadata) if isinstance(metadata, str) else metadata
            client.add(
                agent_id=agent_id,
                content=content,
                event_time=dt,
                metadata=meta,
                importance=importance,
                source="crewai_agent",
            )
            preview = content[:120] + ("…" if len(content) > 120 else "")
            return f"Stored: {preview}"

    class _RecallInput(BaseModel):
        query: str = Field(
            ...,
            description="Natural-language query describing what to retrieve.",
        )
        k: int = Field(
            default=5,
            ge=1,
            le=20,
            description="Number of memories to return (1–20).",
        )

    class RecallFacts(BaseTool):
        name: str = "recall_facts"
        description: str = (
            "Retrieve the most relevant *current* financial facts from Lian. "
            "Superseded facts are automatically excluded — only the latest valid value "
            "is returned.  Use this before answering any question that may rely on stored "
            "financial data to avoid hallucinating stale figures."
        )
        args_schema: type[BaseModel] = _RecallInput

        def _run(self, query: str, k: int = 5) -> str:
            result = client.recall(agent_id=agent_id, query=query, k=k)
            return _fmt(result.get("memories", []))

    class _RecallAtInput(BaseModel):
        query: str = Field(
            ...,
            description="Natural-language query describing what to retrieve.",
        )
        as_of_iso: str = Field(
            ...,
            description=(
                "ISO 8601 timestamp.  Only memories that were valid at this point in time "
                "are returned.  Use for compliance and audit queries such as "
                "'What guidance did we have on 2026-03-01?'"
            ),
        )
        k: int = Field(
            default=5,
            ge=1,
            le=20,
            description="Number of memories to return (1–20).",
        )

    class RecallFactsAt(BaseTool):
        name: str = "recall_facts_at"
        description: str = (
            "Retrieve financial facts that were valid at a specific point in time. "
            "Use for compliance and audit questions: 'What guidance did we have on "
            "2026-03-01?' or 'What was the consensus estimate before the revision?' "
            "Facts ingested after the as_of timestamp are excluded even if they "
            "supersede earlier values.  mem0 has no bitemporal model.  "
            "Graphiti/Zep has temporal graph queries but no compliance audit stack."
        )
        args_schema: type[BaseModel] = _RecallAtInput

        def _run(self, query: str, as_of_iso: str, k: int = 5) -> str:
            as_of = datetime.fromisoformat(as_of_iso.replace("Z", "+00:00"))
            result = client.recall(agent_id=agent_id, query=query, k=k, as_of=as_of)
            return f"Memories valid as of {as_of_iso[:10]}:\n{_fmt(result.get('memories', []))}"

    return [RememberFact(), RecallFacts(), RecallFactsAt()]
