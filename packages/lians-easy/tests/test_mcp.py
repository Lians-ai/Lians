from __future__ import annotations

from lians_easy.mcp import MCPServer
from lians_easy.store import MemoryStore


def _call(server, request_id, name, arguments):
    return server.handle(
        {
            "jsonrpc": "2.0",
            "id": request_id,
            "method": "tools/call",
            "params": {"name": name, "arguments": arguments},
        }
    )["result"]


def test_two_ai_clients_share_one_memory_profile(tmp_path):
    database = tmp_path / "shared.sqlite3"
    claude = MCPServer(MemoryStore(database, profile="personal"))
    codex = MCPServer(MemoryStore(database, profile="personal"))

    stored = _call(
        claude,
        1,
        "remember",
        {"content": "The market research interview incentive is $25", "topic": "research"},
    )
    memory_id = stored["structuredContent"]["id"]
    recalled = _call(codex, 2, "recall", {"query": "interview incentive"})

    assert recalled["structuredContent"]["memories"][0]["id"] == memory_id
    assert "$25" in recalled["content"][0]["text"]


def test_initialize_and_tool_contract(tmp_path):
    server = MCPServer(MemoryStore(tmp_path / "memory.sqlite3"))
    initialized = server.handle(
        {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": {"protocolVersion": "2025-06-18"},
        }
    )
    listed = server.handle({"jsonrpc": "2.0", "id": 2, "method": "tools/list"})
    assert initialized["result"]["serverInfo"]["name"] == "Lians Memory"
    assert {tool["name"] for tool in listed["result"]["tools"]} == {
        "remember",
        "recall",
        "list_memories",
        "correct_memory",
        "forget_memory",
    }

    unsupported = server.handle(
        {
            "jsonrpc": "2.0",
            "id": 3,
            "method": "initialize",
            "params": {"protocolVersion": "2000-01-01"},
        }
    )
    assert unsupported["result"]["protocolVersion"] == "2025-06-18"

    definitions = {tool["name"]: tool for tool in listed["result"]["tools"]}
    assert definitions["remember"]["inputSchema"]["properties"]["content"]["maxLength"] == 20_000
    assert (
        definitions["correct_memory"]["inputSchema"]["properties"]["content"]["maxLength"]
        == 20_000
    )
