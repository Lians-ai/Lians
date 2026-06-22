"""
AutoGen v0.4 integration for Lian.

Provides function tools for AutoGen agents (autogen-agentchat >= 0.4 /
autogen-core >= 0.4).  Works with both the ConversableAgent and the newer
AssistantAgent + function_tool pattern.

Install::

    pip install lian[autogen]
    # or: pip install autogen-agentchat autogen-core

Usage — AssistantAgent with function tools (AutoGen v0.4 async)::

    from autogen_agentchat.agents import AssistantAgent
    from autogen_ext.models import OpenAIChatCompletionClient
    from lian import AsyncLianClient
    from lian.autogen_integration import build_autogen_tools

    client = AsyncLianClient(base_url="https://mem.firm.internal", api_key="...")
    tools  = build_autogen_tools(client, agent_id="equity-desk")

    model_client = OpenAIChatCompletionClient(model="gpt-4o")
    agent = AssistantAgent(
        name="EquityAnalyst",
        model_client=model_client,
        tools=tools,
        system_message="You are an equity analyst with persistent compliance-grade memory.",
    )

Usage — ConversableAgent (AutoGen v0.2 / classic)::

    from autogen import ConversableAgent
    from lian import LocalLianClient
    from lian.autogen_integration import build_autogen_functions

    client = LocalLianClient()
    functions, function_map = build_autogen_functions(client, agent_id="analyst")

    agent = ConversableAgent(
        name="analyst",
        functions=functions,
        function_map=function_map,
    )

Three tools are returned by build_autogen_tools():

- ``agentmem_remember``    — store a fact with event timestamp and metadata
- ``agentmem_recall``      — retrieve current facts by semantic search
- ``agentmem_recall_at``   — retrieve facts valid at a specific past date (compliance)

The ``agentmem_recall_at`` tool supports AutoGen multi-agent compliance workflows:
"What did the risk-assessment agent know before the trade was placed?" — with a
tamper-evident SHA-256 audit chain, not a probabilistic reconstruction.
"""
from __future__ import annotations

import asyncio
import json
from datetime import datetime
from typing import Any


def build_autogen_tools(client: Any, agent_id: str) -> list:
    """
    Return three AutoGen v0.4 FunctionTool instances for AssistantAgent.

    Parameters
    ----------
    client:
        Any Lian client — ``LocalLianClient``, ``LianClient``, or
        ``AsyncLianClient``.  Async clients are detected automatically.
    agent_id:
        The agent namespace to read/write memories under.

    Returns
    -------
    List of three AutoGen ``FunctionTool`` instances.

    Raises
    ------
    ImportError
        If ``autogen-core`` is not installed.
        Install with: ``pip install lian[autogen]``
    """
    try:
        from autogen_core.tools import FunctionTool  # type: ignore[import]
    except ImportError:
        raise ImportError(
            "autogen-core is required for AutoGen integration.\n"
            "Install with: pip install lian[autogen]\n"
            "or: pip install autogen-core"
        )

    _is_async = asyncio.iscoroutinefunction(getattr(client, "recall", None))

    def _fmt(memories: list[dict]) -> str:
        if not memories:
            return "No relevant memories found."
        return "\n".join(
            f"[{(m.get('event_time') or '')[:10]}] {m.get('content') or '[erased]'}"
            for m in memories
        )

    def _run_maybe_async(coro: Any) -> Any:
        if _is_async:
            return asyncio.get_event_loop().run_until_complete(coro)
        return coro  # already a plain value from sync client

    # ── Tool functions ────────────────────────────────────────────────────────

    async def agentmem_remember(
        content: str,
        event_time_iso: str,
        metadata_json: str = "{}",
        importance: float = 0.5,
    ) -> str:
        """
        Store a fact in Lian persistent memory.

        Use event_time_iso for when the event occurred (not now). Add structured
        metadata for precision recall: ticker, metric, entity, source, jurisdiction.

        Facts are AES-256-GCM encrypted at rest, written to a SHA-256 audit chain,
        and automatically superseded when a newer value for the same entity+attribute
        arrives — so recall always returns the current truth.

        :param content: The fact or observation to store.
        :param event_time_iso: ISO 8601 timestamp of when the event occurred.
            Example: '2026-05-10T00:00:00Z'
        :param metadata_json: JSON metadata tags.
            Example: '{"ticker": "NVDA", "metric": "guidance"}'
        :param importance: Salience 0.0–1.0. Default 0.5.
        """
        dt = datetime.fromisoformat(event_time_iso.replace("Z", "+00:00"))
        meta = json.loads(metadata_json) if isinstance(metadata_json, str) else {}
        kwargs: dict[str, Any] = dict(
            agent_id=agent_id,
            content=content,
            event_time=dt,
            metadata=meta,
            importance=importance,
            source="autogen",
        )
        if _is_async:
            await client.add(**kwargs)
        else:
            client.add(**kwargs)
        return f"Stored: {content[:120]}{'…' if len(content) > 120 else ''}"

    async def agentmem_recall(query: str, k: int = 5) -> str:
        """
        Retrieve current relevant facts from Lian.

        Superseded facts are excluded at the DB layer. Only the most recent
        valid value for each fact is returned — your agent never sees stale context.

        :param query: Natural-language query. Example: 'NVDA guidance FY2026'
        :param k: Number of memories to return (1–20). Default 5.
        """
        if _is_async:
            result = await client.recall(agent_id=agent_id, query=query, k=k)
        else:
            result = client.recall(agent_id=agent_id, query=query, k=k)
        return _fmt(result.get("memories", []))

    async def agentmem_recall_at(query: str, as_of_iso: str, k: int = 5) -> str:
        """
        Retrieve facts valid at a specific point in time (compliance/audit path).

        Returns the exact knowledge state at the given timestamp — supports
        out-of-order ingestion correctly. Use in multi-agent workflows where one
        agent must reconstruct what another agent knew before a decision.

        :param query: Natural-language query.
        :param as_of_iso: ISO 8601 timestamp. Only facts valid at this moment
            are returned. Example: '2026-03-01T00:00:00Z'
        :param k: Number of memories to return (1–20). Default 5.
        """
        as_of = datetime.fromisoformat(as_of_iso.replace("Z", "+00:00"))
        if _is_async:
            result = await client.recall(agent_id=agent_id, query=query, k=k, as_of=as_of)
        else:
            result = client.recall(agent_id=agent_id, query=query, k=k, as_of=as_of)
        return f"Facts valid as of {as_of_iso[:10]}:\n{_fmt(result.get('memories', []))}"

    return [
        FunctionTool(agentmem_remember, description=agentmem_remember.__doc__ or ""),
        FunctionTool(agentmem_recall,   description=agentmem_recall.__doc__ or ""),
        FunctionTool(agentmem_recall_at, description=agentmem_recall_at.__doc__ or ""),
    ]


