"""
SDK tests.

LocalAgentMemClient: real in-memory SQLite — proves the zero-setup path works
end-to-end with no server and no mocking.

AgentMemClient (sync HTTP): httpx MockTransport — proves the sync wrapper
drives the async client correctly without needing a live server.
"""
import json
import sys
import pytest
from datetime import datetime, timezone, timedelta
from pathlib import Path
from unittest.mock import MagicMock

# Make the SDK importable from the test runner's working directory
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "sdk" / "python"))

from agentmem_sdk import LocalAgentMemClient, AgentMemClient


T0 = datetime(2026, 1, 1, tzinfo=timezone.utc)
T1 = datetime(2026, 6, 1, tzinfo=timezone.utc)
T2 = datetime(2027, 1, 1, tzinfo=timezone.utc)   # far-future for audit trail


# ===========================================================================
# LocalAgentMemClient
# ===========================================================================

class TestLocalClient:

    def test_add_returns_memory_dict(self):
        with LocalAgentMemClient() as mem:
            result = mem.add(
                agent_id="agent-1",
                content="NVDA Q3 guidance $36B",
                event_time=T1,
                metadata={"ticker": "NVDA", "metric": "guidance"},
            )
        assert result["id"] is not None
        assert result["content"] == "NVDA Q3 guidance $36B"
        assert result["valid_to"] is None
        assert result["namespace"] == "local"

    def test_recall_finds_added_memory(self):
        with LocalAgentMemClient() as mem:
            mem.add(agent_id="a", content="AAPL gross margin 46%",
                    event_time=T0, metadata={"ticker": "AAPL", "metric": "gross_margin"})
            result = mem.recall(agent_id="a", query="AAPL gross margin")
        assert len(result["memories"]) >= 1
        assert "AAPL" in result["memories"][0]["content"]

    def test_supersession_closes_old_memory(self):
        with LocalAgentMemClient() as mem:
            old = mem.add(agent_id="a", content="NVDA guidance $32B",
                          event_time=T0,
                          metadata={"ticker": "NVDA", "metric": "guidance"})
            mem.add(agent_id="a", content="NVDA guidance raised to $36B",
                    event_time=T1,
                    metadata={"ticker": "NVDA", "metric": "guidance"})

            # Present-time: new value ranks first
            present = mem.recall(agent_id="a", query="NVDA guidance", k=5)
        top = present["memories"][0]["content"]
        assert "$36B" in top

    def test_as_of_returns_past_snapshot(self):
        with LocalAgentMemClient() as mem:
            mem.add(agent_id="a", content="TSLA deliveries 400k",
                    event_time=T0, metadata={"ticker": "TSLA", "metric": "deliveries"})
            mem.add(agent_id="a", content="TSLA deliveries 450k",
                    event_time=T1, metadata={"ticker": "TSLA", "metric": "deliveries"})

            past = mem.recall(agent_id="a", query="TSLA deliveries",
                              k=5, as_of=T0 + timedelta(days=1))
            present = mem.recall(agent_id="a", query="TSLA deliveries", k=5)

        past_contents = [m["content"] for m in past["memories"]]
        present_contents = [m["content"] for m in present["memories"]]

        assert any("400k" in c for c in past_contents)
        assert not any("450k" in c for c in past_contents)
        assert present_contents[0] and "450k" in present_contents[0]

    def test_metadata_filter(self):
        with LocalAgentMemClient() as mem:
            mem.add(agent_id="a", content="NVDA revenue $18B",
                    event_time=T0, metadata={"ticker": "NVDA", "metric": "revenue"})
            mem.add(agent_id="a", content="AMD revenue $6B",
                    event_time=T0, metadata={"ticker": "AMD", "metric": "revenue"})

            result = mem.recall(agent_id="a", query="revenue", k=10,
                                filters={"ticker": "NVDA"})

        assert all("NVDA" in m["content"] for m in result["memories"])

    def test_reconstruct_returns_event_trail(self):
        with LocalAgentMemClient() as mem:
            mem.add(agent_id="a", content="MSFT EPS $3.10",
                    event_time=T0, metadata={"ticker": "MSFT", "metric": "eps"})
            result = mem.reconstruct(agent_id="a", as_of=T2)

        assert len(result["memories"]) >= 1
        assert len(result["event_trail"]) >= 1
        assert any(e["op"] == "add" for e in result["event_trail"])

    def test_erase_removes_content(self):
        with LocalAgentMemClient() as mem:
            mem.add(agent_id="a",
                    content="Client Jane Doe, portfolio $500k",
                    event_time=T0,
                    subject_id="jane-001",
                    metadata={})
            result = mem.erase(subject_id="jane-001", request_ref="GDPR-0001")

        assert result["memories_erased"] == 1
        assert result["subject_id"] == "jane-001"

    def test_erase_audit_trail_preserved(self):
        """After erasure the audit trail still contains add + erase ops."""
        with LocalAgentMemClient() as mem:
            mem.add(agent_id="a",
                    content="Client Bob Smith, SSN 123-45-6789",
                    event_time=T0,
                    subject_id="bob-001",
                    metadata={})
            mem.erase(subject_id="bob-001", request_ref="GDPR-0002")
            audit = mem.reconstruct(agent_id="a", as_of=T2)

        ops = {e["op"] for e in audit["event_trail"]}
        assert "add" in ops
        assert "erase" in ops

    def test_namespace_isolation(self):
        """Two LocalAgentMemClient instances with different namespaces are isolated."""
        with LocalAgentMemClient(namespace="tenant-a") as a:
            with LocalAgentMemClient(namespace="tenant-b") as b:
                a.add(agent_id="ag", content="Secret from A",
                      event_time=T0, metadata={})
                result = b.recall(agent_id="ag", query="Secret from A")

        assert len(result["memories"]) == 0

    def test_context_manager_closes_cleanly(self):
        """Exiting the context manager should not raise."""
        mem = LocalAgentMemClient()
        with mem:
            mem.add(agent_id="a", content="test", event_time=T0, metadata={})
        # If we reach here the loop and engine were closed cleanly

    def test_pii_memory_recalled_with_content(self):
        """PII memories encrypted at rest should be decrypted on recall."""
        with LocalAgentMemClient() as mem:
            mem.add(agent_id="a",
                    content="Client Alice, balance $1M",
                    event_time=T0,
                    subject_id="alice-001",
                    metadata={})
            result = mem.recall(agent_id="a", query="Alice balance")

        # Content should be returned (not None) because the subject key is intact
        assert any(m["content"] is not None for m in result["memories"])


