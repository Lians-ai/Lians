"""Conservative desktop-update discovery for Lians Bridge.

Discovery never downloads or executes a package. It offers an update only when
the official stable GitHub release contains both the exact package for this
device and its checksum. Operating-system publisher verification remains the
final trust gate when the user installs that package.
"""

from __future__ import annotations

import json
import platform
import re
from collections.abc import Callable
from typing import Any
from urllib.parse import urlparse
from urllib.request import Request, urlopen

from . import __version__

RELEASE_API = "https://api.github.com/repos/Lians-ai/Lians/releases/latest"
MAX_RELEASE_BYTES = 1_000_000
REQUEST_TIMEOUT_SECONDS = 5
_VERSION = re.compile(r"^(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)$")


def _version(value: str) -> tuple[int, int, int]:
    match = _VERSION.fullmatch(value)
    if match is None:
        raise ValueError("Lians releases must use a stable X.Y.Z version")
    return int(match.group(1)), int(match.group(2)), int(match.group(3))


def _package_name(*, version: str, system: str, machine: str) -> str | None:
    normalized_system = system.casefold()
    normalized_machine = machine.casefold()
    if normalized_system == "windows" and normalized_machine in {"amd64", "x86_64"}:
        return f"Lians-Setup-{version}.exe"
    if normalized_system == "darwin":
        if normalized_machine in {"arm64", "aarch64"}:
            return f"Lians-{version}-macos-arm64.dmg"
        if normalized_machine in {"amd64", "x86_64"}:
            return f"Lians-{version}-macos-x86_64.dmg"
    if normalized_system == "linux" and normalized_machine in {"amd64", "x86_64"}:
        return f"Lians-{version}-linux-x86_64.AppImage"
    return None


def _official_url(value: Any, *, expected_path: str) -> str:
    if not isinstance(value, str):
        raise TypeError("The Lians release URL is missing")
    parsed = urlparse(value)
    if (
        parsed.scheme != "https"
        or parsed.hostname != "github.com"
        or parsed.port is not None
        or parsed.path != expected_path
        or parsed.params
        or parsed.query
        or parsed.fragment
    ):
        raise ValueError("The Lians release URL is not an official GitHub URL")
    return value


def evaluate_release(
    document: dict[str, Any],
    *,
    current_version: str = __version__,
    system: str | None = None,
    machine: str | None = None,
) -> dict[str, Any]:
    """Validate GitHub release metadata and choose the exact consumer package."""
    current = _version(current_version)
    system = system or platform.system()
    machine = machine or platform.machine()

    tag = document.get("tag_name")
    if not isinstance(tag, str) or not tag.startswith("v"):
        raise ValueError("The latest Lians release has an invalid tag")
    available_version = tag[1:]
    available = _version(available_version)
    release_url = _official_url(
        document.get("html_url"),
        expected_path=f"/Lians-ai/Lians/releases/tag/v{available_version}",
    )
    if document.get("draft") is True or document.get("prerelease") is True:
        raise ValueError("The latest Lians release is not a stable release")

    result: dict[str, Any] = {
        "current_version": current_version,
        "available_version": available_version,
        "release_url": release_url,
    }
    if available <= current:
        return {**result, "status": "up_to_date"}

    package_name = _package_name(
        version=available_version,
        system=system,
        machine=machine,
    )
    if package_name is None:
        return {
            **result,
            "status": "unsupported",
            "message": "Automatic desktop updates are not available for this device yet.",
        }

    assets = document.get("assets")
    if not isinstance(assets, list):
        raise TypeError("The latest Lians release has invalid assets")
    by_name = {
        asset.get("name"): asset
        for asset in assets
        if isinstance(asset, dict) and isinstance(asset.get("name"), str)
    }
    checksum_name = f"{package_name}.sha256"
    if package_name not in by_name or checksum_name not in by_name:
        return {
            **result,
            "status": "not_published",
            "message": "A newer Lians release exists, but no trusted desktop update is published for this device.",
        }

    download_url = _official_url(
        by_name[package_name].get("browser_download_url"),
        expected_path=f"/Lians-ai/Lians/releases/download/v{available_version}/{package_name}",
    )
    _official_url(
        by_name[checksum_name].get("browser_download_url"),
        expected_path=f"/Lians-ai/Lians/releases/download/v{available_version}/{checksum_name}",
    )
    return {
        **result,
        "status": "available",
        "package_name": package_name,
        "download_url": download_url,
        "checksum_published": True,
        "message": "A signed Lians desktop update is ready for this device.",
    }


def check_for_update(
    *,
    current_version: str = __version__,
    system: str | None = None,
    machine: str | None = None,
    opener: Callable[..., Any] = urlopen,
) -> dict[str, Any]:
    """Fetch and validate the latest stable release with strict size bounds."""
    request = Request(
        RELEASE_API,
        headers={
            "Accept": "application/vnd.github+json",
            "User-Agent": f"Lians-Bridge/{current_version}",
            "X-GitHub-Api-Version": "2022-11-28",
        },
    )
    with opener(request, timeout=REQUEST_TIMEOUT_SECONDS) as response:
        declared = response.headers.get("Content-Length")
        if declared is not None and int(declared) > MAX_RELEASE_BYTES:
            raise ValueError("The Lians release response is too large")
        payload = response.read(MAX_RELEASE_BYTES + 1)
    if len(payload) > MAX_RELEASE_BYTES:
        raise ValueError("The Lians release response is too large")
    document = json.loads(payload)
    if not isinstance(document, dict):
        raise TypeError("The Lians release response is invalid")
    return evaluate_release(
        document,
        current_version=current_version,
        system=system,
        machine=machine,
    )
