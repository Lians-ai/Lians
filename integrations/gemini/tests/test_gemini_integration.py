from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path


INTEGRATION_ROOT = Path(__file__).resolve().parents[1]
REPOSITORY_ROOT = INTEGRATION_ROOT.parents[1]
SDK_ROOT = REPOSITORY_ROOT / "agentmem" / "sdk" / "python"
CORE_TOOLS = ["remember", "recall", "recall_at"]


def _settings(name: str) -> dict:
    return json.loads((INTEGRATION_ROOT / name).read_text(encoding="utf-8"))


def test_settings_examples_are_minimal_safe_core_profiles() -> None:
    for name in ("settings.example.json", "settings.local.example.json"):
        document = _settings(name)
        assert document["$schema"].endswith("/schemas/settings.schema.json")
        assert set(document["mcpServers"]) == {"lians"}

        server = document["mcpServers"]["lians"]
        assert server["command"] == "uvx"
        assert server["args"] == ["--from", "lians-sdk[mcp]", "lians-mcp"]
        assert server["cwd"] == "."
        assert server["includeTools"] == CORE_TOOLS
        assert server["env"]["LIANS_MCP_PROJECT_ROOT"] == "."
        assert server["env"]["LIANS_AGENT_ID"] == "$LIANS_AGENT_ID"
        assert server["env"]["LIANS_NAMESPACE"] == "$LIANS_NAMESPACE"
        assert server["env"]["LIANS_MCP_ENABLED_TOOLS"] == ",".join(CORE_TOOLS)
        assert server["env"]["LIANS_MCP_RECALL_K"] == "50"
        assert server["env"]["LIANS_MCP_CONTEXT_MAX_TOKENS"] == "2650"
        assert server["env"]["LIANS_MCP_PREWARM"] == "background"
        assert not any(":-" in value for value in server["env"].values())
        assert server["timeout"] >= 300_000
        assert server["trust"] is False


def test_managed_profile_references_key_without_embedding_it() -> None:
    server = _settings("settings.example.json")["mcpServers"]["lians"]
    assert server["env"]["LIANS_API_KEY"] == "$LIANS_API_KEY"
    assert server["env"]["LIANS_URL"] == "https://api.lians.dev"
    assert not any("lians_" in value for value in server["env"].values())


def test_local_profile_cannot_accidentally_route_to_cloud() -> None:
    environment = _settings("settings.local.example.json")["mcpServers"]["lians"]["env"]
    assert environment["LIANS_URL"] == ""
    assert environment["LIANS_API_KEY"] == ""
    assert environment["EMBEDDING_PROVIDER"] == "sentence-transformers"


def test_memory_policy_is_bounded_and_uses_gemini_fully_qualified_names() -> None:
    policy = (INTEGRATION_ROOT / "GEMINI.md").read_text(encoding="utf-8")
    for name in CORE_TOOLS:
        assert f"mcp_lians_{name}" in policy
    assert "`k: 50`" in policy
    assert "`max_tokens: 2650`" in policy
    assert "never fetch the whole history" in policy
    assert "Do not call Lians for a self-contained prompt" in policy


def test_configured_core_tools_exist_and_have_safe_mcp_annotations() -> None:
    python = (
        SDK_ROOT / ".venv" / ("Scripts/python.exe" if sys.platform == "win32" else "bin/python")
    )
    if not python.exists():
        python = Path(sys.executable)

    probe = subprocess.run(
        [
            str(python),
            "-c",
            """
import asyncio
import json
from lians import mcp_server

server = mcp_server._build_server()
handler = next(
    callback
    for request_type, callback in server.request_handlers.items()
    if request_type.__name__ == "ListToolsRequest"
)
result = asyncio.run(handler(type("Request", (), {"params": None})()))
print(json.dumps({
    tool.name: {
        "read_only": tool.annotations.readOnlyHint,
        "destructive": tool.annotations.destructiveHint,
        "idempotent": tool.annotations.idempotentHint,
    }
    for tool in result.root.tools
}))
""",
        ],
        cwd=SDK_ROOT,
        check=True,
        capture_output=True,
        text=True,
    )

    tools = json.loads(probe.stdout)
    assert set(CORE_TOOLS) <= set(tools)
    assert tools["remember"]["read_only"] is False
    for name in ("recall", "recall_at"):
        assert tools[name]["read_only"] is True
        assert tools[name]["destructive"] is False
        assert tools[name]["idempotent"] is True
