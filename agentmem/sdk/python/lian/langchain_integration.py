"""
LangChain integration for Lian.

Two integration patterns:

1. LianChatHistory — BaseChatMessageHistory for RunnableWithMessageHistory.
   Stores conversation turns as Lian memories; supports per-session isolation.

2. LianTools / build_tools — three StructuredTools for ReAct agents and
   LangGraph nodes: remember, recall, recall_at.  The recall_at tool is the
   differentiating one — it answers "what did we know on date X?" which plain
   vector stores cannot.

Both work with any Lian client (LocalLianClient for dev, LianClient
for production) and require no changes to swap between them.

Install langchain support::

    pip install langchain-core          # already installed if you're here
    pip install langchain langchain-openai   # or your preferred LLM provider

Usage — chat history::

    from lian import LocalLianClient
    from lian.langchain_integration import LianChatHistory
    from langchain_core.runnables.history import RunnableWithMessageHistory

    client = LocalLianClient()

    chain_with_history = RunnableWithMessageHistory(
        your_chain,
        lambda session_id: LianChatHistory(client=client, session_id=session_id),
        input_messages_key="input",
        history_messages_key="history",
    )

Usage — agent tools::

    from lian import LocalLianClient
    from lian.langchain_integration import build_tools

    client = LocalLianClient()
    tools = build_tools(client, agent_id="research-agent")

    # Pass tools to any LangChain agent / LangGraph node
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any, Optional, Sequence

from langchain_core.chat_history import BaseChatMessageHistory
from langchain_core.messages import BaseMessage, message_to_dict, messages_from_dict
from langchain_core.tools import BaseTool, StructuredTool
from pydantic import BaseModel, Field
from datetime import timedelta


# ---------------------------------------------------------------------------
# LianChatHistory
# ---------------------------------------------------------------------------

class LianChatHistory(BaseChatMessageHistory):
    """
    LangChain chat message history backed by Lian.

    Each conversation turn is stored as a Lian memory with:
      - agent_id = agent_id (default "chat")
      - metadata = {"session_id": session_id, "msg_type": "human" | "ai"}

    On retrieval the messages are returned in insertion (event_time) order.

    Example::

        history = LianChatHistory(client=LocalLianClient(), session_id="user-123")
        chain = RunnableWithMessageHistory(
            chain,
            lambda sid: LianChatHistory(client=client, session_id=sid),
        )
    """

    def __init__(
        self,
        client: Any,
        session_id: str,
        agent_id: str = "chat",
        max_messages: int = 100,
    ) -> None:
        self._client = client
        self._session_id = session_id
        self._agent_id = agent_id
        self._max_messages = max_messages

    # ------------------------------------------------------------------
    # BaseChatMessageHistory interface
    # ------------------------------------------------------------------

    @property
    def messages(self) -> list[BaseMessage]:
        result = self._client.recall(
            agent_id=self._agent_id,
            query="conversation message",
            k=self._max_messages,
            filters={"session_id": self._session_id},
        )
        raw = result.get("memories", [])
        # Sort chronologically — recall returns by relevance score
        raw.sort(key=lambda m: m.get("event_time") or "")
        out: list[BaseMessage] = []
        for m in raw:
            content = m.get("content")
            if not content:
                continue
            try:
                msg_dict = json.loads(content)
                out.extend(messages_from_dict([msg_dict]))
            except (json.JSONDecodeError, KeyError):
                pass
        return out

    def add_messages(self, messages: Sequence[BaseMessage]) -> None:
        # Offset each message by 1 ms within the batch so chronological sort
        # is stable even when all messages arrive in the same wall-clock second.
        base_time = datetime.now(timezone.utc)
        for i, message in enumerate(messages):
            self._client.add(
                agent_id=self._agent_id,
                content=json.dumps(message_to_dict(message)),
                event_time=base_time + timedelta(milliseconds=i),
                source="langchain_chat",
                metadata={
                    "session_id": self._session_id,
                    "msg_type": message.type,
                },
            )

    def clear(self) -> None:
        # Lian's audit trail is immutable — nothing to delete.
        # For GDPR erasure use client.erase(subject_id=...) explicitly.
        pass


# ---------------------------------------------------------------------------
# LianTools
# ---------------------------------------------------------------------------

class _RememberInput(BaseModel):
    content: str = Field(..., description="The fact, observation, or decision to remember.")
    event_time_iso: str = Field(
        ...,
        description=(
            "ISO 8601 timestamp of when this event occurred — NOT when you are "
            "recording it.  E.g. '2026-05-10T00:00:00Z' for an earnings call on "
            "May 10."
        ),
    )
    metadata: dict = Field(
        default_factory=dict,
        description=(
            "Optional key-value tags for filtering.  Useful fields: "
            "ticker, metric, quarter, entity, source."
        ),
    )


class _RecallInput(BaseModel):
    query: str = Field(..., description="Natural-language query describing what to retrieve.")
    k: int = Field(default=5, ge=1, le=20, description="Number of memories to return.")


class _RecallAtInput(BaseModel):
    query: str = Field(..., description="Natural-language query describing what to retrieve.")
    as_of_iso: str = Field(
        ...,
        description=(
            "ISO 8601 timestamp.  Only memories that were valid at this point in "
            "time are returned.  Use for compliance and audit queries such as "
            "'what guidance did we have on 2026-03-01?'"
        ),
    )
    k: int = Field(default=5, ge=1, le=20, description="Number of memories to return.")


def _format_memories(memories: list[dict]) -> str:
    """Render a recall result list as a plain-text string for LLM consumption."""
    if not memories:
        return "No relevant memories found."
    lines: list[str] = []
    for m in memories:
        ts = (m.get("event_time") or "")[:10]
        content = m.get("content") or "[erased]"
        lines.append(f"[{ts}] {content}")
    return "\n".join(lines)


def build_tools(client: Any, agent_id: str) -> list[BaseTool]:
    """
    Return three LangChain tools wired to the given Lian client.

    remember   — store a fact with its event timestamp
    recall     — retrieve current relevant memories by semantic search
    recall_at  — retrieve memories valid at a specific past date (compliance)

    Example::

        tools = build_tools(LocalLianClient(), agent_id="research-agent")
        agent = create_react_agent(llm, tools, prompt)
    """

    def _remember(content: str, event_time_iso: str, metadata: dict = {}) -> str:
        dt = datetime.fromisoformat(event_time_iso.replace("Z", "+00:00"))
        client.add(
            agent_id=agent_id,
            content=content,
            event_time=dt,
            source="langchain_agent",
            metadata=metadata,
        )
        preview = content[:120] + ("…" if len(content) > 120 else "")
        return f"Stored: {preview}"

    def _recall(query: str, k: int = 5) -> str:
        result = client.recall(agent_id=agent_id, query=query, k=k)
        return _format_memories(result.get("memories", []))

    def _recall_at(query: str, as_of_iso: str, k: int = 5) -> str:
        as_of = datetime.fromisoformat(as_of_iso.replace("Z", "+00:00"))
        result = client.recall(agent_id=agent_id, query=query, k=k, as_of=as_of)
        header = f"Memories valid as of {as_of_iso[:10]}:"
        body = _format_memories(result.get("memories", []))
        return f"{header}\n{body}"

    remember_tool = StructuredTool.from_function(
        func=_remember,
        name="remember",
        description=(
            "Store a financial fact, observation, or decision in persistent memory. "
            "Always provide the event_time_iso for when the event occurred, "
            "not the current time.  Add ticker/metric metadata when relevant."
        ),
        args_schema=_RememberInput,
    )

    recall_tool = StructuredTool.from_function(
        func=_recall,
        name="recall",
        description=(
            "Retrieve the most relevant current memories for a query. "
            "Returns facts that are presently valid (superseded facts are excluded). "
            "Use this before answering any question that might be in memory."
        ),
        args_schema=_RecallInput,
    )

    recall_at_tool = StructuredTool.from_function(
        func=_recall_at,
        name="recall_at",
        description=(
            "Retrieve memories that were valid at a specific point in time. "
            "Use for compliance and audit questions: 'What guidance did we have "
            "on 2026-03-01?' or 'What was the consensus estimate before the revision?'. "
            "This is point-in-time recall — later superseding updates are excluded."
        ),
        args_schema=_RecallAtInput,
    )

    return [remember_tool, recall_tool, recall_at_tool]


class LianTools:
    """
    Convenience wrapper — holds a client and builds tools on demand.

    Example::

        tools_provider = LianTools(client=LocalLianClient(), agent_id="agent-1")
        agent = create_react_agent(llm, tools_provider.as_tools(), prompt)
    """

    def __init__(self, client: Any, agent_id: str) -> None:
        self._client = client
        self._agent_id = agent_id

    def as_tools(self) -> list[BaseTool]:
        return build_tools(self._client, self._agent_id)