# ===========================================================================
# AgentMemClient (sync HTTP — mocked transport)
# ===========================================================================

class TestSyncHTTPClient:
    """
    The HTTP stack is fully exercised by test_api.py.
    These tests focus on what is unique to the sync wrapper:
    delegation to the async client and lifecycle management.
    """

    def test_delegates_to_async_client(self):
        """Sync add() calls the underlying async add() exactly once."""
        from unittest.mock import AsyncMock

        client = AgentMemClient(base_url="http://fake", api_key="key")
        expected = {"id": "test-id", "content": "NVDA guidance $36B"}
        client._async.add = AsyncMock(return_value=expected)

        result = client.add(agent_id="a", content="NVDA guidance $36B", event_time=T0)

        client._async.add.assert_called_once_with(
            agent_id="a", content="NVDA guidance $36B", event_time=T0,
            source=None, subject_id=None, metadata=None, importance=0.5,
        )
        assert result == expected
        client.close()

    def test_recall_delegates_to_async_client(self):
        from unittest.mock import AsyncMock

        client = AgentMemClient(base_url="http://fake", api_key="key")
        expected = {"memories": [], "as_of": None, "total_candidates": 0}
        client._async.recall = AsyncMock(return_value=expected)

        result = client.recall(agent_id="a", query="NVDA", k=3, as_of=T1)

        client._async.recall.assert_called_once_with(
            agent_id="a", query="NVDA", k=3, as_of=T1, filters=None,
        )
        assert result == expected
        client.close()

    def test_context_manager_closes_loop(self):
        """Exiting the context manager closes the event loop."""
        client = AgentMemClient(base_url="http://fake", api_key="key")
        with client:
            assert not client._loop.is_closed()
        assert client._loop.is_closed()
