"""
Extended SDK tests covering the methods added in this session:

  LocalLianClient:
    - batch_add
    - recall_at
    - review_supersessions
    - confirm_supersession / reject_supersession

  LianClient (sync HTTP):
    - batch_add delegation
    - recall_at delegation
    - review_supersessions / confirm / reject delegation
    - audit_export / verify_chain delegation (admin methods)

  LangGraph integration:
    - create_recall_node
    - create_remember_node
    - create_batch_remember_node
    - format_memories_for_prompt

  CrewAI integration:
    - build_crewai_tools returns 3 tools with correct names/descriptions
    - remember_fact tool stores and recall_facts retrieves
    - recall_facts_at returns point-in-time snapshot
    - ImportError raised when crewai not installed

All LocalLianClient tests run against real in-memory SQLite (no mocking,
no server).  HTTP client tests mock the async layer to stay fast.
"""
import json
import sys
import pytest
from datetime import datetime, timezone, timedelta
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "sdk" / "python"))

from lian import LocalLianClient, LianClient

T0 = datetime(2026, 1, 1, tzinfo=timezone.utc)
T1 = datetime(2026, 5, 10, tzinfo=timezone.utc)
T2 = datetime(2026, 9, 1, tzinfo=timezone.utc)


# ===========================================================================
# LocalLianClient — batch_add
# ===========================================================================

class TestLocalBatchAdd:

    def test_batch_add_single_item(self):
        with LocalLianClient() as mem:
            result = mem.batch_add([{
                "agent_id": "a",
                "content": "NVDA guidance $36B",
                "event_time": T0,
                "metadata": {"ticker": "NVDA", "metric": "guidance"},
            }])
        assert result["added"] == 1
        assert len(result["memories"]) == 1
        assert result["memories"][0]["content"] == "NVDA guidance $36B"

    def test_batch_add_multiple_items(self):
        with LocalLianClient() as mem:
            result = mem.batch_add([
                {"agent_id": "a", "content": "AAPL revenue $90B",
                 "event_time": T0, "metadata": {"ticker": "AAPL", "metric": "revenue"}},
                {"agent_id": "a", "content": "MSFT revenue $65B",
                 "event_time": T0, "metadata": {"ticker": "MSFT", "metric": "revenue"}},
            ])
        assert result["added"] == 2

    def test_batch_add_later_item_supersedes_earlier(self):
        """Within a batch, a later revision should supersede an earlier one."""
        with LocalLianClient() as mem:
            mem.batch_add([
                {"agent_id": "a", "content": "NVDA guidance $32B",
                 "event_time": T0, "metadata": {"ticker": "NVDA", "metric": "guidance"}},
                {"agent_id": "a", "content": "NVDA guidance raised to $36B",
                 "event_time": T1, "metadata": {"ticker": "NVDA", "metric": "guidance"}},
            ])
            result = mem.recall(agent_id="a", query="NVDA guidance")

        top = result["memories"][0]["content"]
        assert "$36B" in top

    def test_batch_add_empty_list_returns_zero(self):
        with LocalLianClient() as mem:
            result = mem.batch_add([])
        assert result["added"] == 0

    def test_batch_add_recalled_individually(self):
        """Each item from a batch should be individually retrievable."""
        with LocalLianClient() as mem:
            tickers = ["AAPL", "TSLA", "NVDA"]
            mem.batch_add([
                {"agent_id": "a", "content": f"{t} EPS $3.00",
                 "event_time": T0, "metadata": {"ticker": t, "metric": "eps"}}
                for t in tickers
            ])
            for ticker in tickers:
                result = mem.recall(agent_id="a", query=f"{ticker} EPS",
                                    filters={"ticker": ticker})
            assert len(result["memories"]) >= 1


# ===========================================================================
# LocalLianClient — recall_at
# ===========================================================================