def build_autogen_functions(
    client: Any,
    agent_id: str,
) -> tuple[list[dict], dict[str, Any]]:
    """
    Return (functions_schema, function_map) for AutoGen v0.2 ConversableAgent.

    The functions_schema list goes into the ``functions`` parameter; the
    function_map dict goes into ``function_map``.

    Parameters
    ----------
    client:
        Any synchronous Lian client (LocalLianClient or LianClient).
    agent_id:
        The agent namespace to read/write memories under.

    Returns
    -------
    A (list, dict) tuple compatible with ConversableAgent's ``functions`` and
    ``function_map`` parameters.
    """
    def _fmt(memories: list[dict]) -> str:
        if not memories:
            return "No relevant memories found."
        return "\n".join(
            f"[{(m.get('event_time') or '')[:10]}] {m.get('content') or '[erased]'}"
            for m in memories
        )

    def _remember(content: str, event_time_iso: str, metadata_json: str = "{}", importance: float = 0.5) -> str:
        dt = datetime.fromisoformat(event_time_iso.replace("Z", "+00:00"))
        meta = json.loads(metadata_json) if isinstance(metadata_json, str) else {}
        client.add(agent_id=agent_id, content=content, event_time=dt, metadata=meta, importance=importance, source="autogen_v2")
        return f"Stored: {content[:120]}"

    def _recall(query: str, k: int = 5) -> str:
        result = client.recall(agent_id=agent_id, query=query, k=k)
        return _fmt(result.get("memories", []))

    def _recall_at(query: str, as_of_iso: str, k: int = 5) -> str:
        as_of = datetime.fromisoformat(as_of_iso.replace("Z", "+00:00"))
        result = client.recall(agent_id=agent_id, query=query, k=k, as_of=as_of)
        return f"Facts valid as of {as_of_iso[:10]}:\n{_fmt(result.get('memories', []))}"

    functions = [
        {
            "name": "agentmem_remember",
            "description": "Store a fact with its event timestamp in Lian persistent memory.",
            "parameters": {
                "type": "object",
                "properties": {
                    "content":        {"type": "string", "description": "The fact to remember."},
                    "event_time_iso": {"type": "string", "description": "ISO 8601 event timestamp."},
                    "metadata_json":  {"type": "string", "description": "JSON metadata tags.", "default": "{}"},
                    "importance":     {"type": "number", "description": "Salience 0–1.", "default": 0.5},
                },
                "required": ["content", "event_time_iso"],
            },
        },
        {
            "name": "agentmem_recall",
            "description": "Retrieve current relevant facts from Lian (superseded facts excluded).",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string"},
                    "k":     {"type": "integer", "default": 5},
                },
                "required": ["query"],
            },
        },
        {
            "name": "agentmem_recall_at",
            "description": "Retrieve facts valid at a specific timestamp (compliance / audit path).",
            "parameters": {
                "type": "object",
                "properties": {
                    "query":       {"type": "string"},
                    "as_of_iso":   {"type": "string", "description": "ISO 8601 timestamp."},
                    "k":           {"type": "integer", "default": 5},
                },
                "required": ["query", "as_of_iso"],
            },
        },
    ]

    function_map = {
        "agentmem_remember": _remember,
        "agentmem_recall":   _recall,
        "agentmem_recall_at": _recall_at,
    }

    return functions, function_map
