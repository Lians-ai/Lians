"""Select the newest older signed-desktop candidate from GitHub release JSON.

The script is deliberately network-free. Release workflows fetch the catalog
with their authenticated GitHub token, then pass the saved JSON here so the
selection policy can be unit tested without contacting GitHub.
"""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path
from typing import Any

STABLE_TAG = re.compile(
    r"^v(?P<major>0|[1-9][0-9]*)\.(?P<minor>0|[1-9][0-9]*)\.(?P<patch>0|[1-9][0-9]*)$"
)


def _version(tag: str) -> tuple[int, int, int]:
    match = STABLE_TAG.fullmatch(tag)
    if match is None:
        raise ValueError(f"Expected a stable vX.Y.Z tag, received {tag!r}")
    return tuple(int(match.group(name)) for name in ("major", "minor", "patch"))


def _releases(document: Any) -> list[dict[str, Any]]:
    if not isinstance(document, list):
        raise TypeError("GitHub release catalog must be a JSON array")
    flattened: list[Any] = []
    for item in document:
        if isinstance(item, list):
            flattened.extend(item)
        else:
            flattened.append(item)
    if not all(isinstance(item, dict) for item in flattened):
        raise TypeError("GitHub release catalog contains a non-object release")
    return flattened


def _asset_pair(
    release: dict[str, Any], *, version: str, platform: str, architecture: str | None
) -> tuple[dict[str, Any], dict[str, Any]] | None:
    if platform == "windows":
        artifact_name = f"Lians-Setup-{version}.exe"
    elif platform == "macos":
        if architecture not in {"arm64", "x86_64"}:
            raise ValueError("macOS baseline selection requires arm64 or x86_64")
        artifact_name = f"Lians-{version}-macos-{architecture}.dmg"
    else:
        raise ValueError("platform must be windows or macos")
    checksum_name = f"{artifact_name}.sha256"
    assets = release.get("assets", [])
    if not isinstance(assets, list):
        raise TypeError("GitHub release assets must be a JSON array")
    artifacts = [
        asset for asset in assets if isinstance(asset, dict) and asset.get("name") == artifact_name
    ]
    checksums = [
        asset for asset in assets if isinstance(asset, dict) and asset.get("name") == checksum_name
    ]
    if len(artifacts) != 1 or len(checksums) != 1:
        return None
    return artifacts[0], checksums[0]


def select_baseline(
    document: Any,
    *,
    current_tag: str,
    platform: str,
    architecture: str | None = None,
) -> dict[str, Any]:
    """Return the newest older stable release with an exact artifact/checksum pair."""

    current = _version(current_tag)
    candidates: list[tuple[tuple[int, int, int], dict[str, Any]]] = []
    for release in _releases(document):
        if release.get("draft") is True or release.get("prerelease") is True:
            continue
        tag = release.get("tag_name")
        if not isinstance(tag, str) or STABLE_TAG.fullmatch(tag) is None:
            continue
        candidate_version = _version(tag)
        if candidate_version >= current:
            continue
        version_text = tag.removeprefix("v")
        pair = _asset_pair(
            release,
            version=version_text,
            platform=platform,
            architecture=architecture,
        )
        if pair is None:
            continue
        artifact, checksum = pair
        candidates.append(
            (
                candidate_version,
                {
                    "status": "selected",
                    "tag": tag,
                    "version": version_text,
                    "artifact_name": artifact["name"],
                    "checksum_name": checksum["name"],
                },
            )
        )
    if not candidates:
        return {
            "status": "none",
            "current_tag": current_tag,
            "platform": platform,
            "architecture": architecture,
        }
    return max(candidates, key=lambda item: item[0])[1]


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--catalog", required=True, type=Path)
    parser.add_argument("--current-tag", required=True)
    parser.add_argument("--platform", required=True, choices=("windows", "macos"))
    parser.add_argument("--architecture", choices=("arm64", "x86_64"))
    arguments = parser.parse_args()
    document = json.loads(arguments.catalog.read_text(encoding="utf-8"))
    result = select_baseline(
        document,
        current_tag=arguments.current_tag,
        platform=arguments.platform,
        architecture=arguments.architecture,
    )
    print(json.dumps(result, sort_keys=True))


if __name__ == "__main__":
    main()
