"""Unit coverage for zero-config MCP routing into LocalLiansClient."""
import asyncio
from datetime import datetime

import pytest
from mcp.types import CallToolRequest, CallToolRequestParams

from lians import local_client, mcp_server


class _FakeLocalClient:
    def __init__(self):
        self.calls = []

    def add(self, **kwargs):
        self.calls.append(("add", kwargs))
        return {"id": "memory-1"}

    def recall(self, **kwargs):
        self.calls.append(("recall", kwargs))
        return {"memories": []}

    def context(self, **kwargs):
        self.calls.append(("context", kwargs))
        return {"context": "", "memories": []}

    def memory_lineage(self, memory_id):
        self.calls.append(("memory_lineage", {"memory_id": memory_id}))
        return {"nodes": [], "edges": []}

    def fact_history(self, **kwargs):
        self.calls.append(("fact_history", kwargs))
        return []


def test_local_client_respects_pinned_embedding_provider(monkeypatch):
    monkeypatch.setenv("EMBEDDING_PROVIDER", "local")
    assert local_client._resolve_embedding_provider(None) == "local"
    assert local_client._resolve_embedding_provider("sentence-transformers") == (
        "sentence-transformers"
    )


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


def test_local_context_preserves_budget_filters_and_point_in_time(monkeypatch):
    fake = _FakeLocalClient()
    monkeypatch.setattr(mcp_server, "_LOCAL_CLIENT", fake)

    mcp_server._local_api("POST", "/v1/context", {
        "agent_id": "research",
        "query": "guidance",
        "as_of": "2026-01-01T00:00:00Z",
        "k": 50,
        "max_tokens": 2650,
        "filters": {"ticker": "NVDA"},
    })

    name, values = fake.calls[0]
    assert name == "context"
    assert values["as_of"].isoformat() == "2026-01-01T00:00:00+00:00"
    assert values["k"] == 50
    assert values["max_tokens"] == 2650
    assert values["filters"] == {"ticker": "NVDA"}
    assert values["mmr"] is False
    assert values["max_conflicts"] == 5


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


def test_mcp_tools_advertise_safe_approval_hints():
    server = mcp_server._build_server()
    handler = next(
        callback
        for request_type, callback in server.request_handlers.items()
        if request_type.__name__ == "ListToolsRequest"
    )

    result = asyncio.run(handler(type("Request", (), {"params": None})()))
    tools = {tool.name: tool for tool in result.root.tools}

    recall_schema = tools["recall"].inputSchema["properties"]
    assert recall_schema["k"]["default"] == 50
    assert recall_schema["max_tokens"]["default"] == 2650
    assert tools["remember"].annotations.readOnlyHint is False
    assert tools["remember"].annotations.idempotentHint is False
    for name in set(tools) - {"remember"}:
        assert tools[name].annotations.readOnlyHint is True
        assert tools[name].annotations.destructiveHint is False
        assert tools[name].annotations.idempotentHint is True


def test_mcp_tool_allowlist_is_validated_and_applied(monkeypatch):
    assert mcp_server._parse_enabled_tools(None) is None
    assert mcp_server._parse_enabled_tools("remember, recall") == frozenset({
        "remember", "recall",
    })
    with pytest.raises(ValueError, match="must not be blank"):
        mcp_server._parse_enabled_tools("   ")
    with pytest.raises(ValueError, match="unknown tool"):
        mcp_server._parse_enabled_tools("remember,not-a-tool")

    monkeypatch.setattr(
        mcp_server,
        "LIANS_MCP_ENABLED_TOOLS",
        frozenset({"remember", "recall", "recall_at"}),
    )
    server = mcp_server._build_server()
    handler = next(
        callback
        for request_type, callback in server.request_handlers.items()
        if request_type.__name__ == "ListToolsRequest"
    )
    result = asyncio.run(handler(type("Request", (), {"params": None})()))
    assert {tool.name for tool in result.root.tools} == {
        "remember", "recall", "recall_at",
    }


def test_mcp_recall_at_disables_present_day_conflict_resurfacing(monkeypatch):
    captured = {}

    async def fake_api(method, path, body=None):
        captured.update({"method": method, "path": path, "body": body})
        return {"context": "Memories valid at cutoff", "memories": []}

    monkeypatch.setattr(mcp_server, "_api", fake_api)
    server = mcp_server._build_server()
    handler = next(
        callback
        for request_type, callback in server.request_handlers.items()
        if request_type.__name__ == "CallToolRequest"
    )
    request = CallToolRequest(params=CallToolRequestParams(
        name="recall_at",
        arguments={
            "query": "historical guidance",
            "as_of_iso": "2026-01-01T00:00:00Z",
        },
    ))

    asyncio.run(handler(request))

    assert captured["method"] == "POST"
    assert captured["path"] == "/v1/context"
    assert captured["body"]["surface_conflicts"] is False
    assert captured["body"]["mmr"] is False
    assert captured["body"]["max_conflicts"] == 5


def test_mcp_recall_dispatches_same_context_defaults_for_local_and_hosted(monkeypatch):
    captured = {}

    async def fake_api(method, path, body=None):
        captured.update({"method": method, "path": path, "body": body})
        return {"context": "Relevant facts", "memories": [{"id": "1"}]}

    monkeypatch.setattr(mcp_server, "_api", fake_api)
    server = mcp_server._build_server()
    handler = next(
        callback
        for request_type, callback in server.request_handlers.items()
        if request_type.__name__ == "CallToolRequest"
    )
    request = CallToolRequest(params=CallToolRequestParams(
        name="recall",
        arguments={"query": "current guidance"},
    ))

    asyncio.run(handler(request))

    assert captured["body"]["mmr"] is False
    assert captured["body"]["surface_conflicts"] is True
    assert captured["body"]["max_conflicts"] == 5


def test_mcp_formatter_preserves_conflict_only_bounded_context():
    context = "Relevant facts\n⚠ UNRESOLVED MEMORY CONFLICTS\n- A DISAGREES WITH B"
    rendered = mcp_server._fmt_context({
        "context": context,
        "memories": [],
        "open_conflicts": [{"id": "conflict-1"}],
        "open_conflicts_total": 1,
        "truncated": True,
        "token_estimate": 64,
    })

    assert rendered == context
    assert "Context bounded" not in rendered


def test_mcp_prewarm_mode_is_explicit_and_validated():
    assert mcp_server._parse_prewarm_mode("background") == "background"
    assert mcp_server._parse_prewarm_mode("true") == "sync"
    assert mcp_server._parse_prewarm_mode("off") == "off"
    with pytest.raises(ValueError, match="LIANS_MCP_PREWARM"):
        mcp_server._parse_prewarm_mode("sometimes")


def test_mcp_project_scope_is_stable_isolated_and_fail_closed(tmp_path):
    first = tmp_path / "project-a"
    second = tmp_path / "project-b"

    assert mcp_server._parse_project_scope(None) is None
    assert mcp_server._parse_project_scope(str(first)) == (
        mcp_server._parse_project_scope(str(first))
    )
    assert mcp_server._parse_project_scope(str(first)) != (
        mcp_server._parse_project_scope(str(second))
    )
    with pytest.raises(ValueError, match="must not be blank"):
        mcp_server._parse_project_scope("   ")
