"""
LangGraph integration for AgentMem.

Provides node factory functions for LangGraph state machines.  Each factory
returns an async callable with the signature ``async (state: dict) -> dict``
that LangGraph expects for graph nodes.

Install::

    pip install agentmem-sdk[langgraph]

Usage — inject memories into a ReAct agent graph::

    from langgraph.graph import StateGraph, END
    from agentmem_sdk import LocalAgentMemClient
    from agentmem_sdk.langgraph_integration import create_recall_node, create_remember_node

    client = LocalAgentMemClient()

    recall_node  = create_recall_node(client,  agent_id="analyst")
    remember_node = create_remember_node(client, agent_id="analyst")

    graph = StateGraph(dict)
    graph.add_node("recall",   recall_node)
    graph.add_node("remember", remember_node)
    graph.add_node("llm",      my_llm_node)
    graph.set_entry_point("recall")
    graph.add_edge("recall",  "llm")
    graph.add_edge("llm",     "remember")
    graph.add_edge("remember", END)
    app = graph.compile()

    result = await app.ainvoke({"query": "NVDA guidance Q3 2026"})
    # result["memories"] — list of MemoryOut dicts injected before the LLM

Usage — point-in-time compliance node::

    recall_audit_node = create_recall_node(
        client,
        agent_id="compliance-agent",
        as_of_key="audit_date",   # reads state["audit_date"] for as_of filter
    )
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from typing import Any, Optional


def create_recall_node(
    client: Any,
    agent_id: str,
    *,
    query_key: str = "query",
    memories_key: str = "memories",
    k: int = 5,
    as_of_key: Optional[str] = None,
    filters_key: Optional[str] = None,
):
    """
    Create a LangGraph node that recalls relevant memories into state.

    The node reads ``state[query_key]`` and writes ``state[memories_key]`` with
    a list of MemoryOut dicts.

    Parameters
    ----------
    client:
        Any AgentMem client (``LocalAgentMemClient``, ``AgentMemClient``, or
        ``AsyncAgentMemClient``).  Both sync and async clients are supported.
    agent_id:
        The agent namespace to recall from.
    query_key:
        State key holding the recall query string.  Defaults to ``"query"``.
    memories_key:
        State key to write the recalled memories into.  Defaults to ``"memories"``.
    k:
        Maximum number of memories to return.
    as_of_key:
        Optional state key holding a ``datetime`` or ISO string for point-in-time
        recall.  When set, only memories valid at that timestamp are returned —
        the compliance / audit path.
    filters_key:
        Optional state key holding a metadata filter dict (e.g.
        ``{"ticker": "NVDA"}``).

    Returns
    -------
    An async callable ``(state: dict) -> dict`` suitable for
    ``StateGraph.add_node()``.
    """
    _recall_is_async = asyncio.iscoroutinefunction(getattr(client, "recall", None))

    async def _recall_node(state: dict) -> dict:
        query = state.get(query_key) or ""
        if not query:
            return {memories_key: []}

        as_of: Optional[datetime] = None
        if as_of_key:
            raw = state.get(as_of_key)
            if isinstance(raw, str):
                as_of = datetime.fromisoformat(raw.replace("Z", "+00:00"))
            elif isinstance(raw, datetime):
                as_of = raw

        filters: dict = {}
        if filters_key:
            filters = state.get(filters_key) or {}

        kwargs = dict(agent_id=agent_id, query=query, k=k, as_of=as_of, filters=filters)
        if _recall_is_async:
            result = await client.recall(**kwargs)
        else:
            result = client.recall(**kwargs)

        return {memories_key: result.get("memories", [])}

    return _recall_node


def create_remember_node(
    client: Any,
    agent_id: str,
    *,
    content_key: str = "memory_content",
    event_time_key: str = "memory_event_time",
    metadata_key: str = "memory_metadata",
    subject_id_key: Optional[str] = None,
    importance_key: Optional[str] = None,
    result_key: str = "memory_stored",
):
    """
    Create a LangGraph node that stores a fact from state into AgentMem.

    The node reads ``state[content_key]`` and ``state[event_time_key]``.
    It writes ``state[result_key]`` with the created MemoryOut dict (or ``None``
    if ``content_key`` was absent from state).

    Parameters
    ----------
    client:
        Any AgentMem client.
    agent_id:
        The agent namespace to store into.
    content_key:
        State key holding the content string.  Defaults to ``"memory_content"``.
    event_time_key:
        State key holding a ``datetime`` or ISO string for the event timestamp.
        Defaults to ``now()`` when absent.
    metadata_key:
        State key holding a metadata dict.  Defaults to ``"memory_metadata"``.
    subject_id_key:
        Optional state key for the data-subject ID (for GDPR erasure targeting).
    importance_key:
        Optional state key holding a float 0–1 salience score.
    result_key:
        State key to write the created MemoryOut dict into.

    Returns
    -------
    An async callable ``(state: dict) -> dict`` suitable for
    ``StateGraph.add_node()``.
    """
    _add_is_async = asyncio.iscoroutinefunction(getattr(client, "add", None))

    async def _remember_node(state: dict) -> dict:
        content = state.get(content_key)
        if not content:
            return {result_key: None}

        raw_time = state.get(event_time_key)
        if isinstance(raw_time, str):
            event_time = datetime.fromisoformat(raw_time.replace("Z", "+00:00"))
        elif isinstance(raw_time, datetime):
            event_time = raw_time
        else:
            event_time = datetime.now(timezone.utc)

        metadata = state.get(metadata_key) or {}
        subject_id = state.get(subject_id_key) if subject_id_key else None
        importance = float(state.get(importance_key, 0.5)) if importance_key else 0.5

        kwargs: dict[str, Any] = dict(
            agent_id=agent_id,
            content=content,
            event_time=event_time,
            metadata=metadata,
            importance=importance,
        )
        if subject_id:
            kwargs["subject_id"] = subject_id

        if _add_is_async:
            result = await client.add(**kwargs)
        else:
            result = client.add(**kwargs)

        return {result_key: result}

    return _remember_node


def create_batch_remember_node(
    client: Any,
    agent_id: str,
    *,
    memories_in_key: str = "memories_to_store",
    result_key: str = "batch_stored",
):
    """
    Create a LangGraph node that batch-stores a list of facts from state.

    Reads ``state[memories_in_key]`` — a list of dicts, each matching the
    ``MemoryAdd`` schema (``content``, ``event_time``, ``metadata``, etc.) —
    and writes ``state[result_key]`` with the MemoryBatchResult dict.

    Useful for ingestion pipelines where an upstream node extracts multiple
    structured facts from a document (earnings call, 10-K filing, etc.).
    """
    _batch_is_async = asyncio.iscoroutinefunction(getattr(client, "batch_add", None))

    async def _batch_node(state: dict) -> dict:
        items = state.get(memories_in_key) or []
        if not items:
            return {result_key: {"added": 0, "memories": []}}

        enriched = []
        for item in items:
            row = dict(item)
            row.setdefault("agent_id", agent_id)
            if isinstance(row.get("event_time"), datetime):
                pass  # leave as-is; clients handle datetime → ISO internally
            enriched.append(row)

        if _batch_is_async:
            result = await client.batch_add(enriched)
        else:
            result = client.batch_add(enriched)

        return {result_key: result}

    return _batch_node


# ── Typed state helper ────────────────────────────────────────────────────────

def format_memories_for_prompt(memories: list[dict]) -> str:
    """
    Render a list of MemoryOut dicts as a plain-text block for LLM prompts.

    Suitable for injecting into a system message or human turn alongside the
    user's question.  Returns ``"No relevant memories."`` when the list is empty.
    """
    if not memories:
        return "No relevant memories."
    lines = []
    for m in memories:
        ts = (m.get("event_time") or "")[:10]
        content = m.get("content") or "[erased]"
        lines.append(f"[{ts}] {content}")
    return "\n".join(lines)
