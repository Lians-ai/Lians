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
        "set_current",
        "recall",
        "understand_request",
        "list_memories",
        "memory_health",
        "memory_history",
        "memory_at",
        "track_dependencies",
        "state_impact",
        "state_repair_brief",
        "resolve_state_impact",
        "start_task",
        "checkpoint_task",
        "task_status",
        "task_context",
        "continue_work",
        "configure_verification",
        "verify_work",
        "verification_status",
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
    assert unsupported["result"]["protocolVersion"] == "2025-11-25"

    latest_legacy = server.handle(
        {
            "jsonrpc": "2.0",
            "id": 4,
            "method": "initialize",
            "params": {"protocolVersion": "2025-11-25"},
        }
    )
    assert latest_legacy["result"]["protocolVersion"] == "2025-11-25"

    definitions = {tool["name"]: tool for tool in listed["result"]["tools"]}
    assert definitions["remember"]["inputSchema"]["properties"]["content"]["maxLength"] == 20_000
    assert (
        definitions["correct_memory"]["inputSchema"]["properties"]["content"]["maxLength"] == 20_000
    )


def test_modern_mcp_discovery_and_stateless_tool_calls(tmp_path):
    server = MCPServer(MemoryStore(tmp_path / "memory.sqlite3"))
    metadata = {
        "io.modelcontextprotocol/protocolVersion": "2026-07-28",
        "io.modelcontextprotocol/clientInfo": {"name": "test-client", "version": "1.0"},
        "io.modelcontextprotocol/clientCapabilities": {},
    }

    discovered = server.handle(
        {
            "jsonrpc": "2.0",
            "id": "discover",
            "method": "server/discover",
            "params": {"_meta": metadata},
        }
    )
    assert discovered["result"]["supportedVersions"] == ["2026-07-28"]
    assert discovered["result"]["resultType"] == "complete"
    assert discovered["result"]["cacheScope"] == "private"
    assert discovered["result"]["_meta"]["io.modelcontextprotocol/serverInfo"]["name"] == (
        "Lians Memory"
    )

    listed = server.handle(
        {
            "jsonrpc": "2.0",
            "id": "list",
            "method": "tools/list",
            "params": {"_meta": metadata},
        }
    )
    assert listed["result"]["resultType"] == "complete"
    assert listed["result"]["ttlMs"] == 300_000
    assert any(tool["name"] == "continue_work" for tool in listed["result"]["tools"])

    remembered = server.handle(
        {
            "jsonrpc": "2.0",
            "id": "remember",
            "method": "tools/call",
            "params": {
                "name": "remember",
                "arguments": {"content": "Modern MCP state survives stateless calls."},
                "_meta": metadata,
            },
        }
    )
    assert remembered["result"]["resultType"] == "complete"
    assert remembered["result"]["structuredContent"]["content"].startswith("Modern MCP")


def test_understanding_and_memory_health_tools_are_local_and_read_only(tmp_path):
    server = MCPServer(MemoryStore(tmp_path / "memory.sqlite3"))
    server.store.remember(
        "The research covers public posts from August.",
        kind="project",
        scope="global",
    )

    understood = _call(
        server,
        1,
        "understand_request",
        {"request": "Research this"},
    )
    health = _call(server, 2, "memory_health", {})

    assert understood["structuredContent"]["brief"]["intent"] == "research"
    assert understood["structuredContent"]["brief"]["privacy"]["external_model_called"] is False
    assert health["structuredContent"]["mutated"] is False

def test_task_contract_moves_between_mcp_clients_and_gates_completion(tmp_path):
    database = tmp_path / "shared.sqlite3"
    claude = MCPServer(MemoryStore(database, profile="personal"))
    codex = MCPServer(MemoryStore(database, profile="personal"))
    project_root = str(tmp_path)

    started = _call(
        claude,
        1,
        "start_task",
        {
            "task_id": "cross-agent",
            "goal": "Publish a verified package",
            "success_criteria": ["The package launches", "The test suite passes"],
            "constraints": ["Keep credentials out of the package"],
            "project_root": project_root,
            "client": "claude",
        },
    )
    assert started["structuredContent"]["assessment"]["status"] == "active"

    checkpoint = _call(
        codex,
        2,
        "checkpoint_task",
        {
            "task_id": "cross-agent",
            "summary": "The launcher passed",
            "evidence": [
                {
                    "criterion_id": "criterion-1",
                    "evidence": "Exit code 0",
                    "trust_class": "measured_local",
                    "source": "launcher process",
                }
            ],
            "project_root": project_root,
            "client": "codex",
            "decisions": [
                {
                    "decision": "Ship the native companion first",
                    "reason": "It gives testers one place to resume",
                    "source": "founder review",
                }
            ],
            "open_questions": ["Who signs the Windows build?"],
        },
    )
    assert checkpoint["structuredContent"]["assessment"]["missing_criteria"] == [
        "criterion-1",
        "criterion-2",
    ]
    assert checkpoint["structuredContent"]["assessment"]["untrusted_criteria"] == [
        "criterion-1"
    ]

    context = _call(
        claude,
        3,
        "task_context",
        {"task_id": "cross-agent", "project_root": project_root, "client": "claude"},
    )
    assert "do not claim readiness" in context["content"][0]["text"]
    assert context["structuredContent"]["receipt"]["signature"]["algorithm"] == "Ed25519"

    continued = _call(
        codex,
        4,
        "continue_work",
        {"project_root": project_root, "client": "codex"},
    )
    assert continued["structuredContent"]["selection"] == "automatic"
    assert "Decisions:" in continued["content"][0]["text"]
    assert "Who signs the Windows build?" in continued["content"][0]["text"]
