"""Fail when a Lians source release-candidate manifest drifts from VERSION."""

from __future__ import annotations

import json
import re
import sys
import tomllib
import xml.etree.ElementTree as ET
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
EXPECTED = (ROOT / "VERSION").read_text(encoding="utf-8").strip()


def _toml(path: str) -> dict:
    with (ROOT / path).open("rb") as handle:
        return tomllib.load(handle)


def _match(path: str, pattern: str) -> str:
    text = (ROOT / path).read_text(encoding="utf-8")
    match = re.search(pattern, text)
    if not match:
        raise RuntimeError(f"Could not find version in {path}")
    return match.group(1)


def _exact_dependency_version(path: str, package: str) -> str:
    dependencies = _toml(path)["project"].get("dependencies", [])
    pattern = re.compile(
        rf"{re.escape(package)}(?:\[[^\]]+\])?==([0-9A-Za-z.+-]+)"
    )
    for requirement in dependencies:
        if requirement.startswith(package):
            match = pattern.fullmatch(requirement)
            return match.group(1) if match else f"not exact: {requirement}"
    return "missing"


def _locked_package_version(path: str, package: str) -> str:
    lock = _toml(path)
    matches = [
        entry.get("version", "missing")
        for entry in lock.get("package", [])
        if entry.get("name") == package
    ]
    return matches[0] if len(matches) == 1 else "missing or ambiguous"


def _java_version() -> str:
    root = ET.parse(ROOT / "agentmem/sdk/java/pom.xml").getroot()
    namespace = {"m": "http://maven.apache.org/POM/4.0.0"}
    value = root.findtext("m:version", namespaces=namespace)
    if not value:
        raise RuntimeError("Could not find project version in Java pom.xml")
    return value


def main() -> int:
    server_manifest = json.loads((ROOT / "server.json").read_text(encoding="utf-8"))
    mcpb_project = _toml("integrations/mcpb/pyproject.toml")
    mcpb_manifest = json.loads(
        (ROOT / "integrations/mcpb/manifest.json").read_text(encoding="utf-8")
    )
    plugin_manifest = json.loads(
        (ROOT / "integrations/lians-plugin/.claude-plugin/plugin.json").read_text(
            encoding="utf-8"
        )
    )
    plugin_marketplace = json.loads(
        (ROOT / ".claude-plugin/marketplace.json").read_text(encoding="utf-8")
    )
    package_json = json.loads(
        (ROOT / "agentmem/sdk/typescript/package.json").read_text(encoding="utf-8")
    )
    package_lock = json.loads(
        (ROOT / "agentmem/sdk/typescript/package-lock.json").read_text(encoding="utf-8")
    )
    versions = {
        "root Python package": _toml("pyproject.toml")["project"]["version"],
        "Python SDK": _toml("agentmem/sdk/python/pyproject.toml")["project"]["version"],
        "Lians Easy": _toml("packages/lians-easy/pyproject.toml")["project"]["version"],
        "Python runtime": _match(
            "agentmem/src/lians/__init__.py", r'__version__\s*=\s*"([^"]+)"'
        ),
        "FastAPI contract": _match(
            "agentmem/src/lians/main.py", r'app\s*=\s*FastAPI\([\s\S]*?version="([^"]+)"'
        ),
        "TypeScript SDK": package_json["version"],
        "TypeScript lock": package_lock["version"],
        "TypeScript lock root": package_lock["packages"][""]["version"],
        "Java SDK": _java_version(),
        "C SDK": _match(
            "agentmem/sdk/c/CMakeLists.txt",
            r"project\(lians_c_sdk VERSION ([^\s]+)",
        ),
        "C user agent": _match(
            "agentmem/sdk/c/src/lians.c", r'lians-c-sdk/([^"]+)"'
        ),
        "Go SDK": _match(
            "agentmem/sdk/go/version.go", r'const Version = "([^"]+)"'
        ),
        "MCP Registry server": server_manifest["version"],
        "MCP Registry Python package": server_manifest["packages"][0]["version"],
        "MCPB manifest": mcpb_manifest["version"],
        "MCPB project": mcpb_project["project"]["version"],
        "MCPB runtime pin": _exact_dependency_version(
            "integrations/mcpb/pyproject.toml", "lians-sdk"
        ),
        "Claude plugin": plugin_manifest["version"],
        "Claude plugin marketplace": plugin_marketplace["plugins"][0]["version"],
    }
    mcpb_lock = ROOT / "integrations/mcpb/uv.lock"
    if mcpb_lock.exists():
        versions["MCPB lock project"] = _locked_package_version(
            "integrations/mcpb/uv.lock", "lians-agent-memory-mcpb"
        )
        versions["MCPB lock runtime"] = _locked_package_version(
            "integrations/mcpb/uv.lock", "lians-sdk"
        )
    drift = {name: version for name, version in versions.items() if version != EXPECTED}
    if drift:
        print(f"Release version must be {EXPECTED}. Drift detected:", file=sys.stderr)
        for name, version in drift.items():
            print(f"  {name}: {version}", file=sys.stderr)
        return 1
    print(
        f"Lians source release contract is synchronized at {EXPECTED}. "
        "Public registries are verified separately."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
