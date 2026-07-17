"""
OpenAI Agents SDK integration for Lians.

Provides FunctionTool wrappers for the OpenAI Agents SDK (openai-agents >= 0.1).
Works with any Lians client (LocalLiansClient for dev, LiansClient for
production).

Install::

    pip install lians[openai-agents]
    # or: pip install openai-agents

Usage::

    from agents import Agent, Runner
    from lians import LocalLiansClient
    from lians.openai_agents_integration import build_openai_agent_tools

    client = LocalLiansClient()
    tools  = build_openai_agent_tools(client, agent_id="equity-desk")

    agent = Agent(
        name="Equity Analyst",
        instructions="You are an equity research analyst with persistent memory.",
        tools=tools,
    )
    result = await Runner.run(agent, "What guidance did NVDA give last quarter?")

Four tools are returned:

- ``agentmem_remember``: store a fact with its event timestamp and metadata
- ``agentmem_recall``: retrieve current facts by semantic search
- ``agentmem_recall_at``: retrieve facts valid at a specific past date (compliance)
- ``agentmem_flush``: batch-persist durable facts before context compaction

The ``agentmem_recall_at`` tool answers "what did the model know before the
trade?" from Lians' bitemporal record and tamper-evident audit chain.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any


def build_openai_agent_tools(client: Any, agent_id: str) -> list:
    """
    Return four OpenAI Agents SDK FunctionTool instances wired to *client*.

    Parameters
    ----------
    client:
        Any synchronous Lians client, such as ``LocalLiansClient`` or
        ``LiansClient``.
    agent_id:
        The agent namespace to read/write memories under.

    Returns
    -------
    A list of four FunctionTool instances:
    ``[agentmem_remember, agentmem_recall, agentmem_recall_at, agentmem_flush]``.

    Raises
    ------
    ImportError
        If ``openai-agents`` is not installed.
        Install with: ``pip install lians[openai-agents]``
    """
    try:
        from agents import function_tool  # type: ignore[import]
    except ImportError:
        raise ImportError(
            "openai-agents is required for the OpenAI Agents SDK integration.\n"
            "Install with: pip install lians[openai-agents]\n"
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
        Store a fact or observation in Lians persistent memory.

        Always use event_time_iso for when the event *occurred*, not when you are
        recording it. Add ticker/metric metadata when storing financial facts to
        enable precision recall later. Example event_time_iso: '2026-05-10T00:00:00Z'
        for an earnings call on May 10.

        The memory is stored with AES-256-GCM encryption and written to a SHA-256
        tamper-evident audit chain (SEC 17a-4 compliant). If a newer value for the
        same entity+attribute already exists, this fact will be marked as superseded
        automatically, so default recall returns the current truth.

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
        Retrieve the most relevant *current* facts from Lians for a query.

        Superseded facts are excluded at the database layer, so only the latest
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

        This returns the fact set that was valid at the given timestamp under
        Lians' recorded bitemporal history, including out-of-order ingestion.

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

    @function_tool
    def agentmem_flush(facts_json: str) -> str:
        """
        Persist a batch of durable facts NOW, before context compaction.

        Call this when the conversation is about to be summarized or truncated
        (or when instructed that the context window is nearly full). Anything
        not written here may be lost when older turns are compacted away.
        Review the conversation and extract the facts worth keeping: decisions
        made, constraints learned, client instructions, corrections, and
        commitments, not chit-chat.

        Each write is tagged as a pre-compaction flush in the tamper-evident
        audit chain, so it is provable when the agent externalized what it knew.

        Parameters
        ----------
        facts_json:
            JSON array of fact strings.
            Example: '["Client approved the Q3 rebalance on 2026-07-01",
            "Compliance: no tobacco exposure in any account"]'
        """
        facts = json.loads(facts_json) if isinstance(facts_json, str) else facts_json
        now = datetime.now(timezone.utc)
        written = 0
        for fact in facts or []:
            text = str(fact).strip()
            if not text:
                continue
            client.add(
                agent_id=agent_id,
                content=text,
                event_time=now,
                metadata={"_flush": "pre_compaction"},
                source="openai_agents_flush",
            )
            written += 1
        return f"Flushed {written} durable fact(s) to memory before compaction."

    return [agentmem_remember, agentmem_recall, agentmem_recall_at, agentmem_flush]
