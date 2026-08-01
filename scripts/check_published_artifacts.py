"""Verify the public Lians release matrix against machine-readable registries."""

from __future__ import annotations

import argparse
import json
import re
import sys
import urllib.request
import xml.etree.ElementTree as ET
from collections.abc import Callable
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_STATUS = ROOT / "docs" / "published-release-status.json"
SEMVER = re.compile(r"^\d+\.\d+\.\d+(?:[-+][0-9A-Za-z.-]+)?$")
USER_AGENT = "lians-release-verifier/1"


def load_status(path: Path = DEFAULT_STATUS) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def validate_status(status: dict[str, Any], source_version: str) -> list[str]:
    errors: list[str] = []
    if status.get("schema") != "lians.published-release-status.v1":
        errors.append("unsupported or missing status schema")
    if status.get("source_version") != source_version:
        errors.append(
            f"status source_version={status.get('source_version')!r} does not match "
            f"VERSION={source_version!r}"
        )
    production = status.get("production_api")
    if not isinstance(production, dict) or not SEMVER.fullmatch(str(production.get("version", ""))):
        errors.append("production_api.version must be semver")
    elif production.get("verification_url") != (
        str(production.get("url", "")).rstrip("/") + "/version"
    ):
        errors.append("production_api.verification_url must use the public /version endpoint")
    artifacts = status.get("artifacts")
    if not isinstance(artifacts, dict):
        return [*errors, "artifacts must be an object"]
    required = {
        "pypi",
        "npm",
        "go",
        "maven",
        "c",
        "ghcr_mcp",
        "mcp_registry",
        "github_release",
    }
    missing = sorted(required - artifacts.keys())
    if missing:
        errors.append(f"missing artifacts: {', '.join(missing)}")
    for name, artifact in artifacts.items():
        if not isinstance(artifact, dict):
            errors.append(f"artifacts.{name} must be an object")
            continue
        if not SEMVER.fullmatch(str(artifact.get("version", ""))):
            errors.append(f"artifacts.{name}.version must be semver")
        if not str(artifact.get("url", "")).startswith("https://"):
            errors.append(f"artifacts.{name}.url must use https")
    if (
        isinstance(artifacts.get("c"), dict)
        and isinstance(artifacts.get("github_release"), dict)
        and artifacts["c"].get("version") != artifacts["github_release"].get("version")
    ):
        errors.append("C source asset version must match the GitHub release")
    return errors


def source_sync_drift(status: dict[str, Any]) -> dict[str, str]:
    source = str(status["source_version"])
    drift = {
        name: str(artifact["version"])
        for name, artifact in status["artifacts"].items()
        if artifact["version"] != source
    }
    production_version = str(status["production_api"]["version"])
    if production_version != source:
        drift["production_api"] = production_version
    return drift


def _json_path(payload: bytes, *path: str) -> str:
    value: Any = json.loads(payload)
    for part in path:
        value = value[part]
    return str(value)


def parse_go_versions(payload: bytes) -> str:
    versions = [line.strip().removeprefix("v") for line in payload.decode().splitlines()]
    stable = [version for version in versions if re.fullmatch(r"\d+\.\d+\.\d+", version)]
    if not stable:
        raise ValueError("Go proxy returned no semver releases")
    return max(stable, key=lambda value: tuple(map(int, value.split("."))))


def parse_maven_metadata(payload: bytes) -> str:
    root = ET.fromstring(payload)
    release = root.findtext("./versioning/release")
    if not release:
        raise ValueError("Maven metadata has no release version")
    return release


REGISTRIES: dict[str, tuple[str, Callable[[bytes], str]]] = {
    "production_api": (
        "https://agentmem-lotus.fly.dev/version",
        lambda payload: _json_path(payload, "version"),
    ),
    "pypi": (
        "https://pypi.org/pypi/lians-sdk/json",
        lambda payload: _json_path(payload, "info", "version"),
    ),
    "npm": (
        "https://registry.npmjs.org/@lians-ai%2Flians/latest",
        lambda payload: _json_path(payload, "version"),
    ),
    "go": (
        "https://proxy.golang.org/github.com/%21lians-ai/%21lians/agentmem/sdk/go/@v/list",
        parse_go_versions,
    ),
    "maven": (
        "https://repo1.maven.org/maven2/ai/lians/lians-sdk/maven-metadata.xml",
        parse_maven_metadata,
    ),
    "mcp_registry": (
        "https://registry.modelcontextprotocol.io/v0/servers/io.github.ebeirne%2Flians/versions/latest",
        lambda payload: _json_path(payload, "server", "version"),
    ),
    "github_release": (
        "https://api.github.com/repos/Lians-ai/Lians/releases/latest",
        lambda payload: _json_path(payload, "tag_name").removeprefix("v"),
    ),
}


def fetch(url: str, timeout: float = 20.0) -> bytes:
    request = urllib.request.Request(
        url,
        headers={
            "Accept": "application/json, application/xml, text/plain",
            "User-Agent": USER_AGENT,
        },
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return response.read()


def discover_versions(
    fetcher: Callable[[str], bytes] = fetch,
) -> dict[str, str]:
    return {name: parser(fetcher(url)) for name, (url, parser) in REGISTRIES.items()}


def compare_live(status: dict[str, Any], live: dict[str, str]) -> dict[str, tuple[str, str]]:
    expected = {
        "production_api": str(status["production_api"]["version"]),
        **{
            name: str(artifact["version"])
            for name, artifact in status["artifacts"].items()
            if name != "c"
        },
    }
    return {
        name: (expected[name], actual)
        for name, actual in live.items()
        if expected.get(name) != actual
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--status", type=Path, default=DEFAULT_STATUS)
    parser.add_argument(
        "--require-source-sync",
        action="store_true",
        help="also fail unless every published surface matches VERSION",
    )
    args = parser.parse_args(argv)

    source_version = (ROOT / "VERSION").read_text(encoding="utf-8").strip()
    status = load_status(args.status)
    errors = validate_status(status, source_version)
    if errors:
        for error in errors:
            print(f"ERROR: {error}", file=sys.stderr)
        return 1

    try:
        live = discover_versions()
    except Exception as exc:  # noqa: BLE001 - preserve a concise CLI boundary
        print(f"ERROR: registry verification failed: {exc}", file=sys.stderr)
        return 1

    mismatches = compare_live(status, live)
    if mismatches:
        for name, (expected, actual) in sorted(mismatches.items()):
            print(f"ERROR: {name}: status={expected}, live={actual}", file=sys.stderr)
        return 1

    print("Published release status matches every configured live registry check.")
    for name, version in sorted(live.items()):
        print(f"  {name}: {version}")

    if args.require_source_sync:
        drift = source_sync_drift(status)
        if drift:
            print(
                f"ERROR: source is {source_version}, but these surfaces differ:",
                file=sys.stderr,
            )
            for name, version in sorted(drift.items()):
                print(f"  {name}: {version}", file=sys.stderr)
            return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
