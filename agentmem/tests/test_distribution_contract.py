import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def _json(relative_path: str) -> dict:
    return json.loads((ROOT / relative_path).read_text(encoding="utf-8"))


def test_gemini_starter_profile_is_bounded_and_requires_confirmation():
    server = _json("integrations/gemini/settings.example.json")["mcpServers"]["lians"]

    assert server["command"] == "uvx"
    assert server["args"] == ["--from", "lians-sdk[mcp]", "lians-mcp"]
    assert server["includeTools"] == [
        "remember", "recall", "list_memories", "correct_memory", "forget_memory"
    ]
    assert server["trust"] is False


def test_gemini_extension_uses_the_published_bounded_server():
    extension = _json("gemini-extension.json")
    server = extension["mcpServers"]["lians"]

    assert extension["version"] == "0.5.1"
    assert server["command"] == "uvx"
    assert server["args"] == ["--from", "lians-sdk[mcp]==0.5.0", "lians-mcp"]
    assert server["includeTools"] == ["remember", "recall"]
    assert server["timeout"] == 300000


def test_cursor_starter_profile_is_local_bounded_stdio():
    server = _json("integrations/cursor/mcp.example.json")["mcpServers"]["lians"]

    assert server["command"] == "uvx"
    assert server["args"] == ["--from", "lians-sdk[mcp]", "lians-mcp"]
    assert server["env"] == {
        "LIANS_MCP_ENABLED_TOOLS": (
            "remember,recall,list_memories,correct_memory,forget_memory"
        )
    }


def test_cursor_guide_documents_project_and_global_install_without_credentials():
    guide = (ROOT / "integrations/cursor/README.md").read_text(encoding="utf-8")

    assert ".cursor/mcp.json" in guide
    assert "~/.cursor/mcp.json" in guide
    assert "needs no Lians account or API key" in guide
    assert "LIANS_API_KEY=" not in guide


def test_codex_plugin_is_available_from_repository_marketplace():
    marketplace = _json(".agents/plugins/marketplace.json")
    plugin = marketplace["plugins"][0]

    assert marketplace["name"] == "lians"
    assert plugin["name"] == "lians-memory"
    assert plugin["source"] == {"source": "local", "path": "./plugins/lians-memory"}
    assert plugin["policy"]["installation"] == "AVAILABLE"


def test_claude_plugin_versions_match():
    marketplace = _json(".claude-plugin/marketplace.json")
    manifest = _json("integrations/lians-plugin/.claude-plugin/plugin.json")
    plugin = marketplace["plugins"][0]

    assert plugin["source"] == "./integrations/lians-plugin"
    assert plugin["version"] == manifest["version"]


def test_commercial_boundary_preserves_public_license_and_reserves_services():
    boundary = (ROOT / "docs/community-cloud-boundary.md").read_text(encoding="utf-8")

    assert "Code already released under Apache 2.0 remains available" in boundary
    assert "hosted continuity across supported clients and devices" in boundary
    assert "higher managed storage, write, recall, and operational limits" in boundary
    assert "does not include a production Lians-hosted tenant" in boundary
    assert "Codex, Cursor, Claude, Gemini" in boundary


def test_student_community_kit_uses_working_repository_asset_paths():
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    kit = (ROOT / "docs/student-community-kit.md").read_text(encoding="utf-8")

    assert "[student and community kit](docs/student-community-kit.md)" in readme
    assert 'src="assets/logo-blue.png"' in kit
    assert "[blue lotus logo](assets/logo-blue.png)" in kit
    assert (ROOT / "docs/assets/logo-blue.png").is_file()
