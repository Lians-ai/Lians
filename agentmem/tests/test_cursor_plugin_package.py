from __future__ import annotations

import base64
import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
MARKETPLACE_PATH = ROOT / ".cursor-plugin" / "marketplace.json"
PLUGIN_ROOT = ROOT / "integrations" / "cursor"
PLUGIN_MANIFEST_PATH = PLUGIN_ROOT / ".cursor-plugin" / "plugin.json"
MCP_PATH = PLUGIN_ROOT / "mcp.json"
MCP_EXAMPLE_PATH = PLUGIN_ROOT / "mcp.example.json"
README_PATH = PLUGIN_ROOT / "README.md"
SKILL_PATH = PLUGIN_ROOT / "skills" / "lians-memory" / "SKILL.md"


def _json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def test_cursor_marketplace_points_to_real_plugin() -> None:
    marketplace = _json(MARKETPLACE_PATH)
    assert marketplace["name"] == "lians"
    assert marketplace["owner"]["name"] == "Lians"

    entries = marketplace["plugins"]
    assert len(entries) == 1
    entry = entries[0]
    assert entry["name"] == "lians-memory"
    assert entry["source"] == "./integrations/cursor"
    assert (ROOT / entry["source"]).resolve() == PLUGIN_ROOT.resolve()


def test_cursor_plugin_manifest_and_references_are_valid() -> None:
    manifest = _json(PLUGIN_MANIFEST_PATH)
    assert manifest["name"] == "lians-memory"
    assert re.fullmatch(r"[a-z0-9](?:[a-z0-9.-]*[a-z0-9])?", manifest["name"])
    assert manifest["license"] == "Apache-2.0"
    assert re.fullmatch(r"\d+\.\d+\.\d+", manifest["version"])
    assert (PLUGIN_ROOT / manifest["logo"]).is_file()

    skill = SKILL_PATH.read_text(encoding="utf-8")
    assert skill.startswith("---\n")
    frontmatter = skill.split("---\n", 2)[1]
    assert re.search(r"^name:\s+lians-memory$", frontmatter, re.MULTILINE)
    assert re.search(r"^description:\s+\S", frontmatter, re.MULTILINE)


def test_cursor_mcp_uses_immutable_encrypted_bridge_runtime() -> None:
    config = _json(MCP_PATH)
    assert set(config["mcpServers"]) == {"lians-memory"}
    server = config["mcpServers"]["lians-memory"]
    assert server["command"] == "uvx"
    assert server["args"][0] == "--from"
    assert re.fullmatch(
        r"lians-easy @ https://github\.com/Lians-ai/Lians/archive/[0-9a-f]{40}\.zip"
        r"#subdirectory=packages/lians-easy",
        server["args"][1],
    )
    assert server["args"][2:] == [
        "lians-easy",
        "mcp",
    ]
    assert "437a9f5038434572f1ed016434f8878328c8c499" in server["args"][1]
    assert "env" not in server


def test_cursor_install_routes_cannot_drift_from_plugin_config() -> None:
    plugin_server = _json(MCP_PATH)["mcpServers"]["lians-memory"]
    example_server = _json(MCP_EXAMPLE_PATH)["mcpServers"]["lians-memory"]
    assert example_server == plugin_server

    readme = README_PATH.read_text(encoding="utf-8")
    match = re.search(r"install-mcp\?name=lians-memory&config=([A-Za-z0-9+/=]+)", readme)
    assert match is not None
    deeplink_server = json.loads(base64.b64decode(match.group(1)).decode("utf-8"))
    assert deeplink_server == plugin_server

    assert "AES-GCM encrypted at rest" in readme
    assert "DPAPI protects the local root key" in readme
    assert "SQLite plaintext" not in readme
    assert "[PR #170](https://github.com/Lians-ai/Lians/pull/170)" in readme
    assert "advance the immutable pin" in readme
