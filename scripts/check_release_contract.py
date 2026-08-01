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


def _java_version() -> str:
    root = ET.parse(ROOT / "agentmem/sdk/java/pom.xml").getroot()
    namespace = {"m": "http://maven.apache.org/POM/4.0.0"}
    value = root.findtext("m:version", namespaces=namespace)
    if not value:
        raise RuntimeError("Could not find project version in Java pom.xml")
    return value


def main() -> int:
    package_json = json.loads(
        (ROOT / "agentmem/sdk/typescript/package.json").read_text(encoding="utf-8")
    )
    package_lock = json.loads(
        (ROOT / "agentmem/sdk/typescript/package-lock.json").read_text(encoding="utf-8")
    )
    versions = {
        "root Python package": _toml("pyproject.toml")["project"]["version"],
        "Python SDK": _toml("agentmem/sdk/python/pyproject.toml")["project"]["version"],
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
    }
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
