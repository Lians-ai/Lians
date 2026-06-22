"""
OpenAI Agents SDK integration for Lian.

Provides FunctionTool wrappers for the OpenAI Agents SDK (openai-agents >= 0.1).
Works with any Lian client (LocalLianClient for dev, LianClient for
production).

Install::

    pip install lian[openai-agents]
    # or: pip install openai-agents

Usage::

    from openai_agents import Agent, Runner
    from lian import LocalLianClient
    from lian.openai_agents_integration import build_openai_agent_tools

    client = LocalLianClient()
    tools  = build_openai_agent_tools(client, agent_id="equity-desk")

    agent = Agent(
        name="Equity Analyst",
        instructions="You are an equity research analyst with persistent memory.",
        tools=tools,
    )
    result = await Runner.run(agent, "What guidance did NVDA give last quarter?")

Three tools are returned:

- ``agentmem_remember``       — store a fact with its event timestamp and metadata
- ``agentmem_recall``         — retrieve current facts by semantic search
- ``agentmem_recall_at``      — retrieve facts valid at a specific past date (compliance)

The ``agentmem_recall_at`` tool is the compliance differentiator:
it answers "what did the model know before the trade?" with a verifiable,
tamper-evident hash chain — no other memory store in the OpenAI ecosystem provides this.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any


def build_openai_agent_tools(client: Any, agent_id: str) -> list:
    """
    Return three OpenAI Agents SDK FunctionTool instances wired to *client*.

    Parameters
    ----------
    client:
        Any synchronous Lian client — ``LocalLianClient`` or
        ``LianClient``.
    agent_id:
        The agent namespace to read/write memories under.

    Returns
    -------
    A list of three FunctionTool instances:
    ``[agentmem_remember, agentmem_recall, agentmem_recall_at]``.

    Raises
    ------
    ImportError
        If ``openai-agents`` is not installed.
        Install with: ``pip install lian[openai-agents]``
    """
    try:
        from agents import function_tool  # type: ignore[import]
    except ImportError:
        raise ImportError(
            "openai-agents is required for the OpenAI Agents SDK integration.\n"
            "Install with: pip install lian[openai-agents]\n"
            "or: pip install openai-agents"
        )

    def _fmt(memories: list[dict]) -> str:
        if not memories:
            return "No relevant memories found."
        return "\n".join(
            f"[{(m.get('event_time') or '')[:10]}] {m.get('content') or '[erased]'}"
            for m in memories
        )

    @function_tool
    def agentmem_remember(
        content: str,
        event_time_iso: str,
        metadata_json: str = "{}",
        importance: float = 0.5,
    ) -> str:
        """
        Store a fact or observation in Lian persistent memory.

        Always use event_time_iso for when the event *occurred*, not when you are
        recording it. Add ticker/metric metadata when storing financial facts to
        enable precision recall later. Example event_time_iso: '2026-05-10T00:00:00Z'
        for an earnings call on May 10.

        The memory is stored with AES-256-GCM encryption and written to a SHA-256
        tamper-evident audit chain (SEC 17a-4 compliant). If a newer value for the
        same entity+attribute already exists, this fact will be marked as superseded
        automatically — the agent always recalls the current truth.

        Parameters
        ----------
        content:
            The fact, observation, or decision to remember.
        event_time_iso:
            ISO 8601 timestamp for when the real-world event occurred.
        metadata_json:
            JSON string of metadata tags. Useful keys: ticker, metric, entity,
            source, sector. Example: '{"ticker": "NVDA", "metric": "guidance"}'
        importance:
            Salience score 0.0–1.0. Default 0.5. Use 0.8–1.0 for high-impact
            regulatory or market-moving facts.
        """
        dt = datetime.fromisoformat(event_time_iso.replace("Z", "+00:00"))
        meta = json.loads(metadata_json) if isinstance(metadata_json, str) else metadata_json
        client.add(
            agent_id=agent_id,
            content=content,
            event_time=dt,
            metadata=meta,
            importance=importance,
            source="openai_agents",
        )
        preview = content[:120] + ("…" if len(content) > 120 else "")
        return f"Stored: {preview}"

    @function_tool
    def agentmem_recall(query: str, k: int = 5) -> str:
        """
        Retrieve the most relevant *current* facts from Lian for a query.

        Superseded facts are excluded at the database layer — only the latest
        valid value is returned. Use before answering any question that depends
        on stored financial or factual data to avoid hallucinating stale figures.

        Parameters
        ----------
        query:
            Natural-language question describing what to retrieve.
            Example: "NVDA FY2026 revenue guidance"
        k:
            Maximum number of memories to return (1–20). Default 5.
        """
        result = client.recall(agent_id=agent_id, query=query, k=k)
        return _fmt(result.get("memories", []))

    @function_tool
    def agentmem_recall_at(query: str, as_of_iso: str, k: int = 5) -> str:
        """
        Retrieve facts that were valid at a specific point in time.

        Use for compliance and audit queries: "What guidance did we have on
        2026-03-01?" or "What was the consensus before the revision?"

        This is the compliance differentiator: it returns the exact fact set that
        was valid at the given timestamp, accounting for out-of-order ingestion.
        mem0 has no bitemporal model. Graphiti/Zep has temporal graph queries but
        no hash-chain audit stack — there is no verifiable proof that the result
        hasn't been altered.

        Parameters
        ----------
        query:
            Natural-language question describing what to retrieve.
        as_of_iso:
            ISO 8601 timestamp. Only memories valid at this point are returned.
            Example: '2026-03-01T00:00:00Z' for a March 1 audit reconstruction.
        k:
            Maximum number of memories to return (1–20). Default 5.
        """
        as_of = datetime.fromisoformat(as_of_iso.replace("Z", "+00:00"))
        result = client.recall(agent_id=agent_id, query=query, k=k, as_of=as_of)
        return f"Facts valid as of {as_of_iso[:10]}:\n{_fmt(result.get('memories', []))}"

    return [agentmem_remember, agentmem_recall, agentmem_recall_at]