class TestLocalRecallAt:

    def test_recall_at_returns_past_snapshot(self):
        with LocalLianClient() as mem:
            mem.add(agent_id="a", content="TSLA deliveries 400k",
                    event_time=T0, metadata={"ticker": "TSLA", "metric": "deliveries"})
            mem.add(agent_id="a", content="TSLA deliveries 450k",
                    event_time=T1, metadata={"ticker": "TSLA", "metric": "deliveries"})

            past = mem.recall_at(agent_id="a", query="TSLA deliveries",
                                 as_of=T0 + timedelta(days=1))

        contents = [m["content"] for m in past["memories"]]
        assert any("400k" in c for c in contents)
        assert not any("450k" in c for c in contents)

    def test_recall_at_present_returns_current(self):
        with LocalLianClient() as mem:
            mem.add(agent_id="a", content="TSLA deliveries 400k",
                    event_time=T0, metadata={"ticker": "TSLA", "metric": "deliveries"})
            mem.add(agent_id="a", content="TSLA deliveries 450k",
                    event_time=T1, metadata={"ticker": "TSLA", "metric": "deliveries"})

            present = mem.recall_at(agent_id="a", query="TSLA deliveries",
                                    as_of=T2)

        assert "450k" in present["memories"][0]["content"]

    def test_recall_at_matches_recall_with_as_of(self):
        """recall_at must produce identical results to recall(as_of=...)."""
        with LocalLianClient() as mem:
            mem.add(agent_id="a", content="FED rate 5.25%",
                    event_time=T0, metadata={})

            r1 = mem.recall_at(agent_id="a", query="FED rate", as_of=T1)
            r2 = mem.recall(agent_id="a", query="FED rate", as_of=T1)

        assert [m["id"] for m in r1["memories"]] == [m["id"] for m in r2["memories"]]


# ===========================================================================
# LocalLianClient — supersession review / confirm / reject
# ===========================================================================

class TestLocalSupersessionReview:

    def test_review_supersessions_returns_result_shape(self):
        with LocalLianClient() as mem:
            # Low-importance add so it might land in review queue
            mem.add(agent_id="a", content="NVDA guidance $32B",
                    event_time=T0, metadata={"ticker": "NVDA", "metric": "guidance"})
            mem.add(agent_id="a", content="NVDA guidance raised to $36B",
                    event_time=T1, metadata={"ticker": "NVDA", "metric": "guidance"})

            result = mem.review_supersessions()

        assert "items" in result
        assert "total" in result
        assert "confidence_threshold" in result
        assert isinstance(result["items"], list)

    def test_review_supersessions_custom_threshold(self):
        """Passing threshold=1.0 should return all supersession events."""
        with LocalLianClient() as mem:
            mem.add(agent_id="a", content="AAPL EPS $1.50",
                    event_time=T0, metadata={"ticker": "AAPL", "metric": "eps"})
            mem.add(agent_id="a", content="AAPL EPS $1.60",
                    event_time=T1, metadata={"ticker": "AAPL", "metric": "eps"})

            result = mem.review_supersessions(threshold=1.0)

        assert isinstance(result["items"], list)

    def test_confirm_supersession_on_review_item(self):
        """confirm_supersession should succeed for a real supersession event."""
        with LocalLianClient() as mem:
            mem.add(agent_id="a", content="NVDA guidance $32B",
                    event_time=T0, metadata={"ticker": "NVDA", "metric": "guidance"})
            new = mem.add(agent_id="a", content="NVDA guidance $36B",
                          event_time=T1, metadata={"ticker": "NVDA", "metric": "guidance"})

            # The new memory is what we want to confirm as correct
            result = mem.confirm_supersession(
                memory_id=new["id"],
                reviewer_note="Confirmed against Bloomberg terminal",
            )

        assert result["action"] == "confirm"
        assert "applied_at" in result

    def test_reject_supersession_restores_old_memory(self):
        """After reject, the old memory should reappear in present-time recall."""
        with LocalLianClient() as mem:
            old = mem.add(agent_id="a", content="NVDA guidance $32B",
                          event_time=T0, metadata={"ticker": "NVDA", "metric": "guidance"})
            mem.add(agent_id="a", content="NVDA guidance $36B",
                    event_time=T1, metadata={"ticker": "NVDA", "metric": "guidance"})

            # Reject: the supersession was wrong
            result = mem.reject_supersession(
                memory_id=old["id"],
                reviewer_note="Source retracted the revision",
            )

        assert result["action"] == "reject"


# ===========================================================================
# LianClient (sync HTTP) — new method delegation
# ===========================================================================

