"""A credential-free LangGraph workflow with durable local Lians memory."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal, TypedDict

from langgraph.graph import END, START, StateGraph
from lians import LocalLiansClient
from lians.langgraph_integration import create_recall_node, create_remember_node

AGENT_ID = "release-coordinator"
REMEMBERED_FACT = "Project Aurora deploys with Python 3.12."
DEFAULT_DB_PATH = Path(__file__).parent / ".data" / "memory.db"


class MemoryState(TypedDict, total=False):
    """State shared by the remember and recall graph invocations."""

    action: Literal["remember", "recall"]
    memory_content: str
    memory_event_time: datetime
    memory_metadata: dict[str, str]
    memory_filters: dict[str, str]
    memory_stored: dict[str, Any] | None
    query: str
    memories: list[dict[str, Any]]


def _route(state: MemoryState) -> Literal["remember", "recall"]:
    """Select the requested memory operation for this invocation."""
    return state["action"]


def create_memory_graph(memory: LocalLiansClient) -> Any:
    """Compile a graph that can remember in one invocation and recall later."""
    graph = StateGraph(MemoryState)
    graph.add_node("remember", create_remember_node(memory, agent_id=AGENT_ID))
    graph.add_node(
        "recall",
        create_recall_node(
            memory,
            agent_id=AGENT_ID,
            filters_key="memory_filters",
        ),
    )
    graph.add_conditional_edges(
        START,
        _route,
        {"remember": "remember", "recall": "recall"},
    )
    graph.add_edge("remember", END)
    graph.add_edge("recall", END)
    return graph.compile()


async def _invoke_graph(memory: LocalLiansClient) -> dict[str, Any]:
    graph = create_memory_graph(memory)
    remembered = await graph.ainvoke(
        {
            "action": "remember",
            "memory_content": REMEMBERED_FACT,
            "memory_event_time": datetime(2026, 8, 1, 9, tzinfo=UTC),
            "memory_metadata": {"project": "aurora"},
        }
    )
    recalled = await graph.ainvoke(
        {
            "action": "recall",
            "query": "Which Python version does Project Aurora deploy with?",
            "memory_filters": {"project": "aurora"},
        }
    )

    return {"remembered": remembered["memory_stored"], "memories": recalled["memories"]}


def run_demo(db_path: Path = DEFAULT_DB_PATH) -> dict[str, Any]:
    """Run two graph invocations against one durable local memory store."""
    db_path.parent.mkdir(parents=True, exist_ok=True)
    with LocalLiansClient(db_path=db_path, embedding_provider="local") as memory:
        return asyncio.run(_invoke_graph(memory))


def main() -> None:
    result = run_demo()
    print("Remembered:", result["remembered"]["content"])
    print("Recalled later:", [memory["content"] for memory in result["memories"]])


if __name__ == "__main__":
    main()
