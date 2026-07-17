"""Unit coverage for zero-config MCP routing into LocalLiansClient."""
from datetime import datetime
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "sdk" / "python"))

from lians import mcp_server


class _FakeLocalClient:
    def __init__(self):
        self.calls = []

    def add(self, **kwargs):
        self.calls.append(("add", kwargs))
        return {"id": "memory-1"}

    def recall(self, **kwargs):
        self.calls.append(("recall", kwargs))
        return {"memories": []}

    def memory_lineage(self, memory_id):
        self.calls.append(("memory_lineage", {"memory_id": memory_id}))
        return {"nodes": [], "edges": []}

    def fact_history(self, **kwargs):
        self.calls.append(("fact_history", kwargs))
        return []


def test_local_remember_parses_iso_timestamp(monkeypatch):
    fake = _FakeLocalClient()
    monkeypatch.setattr(mcp_server, "_LOCAL_CLIENT", fake)

    result = mcp_server._local_api("POST", "/v1/memories", {
        "agent_id": "research",
        "content": "NVDA raised guidance",
        "event_time": "2026-07-17T14:30:00Z",
        "metadata": {"ticker": "NVDA"},
    })

    assert result == {"id": "memory-1"}
    name, values = fake.calls[0]
    assert name == "add"
    assert values["event_time"] == datetime.fromisoformat("2026-07-17T14:30:00+00:00")


def test_local_recall_at_preserves_point_in_time(monkeypatch):
    fake = _FakeLocalClient()
    monkeypatch.setattr(mcp_server, "_LOCAL_CLIENT", fake)

    mcp_server._local_api("POST", "/v1/recall", {
        "agent_id": "research",
        "query": "guidance",
        "as_of": "2026-01-01T00:00:00Z",
        "k": 7,
    })

    name, values = fake.calls[0]
    assert name == "recall"
    assert values["as_of"].isoformat() == "2026-01-01T00:00:00+00:00"
    assert values["k"] == 7


def test_local_query_routes_parse_query_strings(monkeypatch):
    fake = _FakeLocalClient()
    monkeypatch.setattr(mcp_server, "_LOCAL_CLIENT", fake)

    mcp_server._local_api("GET", "/v1/memories/abc-123/lineage")
    history = mcp_server._local_api(
        "GET",
        "/v1/facts/history?ticker=NVDA&metric=guidance&agent_id=desk&limit=12",
    )

    assert fake.calls[0] == ("memory_lineage", {"memory_id": "abc-123"})
    assert fake.calls[1] == ("fact_history", {
        "agent_id": "desk",
        "ticker": "NVDA",
        "metric": "guidance",
        "limit": 12,
    })
    assert history == {"ticker": "NVDA", "items": []}
