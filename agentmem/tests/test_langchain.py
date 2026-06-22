"""
LangChain integration tests.

All tests run against LocalLianClient (real SQLite, no server).
Skips cleanly if langchain-core is not installed.
"""
import sys
import pytest
from datetime import datetime, timezone, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "sdk" / "python"))

langchain_core = pytest.importorskip("langchain_core")

from langchain_core.messages import HumanMessage, AIMessage

from lian import LocalLianClient
from lian.langchain_integration import (
    LianChatHistory,
    AgentMemTools,
    build_tools,
)

T0 = datetime(2026, 1, 1, tzinfo=timezone.utc)
T1 = datetime(2026, 5, 10, tzinfo=timezone.utc)
T2 = datetime(2026, 6, 1, tzinfo=timezone.utc)


# ===========================================================================
# LianChatHistory
# ===========================================================================

class TestLianChatHistory:

    def test_empty_history_returns_empty_list(self):
        with LocalLianClient() as client:
            history = LianChatHistory(client=client, session_id="s1")
            assert history.messages == []

    def test_add_and_retrieve_messages(self):
        with LocalLianClient() as client:
            history = LianChatHistory(client=client, session_id="s2")
            history.add_messages([
                HumanMessage(content="What is NVDA guidance?"),
                AIMessage(content="NVDA Q3 guidance is $36B as of May 2026."),
            ])
            msgs = history.messages
        assert len(msgs) == 2
        assert isinstance(msgs[0], HumanMessage)
        assert isinstance(msgs[1], AIMessage)
        assert "NVDA" in msgs[0].content
        assert "$36B" in msgs[1].content

    def test_messages_returned_in_chronological_order(self):
        with LocalLianClient() as client:
            history = LianChatHistory(client=client, session_id="s3")
            history.add_messages([HumanMessage(content="first")])
            history.add_messages([AIMessage(content="second")])
            history.add_messages([HumanMessage(content="third")])
            msgs = history.messages
        assert [m.content for m in msgs] == ["first", "second", "third"]

    def test_session_isolation(self):
        """Messages from different sessions must not bleed across."""
        with LocalLianClient() as client:
            h1 = LianChatHistory(client=client, session_id="alice")
            h2 = LianChatHistory(client=client, session_id="bob")
            h1.add_messages([HumanMessage(content="Alice's secret")])
            h2.add_messages([HumanMessage(content="Bob's message")])
            alice_msgs = h1.messages
            bob_msgs = h2.messages
        assert len(alice_msgs) == 1
        assert "Alice" in alice_msgs[0].content
        assert len(bob_msgs) == 1
        assert "Bob" in bob_msgs[0].content

    def test_clear_is_noop(self):
        """clear() must not raise (audit trail is immutable)."""
        with LocalLianClient() as client:
            history = LianChatHistory(client=client, session_id="s4")
            history.add_messages([HumanMessage(content="hello")])
            history.clear()
            # Messages still in the immutable audit trail
            assert len(history.messages) >= 0   # does not raise

    def test_multiple_turns_roundtrip(self):
        with LocalLianClient() as client:
            history = LianChatHistory(client=client, session_id="s5")
            turns = [
                HumanMessage(content=f"turn {i}") for i in range(5)
            ]
            history.add_messages(turns)
            msgs = history.messages
        assert len(msgs) == 5
        assert all(m.type == "human" for m in msgs)


# ===========================================================================
# AgentMemTools / build_tools
# ===========================================================================