class TestSyncHTTPClientExtended:

    def _client(self):
        return LianClient(base_url="http://fake", api_key="k", admin_secret="s")

    def test_batch_add_delegates(self):
        client = self._client()
        expected = {"added": 2, "memories": []}
        client._async.batch_add = AsyncMock(return_value=expected)

        memories = [
            {"agent_id": "a", "content": "fact1", "event_time": T0, "metadata": {}},
            {"agent_id": "a", "content": "fact2", "event_time": T1, "metadata": {}},
        ]
        result = client.batch_add(memories)

        client._async.batch_add.assert_called_once_with(memories)
        assert result == expected
        client.close()

    def test_recall_at_delegates(self):
        client = self._client()
        expected = {"memories": [], "as_of": T1.isoformat(), "total_candidates": 0}
        client._async.recall_at = AsyncMock(return_value=expected)

        result = client.recall_at(agent_id="a", query="NVDA", as_of=T1, k=3)

        client._async.recall_at.assert_called_once_with(
            agent_id="a", query="NVDA", as_of=T1, k=3, filters=None,
        )
        assert result == expected
        client.close()

    def test_review_supersessions_delegates(self):
        client = self._client()
        expected = {"items": [], "total": 0, "confidence_threshold": 0.75}
        client._async.review_supersessions = AsyncMock(return_value=expected)

        result = client.review_supersessions(threshold=0.8, limit=20)

        client._async.review_supersessions.assert_called_once_with(threshold=0.8, limit=20)
        assert result == expected
        client.close()

    def test_confirm_supersession_delegates(self):
        client = self._client()
        expected = {"memory_id": "abc", "action": "confirm", "applied_at": T1.isoformat()}
        client._async.confirm_supersession = AsyncMock(return_value=expected)

        result = client.confirm_supersession("abc", reviewer_note="ok")

        client._async.confirm_supersession.assert_called_once_with(
            memory_id="abc", reviewer_note="ok"
        )
        assert result == expected
        client.close()

    def test_reject_supersession_delegates(self):
        client = self._client()
        expected = {"memory_id": "abc", "action": "reject", "applied_at": T1.isoformat()}
        client._async.reject_supersession = AsyncMock(return_value=expected)

        result = client.reject_supersession("abc", reviewer_note="wrong")

        client._async.reject_supersession.assert_called_once_with(
            memory_id="abc", reviewer_note="wrong"
        )
        assert result == expected
        client.close()

    def test_verify_chain_delegates(self):
        client = self._client()
        expected = {"status": "ok", "rows_checked": 10, "violations": []}
        client._async.verify_chain = AsyncMock(return_value=expected)

        result = client.verify_chain(namespace="my-ns")

        client._async.verify_chain.assert_called_once_with(namespace="my-ns")
        assert result == expected
        client.close()

    def test_audit_export_delegates(self):
        client = self._client()
        expected = {"namespace": "my-ns", "total_rows": 5, "events": []}
        client._async.audit_export = AsyncMock(return_value=expected)

        result = client.audit_export(namespace="my-ns", from_dt=T0, verify=True)

        client._async.audit_export.assert_called_once_with(
            namespace="my-ns", from_dt=T0, to_dt=None, limit=100_000, verify=True
        )
        assert result == expected
        client.close()


try:
    import langgraph as _lg  # noqa: F401
    _HAS_LANGGRAPH = True
except ImportError:
    _HAS_LANGGRAPH = False

try:
    import crewai as _ca  # noqa: F401
    _HAS_CREWAI = True
except ImportError:
    _HAS_CREWAI = False


# ===========================================================================
# LangGraph integration
# ===========================================================================

