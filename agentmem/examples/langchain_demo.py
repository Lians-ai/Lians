"""
Demo: AgentMem + LangChain integration.

Shows two patterns:
  1. LianChatHistory — plugs into RunnableWithMessageHistory
  2. build_tools — three tools for a ReAct agent (no LLM required to run)

Run with no server needed:
    cd agentmem
    python examples/langchain_demo.py
"""
import sys
from pathlib import Path
from datetime import datetime, timezone

_root = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_root / "sdk" / "python"))
sys.path.insert(0, str(_root))

from lian import LocalLianClient
from lian.langchain_integration import LianChatHistory, build_tools
from langchain_core.messages import HumanMessage, AIMessage


def demo_chat_history():
    print("=== Pattern 1: LianChatHistory ===\n")
    client = LocalLianClient()

    # Simulate a multi-turn conversation stored in AgentMem
    history = LianChatHistory(client=client, session_id="demo-session-1")

    history.add_messages([
        HumanMessage(content="What is NVDA's Q3 guidance?"),
        AIMessage(content="NVDA Q3 FY2026 guidance is $36B per the May analyst day."),
        HumanMessage(content="What was the original guidance before the revision?"),
        AIMessage(content="The original Q3 guidance was $32B from the February earnings call."),
    ])

    print("Stored 4 messages.  Retrieving in order:")
    for msg in history.messages:
        role = "User" if msg.type == "human" else "AI  "
        print(f"  {role}: {msg.content}")

    print(f"\n(Works with RunnableWithMessageHistory — pass a lambda that returns")
    print(f" LianChatHistory(client=client, session_id=session_id))\n")
    client.close()


def demo_agent_tools():
    print("=== Pattern 2: AgentMem Tools for ReAct agents ===\n")
    client = LocalLianClient()
    tools = {t.name: t for t in build_tools(client, agent_id="research-agent")}

    print("Available tools:", list(tools.keys()))
    print()

    # remember
    tools["remember"].invoke({
        "content": "NVDA Q3 FY2026 guidance: $32B",
        "event_time_iso": "2026-02-01T00:00:00Z",
        "metadata": {"ticker": "NVDA", "metric": "guidance", "quarter": "Q3FY26"},
    })
    tools["remember"].invoke({
        "content": "NVDA raises Q3 FY2026 guidance to $36B (analyst day)",
        "event_time_iso": "2026-05-10T00:00:00Z",
        "metadata": {"ticker": "NVDA", "metric": "guidance", "quarter": "Q3FY26"},
    })
    print("Stored two NVDA guidance facts.\n")

    # recall — present time (returns current valid value)
    print("recall('NVDA guidance'):")
    print(tools["recall"].invoke({"query": "NVDA guidance", "k": 3}))

    # recall_at — point in time (before the revision)
    print("\nrecall_at('NVDA guidance', as_of='2026-03-01')  <- before revision:")
    print(tools["recall_at"].invoke({
        "query": "NVDA guidance",
        "as_of_iso": "2026-03-01T00:00:00Z",
    }))

    print("\n(Pass tools to create_react_agent / LangGraph node)")
    client.close()


if __name__ == "__main__":
    demo_chat_history()
    demo_agent_tools()