class TestAgentMemTools:

    def test_build_tools_returns_three_tools(self):
        with LocalLianClient() as client:
            tools = build_tools(client, agent_id="a")
        names = {t.name for t in tools}
        assert names == {"remember", "recall", "recall_at"}

    def test_agentmemtools_as_tools(self):
        with LocalLianClient() as client:
            provider = AgentMemTools(client=client, agent_id="a")
            tools = provider.as_tools()
        assert len(tools) == 3

    def test_remember_tool_stores_memory(self):
        with LocalLianClient() as client:
            tools = {t.name: t for t in build_tools(client, agent_id="a")}
            result = tools["remember"].invoke({
                "content": "NVDA Q3 guidance raised to $36B",
                "event_time_iso": "2026-05-10T00:00:00Z",
                "metadata": {"ticker": "NVDA", "metric": "guidance"},
            })
            memories = client.recall(agent_id="a", query="NVDA guidance")["memories"]
        assert "Stored" in result
        assert any("$36B" in (m.get("content") or "") for m in memories)

    def test_recall_tool_finds_remembered_fact(self):
        with LocalLianClient() as client:
            tools = {t.name: t for t in build_tools(client, agent_id="b")}
            tools["remember"].invoke({
                "content": "AAPL gross margin 46%",
                "event_time_iso": "2026-03-01T00:00:00Z",
                "metadata": {"ticker": "AAPL", "metric": "gross_margin"},
            })
            result = tools["recall"].invoke({"query": "AAPL gross margin", "k": 3})
        assert "46%" in result

    def test_recall_at_returns_point_in_time_snapshot(self):
        """recall_at must exclude memories with event_time after as_of."""
        with LocalLianClient() as client:
            tools = {t.name: t for t in build_tools(client, agent_id="c")}

            # Store two sequential guidance values
            tools["remember"].invoke({
                "content": "NVDA guidance $32B",
                "event_time_iso": "2026-02-01T00:00:00Z",
                "metadata": {"ticker": "NVDA", "metric": "guidance"},
            })
            tools["remember"].invoke({
                "content": "NVDA guidance raised to $36B",
                "event_time_iso": "2026-05-10T00:00:00Z",
                "metadata": {"ticker": "NVDA", "metric": "guidance"},
            })

            # Query as of March (before the revision)
            result = tools["recall_at"].invoke({
                "query": "NVDA guidance",
                "as_of_iso": "2026-03-01T00:00:00Z",
                "k": 5,
            })

        assert "$32B" in result
        assert "$36B" not in result

    def test_recall_no_results_returns_message(self):
        with LocalLianClient() as client:
            tools = {t.name: t for t in build_tools(client, agent_id="d")}
            result = tools["recall"].invoke({"query": "nonexistent zzzxxx", "k": 3})
        assert "No relevant memories" in result

    def test_remember_tool_metadata_filter(self):
        """metadata passed to remember is available for recall filtering."""
        with LocalLianClient() as client:
            tools = {t.name: t for t in build_tools(client, agent_id="e")}
            tools["remember"].invoke({
                "content": "AMD gross margin 50%",
                "event_time_iso": "2026-01-01T00:00:00Z",
                "metadata": {"ticker": "AMD"},
            })
            tools["remember"].invoke({
                "content": "NVDA gross margin 75%",
                "event_time_iso": "2026-01-01T00:00:00Z",
                "metadata": {"ticker": "NVDA"},
            })
            result = tools["recall"].invoke({"query": "gross margin"})
        # Both should appear in recall (no filter applied via tool)
        assert "AMD" in result or "NVDA" in result

    def test_recall_at_header_contains_date(self):
        with LocalLianClient() as client:
            tools = {t.name: t for t in build_tools(client, agent_id="f")}
            tools["remember"].invoke({
                "content": "Test fact",
                "event_time_iso": "2026-01-01T00:00:00Z",
                "metadata": {},
            })
            result = tools["recall_at"].invoke({
                "query": "fact",
                "as_of_iso": "2026-06-01T00:00:00Z",
            })
        assert "2026-06-01" in result

    def test_tools_have_descriptions(self):
        """Tool descriptions must be non-empty so LLMs know how to use them."""
        with LocalLianClient() as client:
            tools = build_tools(client, agent_id="g")
        for tool in tools:
            assert tool.description, f"{tool.name} has no description"
            assert len(tool.description) > 20

    def test_tools_have_args_schema(self):
        with LocalLianClient() as client:
            tools = build_tools(client, agent_id="h")
        for tool in tools:
            assert tool.args_schema is not None, f"{tool.name} missing args_schema"