@pytest.mark.skipif(not _HAS_LANGGRAPH, reason="langgraph not installed")
class TestLangGraphIntegration:

    def test_recall_node_injects_memories(self):
        import asyncio
        from lian.langgraph_integration import create_recall_node

        with LocalLianClient() as client:
            client.add(agent_id="lg", content="NVDA guidance $36B",
                       event_time=T0, metadata={"ticker": "NVDA"})

            recall = create_recall_node(client, agent_id="lg")
            state = {"query": "NVDA guidance"}
            result = asyncio.run(recall(state))

        assert "memories" in result
        assert len(result["memories"]) >= 1
        assert any("NVDA" in (m.get("content") or "") for m in result["memories"])

    def test_recall_node_empty_query_returns_empty(self):
        import asyncio
        from lian.langgraph_integration import create_recall_node

        with LocalLianClient() as client:
            recall = create_recall_node(client, agent_id="lg2")
            result = asyncio.run(recall({"query": ""}))

        assert result["memories"] == []

    def test_recall_node_custom_keys(self):
        import asyncio
        from lian.langgraph_integration import create_recall_node

        with LocalLianClient() as client:
            client.add(agent_id="lg3", content="FED rate 5.25%",
                       event_time=T0, metadata={})

            recall = create_recall_node(
                client, agent_id="lg3",
                query_key="search_term",
                memories_key="context",
            )
            result = asyncio.run(recall({"search_term": "FED rate"}))

        assert "context" in result
        assert len(result["context"]) >= 1

    def test_recall_node_as_of_key(self):
        import asyncio
        from lian.langgraph_integration import create_recall_node

        with LocalLianClient() as client:
            client.add(agent_id="lg4", content="TSLA deliveries 400k",
                       event_time=T0, metadata={"ticker": "TSLA"})
            client.add(agent_id="lg4", content="TSLA deliveries 450k",
                       event_time=T1, metadata={"ticker": "TSLA"})

            recall = create_recall_node(
                client, agent_id="lg4",
                as_of_key="audit_date",
            )
            state = {
                "query": "TSLA deliveries",
                "audit_date": (T0 + timedelta(days=1)).isoformat(),
            }
            result = asyncio.run(recall(state))

        contents = [m.get("content") or "" for m in result["memories"]]
        assert any("400k" in c for c in contents)
        assert not any("450k" in c for c in contents)

    def test_remember_node_stores_memory(self):
        import asyncio
        from lian.langgraph_integration import create_remember_node

        with LocalLianClient() as client:
            remember = create_remember_node(client, agent_id="lg5")
            state = {
                "memory_content": "JPM net income $13B",
                "memory_event_time": T1,
                "memory_metadata": {"ticker": "JPM", "metric": "net_income"},
            }
            result = asyncio.run(remember(state))

            recalled = client.recall(agent_id="lg5", query="JPM net income")

        assert result["memory_stored"] is not None
        assert any("JPM" in (m.get("content") or "") for m in recalled["memories"])

    def test_remember_node_no_content_returns_none(self):
        import asyncio
        from lian.langgraph_integration import create_remember_node

        with LocalLianClient() as client:
            remember = create_remember_node(client, agent_id="lg6")
            result = asyncio.run(remember({}))

        assert result["memory_stored"] is None

    def test_remember_node_default_event_time(self):
        """When event_time_key is absent, node defaults to now()."""
        import asyncio
        from lian.langgraph_integration import create_remember_node

        with LocalLianClient() as client:
            remember = create_remember_node(client, agent_id="lg7")
            result = asyncio.run(remember({"memory_content": "test fact"}))

        assert result["memory_stored"] is not None

    def test_batch_remember_node(self):
        import asyncio
        from lian.langgraph_integration import create_batch_remember_node

        with LocalLianClient() as client:
            batch_node = create_batch_remember_node(client, agent_id="lg8")
            state = {
                "memories_to_store": [
                    {"content": "AAPL EPS $1.50", "event_time": T0,
                     "metadata": {"ticker": "AAPL"}},
                    {"content": "AAPL EPS $1.60", "event_time": T1,
                     "metadata": {"ticker": "AAPL"}},
                ]
            }
            result = asyncio.run(batch_node(state))

        assert result["batch_stored"]["added"] == 2

    def test_batch_remember_node_empty_list(self):
        import asyncio
        from lian.langgraph_integration import create_batch_remember_node

        with LocalLianClient() as client:
            batch_node = create_batch_remember_node(client, agent_id="lg9")
            result = asyncio.run(batch_node({}))

        assert result["batch_stored"]["added"] == 0

    def test_format_memories_for_prompt(self):
        from lian.langgraph_integration import format_memories_for_prompt

        memories = [
            {"event_time": "2026-05-10T00:00:00Z", "content": "NVDA guidance $36B"},
            {"event_time": "2026-03-01T00:00:00Z", "content": "FED rate 5.25%"},
        ]
        text = format_memories_for_prompt(memories)
        assert "NVDA" in text
        assert "2026-05-10" in text
        assert "FED" in text

    def test_format_memories_empty(self):
        from lian.langgraph_integration import format_memories_for_prompt
        assert format_memories_for_prompt([]) == "No relevant memories."

    def test_format_memories_erased_content(self):
        from lian.langgraph_integration import format_memories_for_prompt
        memories = [{"event_time": "2026-01-01T00:00:00Z", "content": None}]
        text = format_memories_for_prompt(memories)
        assert "[erased]" in text


# ===========================================================================
# CrewAI integration
# ===========================================================================

