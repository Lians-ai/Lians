import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def _json(relative_path: str) -> dict:
    return json.loads((ROOT / relative_path).read_text(encoding="utf-8"))


def test_gemini_starter_profile_is_bounded_and_requires_confirmation():
    server = _json("integrations/gemini/settings.example.json")["mcpServers"]["lians"]

    assert server["command"] == "uvx"
    assert server["args"] == ["--from", "lians-sdk[mcp]", "lians-mcp"]
    assert server["includeTools"] == ["remember", "recall"]
    assert server["trust"] is False


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
