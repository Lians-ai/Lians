"""Conservative, user-initiated desktop updates for Lians Bridge.

Discovery never downloads or executes a package. A separate confirmed action
downloads the exact package and checksum from the official stable GitHub
release, verifies the complete file, and saves it without overwriting anything.
Opening that file is another confirmed action and fails closed unless the
operating system accepts the publisher identity used by the installed app.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import platform
import re
import shutil
import subprocess
import sys
import tempfile
from collections.abc import Callable
from pathlib import Path
from typing import Any
from urllib.parse import urlparse
from urllib.request import Request, urlopen

from . import __version__

RELEASE_API = "https://api.github.com/repos/Lians-ai/Lians/releases/latest"
MAX_RELEASE_BYTES = 1_000_000
MAX_CHECKSUM_BYTES = 4_096
MAX_PACKAGE_BYTES = 512 * 1024 * 1024
REQUEST_TIMEOUT_SECONDS = 5
DOWNLOAD_TIMEOUT_SECONDS = 30
DOWNLOAD_CHUNK_BYTES = 64 * 1024
_VERSION = re.compile(r"^(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)$")
_CHECKSUM = re.compile(r"^([0-9a-fA-F]{64})[ \t]+[*]?(.+)$")


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
    checksum_name = f"{package_name}.sha256"
    package_assets = [
        asset for asset in assets if isinstance(asset, dict) and asset.get("name") == package_name
    ]
    checksum_assets = [
        asset for asset in assets if isinstance(asset, dict) and asset.get("name") == checksum_name
    ]
    if not package_assets or not checksum_assets:
        return {
            **result,
            "status": "not_published",
            "message": "A newer Lians release exists, but no trusted desktop update is published for this device.",
        }
    if len(package_assets) != 1 or len(checksum_assets) != 1:
        raise ValueError("The latest Lians release contains duplicate desktop assets")

    download_url = _official_url(
        package_assets[0].get("browser_download_url"),
        expected_path=f"/Lians-ai/Lians/releases/download/v{available_version}/{package_name}",
    )
    checksum_url = _official_url(
        checksum_assets[0].get("browser_download_url"),
        expected_path=f"/Lians-ai/Lians/releases/download/v{available_version}/{checksum_name}",
    )
    return {
        **result,
        "status": "available",
        "package_name": package_name,
        "download_url": download_url,
        "checksum_url": checksum_url,
        "checksum_published": True,
        "message": "A verified Lians desktop update is ready for this device.",
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


def _validated_download_fields(
    release: dict[str, Any], *, system: str, machine: str
) -> tuple[str, str, str, str]:
    if release.get("status") != "available":
        raise ValueError("No trusted Lians update is ready to download")
    available_version = release.get("available_version")
    if not isinstance(available_version, str):
        raise TypeError("The Lians update version is missing")
    _version(available_version)
    expected_name = _package_name(
        version=available_version,
        system=system,
        machine=machine,
    )
    package_name = release.get("package_name")
    if expected_name is None or package_name != expected_name:
        raise ValueError("The Lians update does not match this device")
    download_url = _official_url(
        release.get("download_url"),
        expected_path=f"/Lians-ai/Lians/releases/download/v{available_version}/{package_name}",
    )
    checksum_name = f"{package_name}.sha256"
    checksum_url = _official_url(
        release.get("checksum_url"),
        expected_path=f"/Lians-ai/Lians/releases/download/v{available_version}/{checksum_name}",
    )
    return available_version, package_name, download_url, checksum_url


def _request(url: str, *, version: str) -> Request:
    return Request(
        url,
        headers={
            "Accept": "application/octet-stream",
            "User-Agent": f"Lians-Bridge/{version}",
        },
    )


def _declared_size(response: Any, *, maximum: int) -> None:
    declared = response.headers.get("Content-Length")
    if declared is not None:
        try:
            size = int(declared)
        except (TypeError, ValueError) as exc:
            raise ValueError("The Lians update has an invalid size") from exc
        if size < 0 or size > maximum:
            raise ValueError("The Lians update is too large")


def _read_checksum(
    url: str,
    *,
    package_name: str,
    version: str,
    opener: Callable[..., Any],
) -> str:
    with opener(
        _request(url, version=version), timeout=DOWNLOAD_TIMEOUT_SECONDS
    ) as response:
        _declared_size(response, maximum=MAX_CHECKSUM_BYTES)
        payload = response.read(MAX_CHECKSUM_BYTES + 1)
    if not payload or len(payload) > MAX_CHECKSUM_BYTES:
        raise ValueError("The Lians update checksum is empty or too large")
    try:
        lines = [line.strip() for line in payload.decode("utf-8-sig").splitlines() if line.strip()]
    except UnicodeDecodeError as exc:
        raise ValueError("The Lians update checksum is invalid") from exc
    if len(lines) != 1:
        raise ValueError("The Lians update checksum is ambiguous")
    match = _CHECKSUM.fullmatch(lines[0])
    if match is None or match.group(2) != package_name:
        raise ValueError("The Lians update checksum does not match this package")
    return match.group(1).casefold()


def _download_package(
    url: str,
    destination: Path,
    *,
    expected_sha256: str,
    version: str,
    opener: Callable[..., Any],
) -> None:
    digest = hashlib.sha256()
    total = 0
    with opener(
        _request(url, version=version), timeout=DOWNLOAD_TIMEOUT_SECONDS
    ) as response:
        _declared_size(response, maximum=MAX_PACKAGE_BYTES)
        with destination.open("wb") as handle:
            while True:
                chunk = response.read(DOWNLOAD_CHUNK_BYTES)
                if not chunk:
                    break
                total += len(chunk)
                if total > MAX_PACKAGE_BYTES:
                    raise ValueError("The Lians update is too large")
                handle.write(chunk)
                digest.update(chunk)
            handle.flush()
            os.fsync(handle.fileno())
    if total == 0:
        raise ValueError("The Lians update is empty")
    if not hmac.compare_digest(digest.hexdigest(), expected_sha256):
        raise ValueError("The Lians update failed checksum verification")


def _publish_without_overwrite(temporary: Path, directory: Path, package_name: str) -> Path:
    stem = Path(package_name).stem
    suffix = Path(package_name).suffix
    for number in range(1_000):
        name = package_name if number == 0 else f"{stem} ({number}){suffix}"
        candidate = directory / name
        try:
            os.link(temporary, candidate)
        except FileExistsError:
            continue
        except OSError:
            try:
                descriptor = os.open(candidate, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            except FileExistsError:
                continue
            try:
                with os.fdopen(descriptor, "wb") as target:
                    with temporary.open("rb") as source:
                        shutil.copyfileobj(source, target, length=DOWNLOAD_CHUNK_BYTES)
                    target.flush()
                    os.fsync(target.fileno())
            except Exception:
                candidate.unlink(missing_ok=True)
                raise
        temporary.unlink(missing_ok=True)
        return candidate
    raise FileExistsError("Too many Lians update files already exist in Downloads")


def _powershell_signature(path: Path, *, runner: Callable[..., Any]) -> dict[str, str] | None:
    powershell = shutil.which("powershell.exe") or shutil.which("powershell")
    if powershell is None:
        return None
    command = (
        "$signature=Get-AuthenticodeSignature -LiteralPath $args[0];"
        "[pscustomobject]@{Status=$signature.Status.ToString();"
        "Subject=$(if($signature.SignerCertificate){$signature.SignerCertificate.Subject}else{$null})}"
        "|ConvertTo-Json -Compress"
    )
    completed = runner(
        [powershell, "-NoProfile", "-NonInteractive", "-Command", command, str(path)],
        capture_output=True,
        text=True,
        timeout=15,
        check=False,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    if completed.returncode != 0:
        return None
    try:
        result = json.loads(completed.stdout)
    except (TypeError, json.JSONDecodeError):
        return None
    if not isinstance(result, dict):
        return None
    status = result.get("Status")
    subject = result.get("Subject")
    if not isinstance(status, str) or not isinstance(subject, str):
        return None
    return {"status": status, "subject": subject}


def _macos_team_id(path: Path, *, runner: Callable[..., Any]) -> str | None:
    completed = runner(
        ["/usr/bin/codesign", "-d", "--verbose=4", str(path)],
        capture_output=True,
        text=True,
        timeout=15,
        check=False,
    )
    if completed.returncode != 0:
        return None
    output = f"{completed.stdout}\n{completed.stderr}"
    for line in output.splitlines():
        if line.startswith("TeamIdentifier="):
            value = line.partition("=")[2].strip()
            return value or None
    return None


def inspect_platform_trust(
    path: str | Path,
    *,
    system: str | None = None,
    current_executable: str | Path | None = None,
    runner: Callable[..., Any] = subprocess.run,
) -> dict[str, Any]:
    """Fail closed unless the candidate matches the installed publisher."""
    candidate = Path(path)
    system = system or platform.system()
    executable = Path(current_executable or sys.executable)
    frozen = current_executable is not None or bool(getattr(sys, "frozen", False))

    if system.casefold() == "windows" and frozen:
        installed = _powershell_signature(executable, runner=runner)
        update = _powershell_signature(candidate, runner=runner)
        if (
            installed
            and update
            and installed["status"] == "Valid"
            and update["status"] == "Valid"
            and installed["subject"] == update["subject"]
        ):
            return {
                "trust": "publisher_verified",
                "can_open": True,
                "trust_message": "Checksum and Windows publisher verified.",
            }

    if system.casefold() == "darwin" and frozen:
        installed_team = _macos_team_id(executable, runner=runner)
        update_team = _macos_team_id(candidate, runner=runner)
        assessment = runner(
            [
                "/usr/sbin/spctl",
                "--assess",
                "--type",
                "open",
                "--context",
                "context:primary-signature",
                str(candidate),
            ],
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
        staple = runner(
            ["/usr/bin/xcrun", "stapler", "validate", str(candidate)],
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
        if (
            installed_team
            and update_team == installed_team
            and assessment.returncode == 0
            and staple.returncode == 0
        ):
            return {
                "trust": "publisher_verified",
                "can_open": True,
                "trust_message": "Checksum, Apple publisher, and notarization verified.",
            }

    return {
        "trust": "checksum_verified",
        "can_open": False,
        "trust_message": (
            "Checksum verified. Lians will show the file in Downloads so the operating system "
            "can perform its final safety check."
        ),
    }


def download_verified_update(
    release: dict[str, Any],
    *,
    destination_dir: str | Path | None = None,
    system: str | None = None,
    machine: str | None = None,
    opener: Callable[..., Any] = urlopen,
    trust_checker: Callable[[Path], dict[str, Any]] = inspect_platform_trust,
) -> dict[str, Any]:
    """Download and verify an update after an explicit user confirmation."""
    system = system or platform.system()
    machine = machine or platform.machine()
    available_version, package_name, download_url, checksum_url = _validated_download_fields(
        release,
        system=system,
        machine=machine,
    )
    expected_sha256 = _read_checksum(
        checksum_url,
        package_name=package_name,
        version=__version__,
        opener=opener,
    )
    directory = Path(destination_dir) if destination_dir is not None else Path.home() / "Downloads"
    directory.mkdir(parents=True, exist_ok=True)
    directory = directory.resolve()
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=".lians-update-", suffix=".part", dir=directory
    )
    os.close(descriptor)
    temporary = Path(temporary_name)
    try:
        _download_package(
            download_url,
            temporary,
            expected_sha256=expected_sha256,
            version=__version__,
            opener=opener,
        )
        published = _publish_without_overwrite(temporary, directory, package_name)
    finally:
        temporary.unlink(missing_ok=True)
    try:
        trust = trust_checker(published)
    except (OSError, RuntimeError, TypeError, ValueError, subprocess.SubprocessError):
        published.unlink(missing_ok=True)
        raise
    if not isinstance(trust, dict) or not isinstance(trust.get("can_open"), bool):
        published.unlink(missing_ok=True)
        raise TypeError("The Lians update publisher check returned an invalid result")
    return {
        "status": "downloaded",
        "available_version": available_version,
        "package_name": published.name,
        "original_package_name": package_name,
        "sha256": expected_sha256,
        "saved_location": "Downloads",
        "path": str(published),
        **trust,
    }


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(DOWNLOAD_CHUNK_BYTES):
            digest.update(chunk)
    return digest.hexdigest()


def open_prepared_update(
    prepared: dict[str, Any],
    *,
    system: str | None = None,
    trust_checker: Callable[[Path], dict[str, Any]] = inspect_platform_trust,
    launcher: Callable[[Path, bool, str], None] | None = None,
) -> dict[str, Any]:
    """Reverify a prepared file, then open it or reveal it after confirmation."""
    path_value = prepared.get("path")
    expected_sha256 = prepared.get("sha256")
    if not isinstance(path_value, str) or not isinstance(expected_sha256, str):
        raise TypeError("No verified Lians update is ready to open")
    path = Path(path_value)
    if (
        not path.is_file()
        or path.stat().st_size <= 0
        or path.stat().st_size > MAX_PACKAGE_BYTES
        or not hmac.compare_digest(_sha256(path), expected_sha256)
    ):
        raise ValueError("The downloaded Lians update changed and will not be opened")
    trust = trust_checker(path)
    if not isinstance(trust, dict) or not isinstance(trust.get("can_open"), bool):
        raise TypeError("The Lians update publisher check returned an invalid result")
    can_open = trust["can_open"]
    system = system or platform.system()

    if launcher is not None:
        launcher(path, can_open, system)
    elif system.casefold() == "windows":
        os.startfile(path if can_open else path.parent)  # type: ignore[attr-defined]
    elif system.casefold() == "darwin":
        command = ["/usr/bin/open", str(path)] if can_open else ["/usr/bin/open", "-R", str(path)]
        subprocess.Popen(
            command,
            close_fds=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
    else:
        subprocess.Popen(
            ["xdg-open", str(path.parent)],
            close_fds=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )

    return {
        "status": "opened" if can_open else "revealed",
        "package_name": path.name,
        "saved_location": "Downloads",
        **trust,
    }