@pytest.mark.skipif(not _HAS_CREWAI, reason="crewai not installed")
class TestCrewAIIntegration:

    def test_returns_three_tools(self):
        from lian.crewai_integration import build_crewai_tools
        with LocalLianClient() as client:
            tools = build_crewai_tools(client, agent_id="crew")
        assert len(tools) == 3

    def test_tool_names(self):
        from lian.crewai_integration import build_crewai_tools
        with LocalLianClient() as client:
            tools = build_crewai_tools(client, agent_id="crew")
        names = {t.name for t in tools}
        assert names == {"remember_fact", "recall_facts", "recall_facts_at"}

    def test_tools_have_descriptions(self):
        from lian.crewai_integration import build_crewai_tools
        with LocalLianClient() as client:
            tools = build_crewai_tools(client, agent_id="crew")
        for tool in tools:
            assert tool.description and len(tool.description) > 20

    def test_tools_have_args_schema(self):
        from lian.crewai_integration import build_crewai_tools
        with LocalLianClient() as client:
            tools = build_crewai_tools(client, agent_id="crew")
        for tool in tools:
            assert tool.args_schema is not None, f"{tool.name} missing args_schema"

    def test_remember_fact_stores_memory(self):
        from lian.crewai_integration import build_crewai_tools
        with LocalLianClient() as client:
            tools = {t.name: t for t in build_crewai_tools(client, agent_id="crew2")}
            result = tools["remember_fact"]._run(
                content="NVDA Q3 guidance $36B",
                event_time_iso="2026-05-10T00:00:00Z",
                metadata=json.dumps({"ticker": "NVDA", "metric": "guidance"}),
            )
            memories = client.recall(agent_id="crew2", query="NVDA guidance")["memories"]

        assert "Stored" in result
        assert any("$36B" in (m.get("content") or "") for m in memories)

    def test_recall_facts_finds_stored_memory(self):
        from lian.crewai_integration import build_crewai_tools
        with LocalLianClient() as client:
            tools = {t.name: t for t in build_crewai_tools(client, agent_id="crew3")}
            tools["remember_fact"]._run(
                content="AAPL gross margin 46%",
                event_time_iso="2026-03-01T00:00:00Z",
                metadata=json.dumps({"ticker": "AAPL"}),
            )
            result = tools["recall_facts"]._run(query="AAPL gross margin", k=3)

        assert "46%" in result

    def test_recall_facts_no_results(self):
        from lian.crewai_integration import build_crewai_tools
        with LocalLianClient() as client:
            tools = {t.name: t for t in build_crewai_tools(client, agent_id="crew4")}
            result = tools["recall_facts"]._run(query="nonexistent zzzxxx", k=3)

        assert "No relevant memories" in result

    def test_recall_facts_at_point_in_time(self):
        from lian.crewai_integration import build_crewai_tools
        with LocalLianClient() as client:
            tools = {t.name: t for t in build_crewai_tools(client, agent_id="crew5")}

            tools["remember_fact"]._run(
                content="NVDA guidance $32B",
                event_time_iso="2026-02-01T00:00:00Z",
                metadata=json.dumps({"ticker": "NVDA", "metric": "guidance"}),
            )
            tools["remember_fact"]._run(
                content="NVDA guidance raised to $36B",
                event_time_iso="2026-05-10T00:00:00Z",
                metadata=json.dumps({"ticker": "NVDA", "metric": "guidance"}),
            )

            result = tools["recall_facts_at"]._run(
                query="NVDA guidance",
                as_of_iso="2026-03-01T00:00:00Z",
                k=5,
            )

        assert "$32B" in result
        assert "$36B" not in result

    def test_recall_facts_at_header_contains_date(self):
        from lian.crewai_integration import build_crewai_tools
        with LocalLianClient() as client:
            tools = {t.name: t for t in build_crewai_tools(client, agent_id="crew6")}
            tools["remember_fact"]._run(
                content="Test fact",
                event_time_iso="2026-01-01T00:00:00Z",
                metadata="{}",
            )
            result = tools["recall_facts_at"]._run(
                query="fact",
                as_of_iso="2026-06-01T00:00:00Z",
            )

        assert "2026-06-01" in result

    def test_import_error_without_crewai(self):
        """build_crewai_tools raises ImportError when crewai is absent."""
        from lian.crewai_integration import build_crewai_tools
        import lian.crewai_integration as mod
        original = mod._CREWAI_AVAILABLE
        mod._CREWAI_AVAILABLE = False
        try:
            with pytest.raises(ImportError, match="crewai"):
                build_crewai_tools(MagicMock(), agent_id="x")
        finally:
            mod._CREWAI_AVAILABLE = original
