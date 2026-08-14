from __future__ import annotations

import hashlib
import json
import re
import struct
from pathlib import Path

REPOSITORY_ROOT = Path(__file__).resolve().parents[3]
PACKAGE_ROOT = REPOSITORY_ROOT / "packages" / "lians-easy"


def _package_version() -> str:
    pyproject = (PACKAGE_ROOT / "pyproject.toml").read_text(encoding="utf-8")
    match = re.search(r'^version\s*=\s*"([^"]+)"', pyproject, flags=re.MULTILINE)
    assert match is not None
    return match.group(1)


def test_windows_identity_resources_match_package_version() -> None:
    version = _package_version()
    numeric = tuple(int(part) for part in version.split(".")) + (0,)
    resource = (PACKAGE_ROOT / "windows-version-info.txt").read_text(encoding="utf-8")

    assert f"filevers={numeric}" in resource
    assert f"prodvers={numeric}" in resource
    assert f"StringStruct('FileVersion', '{version}')" in resource
    assert f"StringStruct('ProductVersion', '{version}')" in resource
    assert "StringStruct('ProductName', 'Lians Bridge')" in resource
    assert "StringStruct('OriginalFilename', 'LiansMemory.exe')" in resource


def test_windows_icon_contains_standard_shell_sizes() -> None:
    icon = (PACKAGE_ROOT / "windows-lians.ico").read_bytes()
    reserved, kind, count = struct.unpack("<HHH", icon[:6])
    assert (reserved, kind) == (0, 1)

    sizes: list[int] = []
    for index in range(count):
        offset = 6 + index * 16
        width, height, _, _, _, bits, length, image_offset = struct.unpack(
            "<BBBBHHII", icon[offset : offset + 16]
        )
        width = width or 256
        height = height or 256
        assert width == height
        assert bits == 32
        assert length > 0
        assert image_offset + length <= len(icon)
        sizes.append(width)

    assert sizes == [16, 24, 32, 48, 64, 128, 256]


def test_stable_release_cannot_upload_unsigned_desktop_assets() -> None:
    workflow = (REPOSITORY_ROOT / ".github" / "workflows" / "release.yml").read_text(
        encoding="utf-8"
    )
    desktop_job = workflow.split("  lians-easy:\n", 1)[1].split("\n  go-tag:", 1)[0]

    assert "vars.PUBLISH_SIGNED_LIANS_DESKTOP == 'true'" in desktop_job
    assert "Get-AuthenticodeSignature" in desktop_job
    assert "signature.Status -ne 'Valid'" in desktop_job
    assert "Block incomplete macOS publisher path" in desktop_job
    assert "Block incomplete Linux publisher path" in desktop_job
    assert desktop_job.index("Get-AuthenticodeSignature") < desktop_job.index(
        "gh release upload"
    )

    pull_request_workflow = (
        REPOSITORY_ROOT / ".github" / "workflows" / "build-lians-easy.yml"
    ).read_text(encoding="utf-8")
    for build_contract in (workflow, pull_request_workflow):
        assert "--add-data" in build_contract
        assert "lians_easy/app" in build_contract
        assert "--icon packages/lians-easy/windows-lians.ico" in build_contract
        assert "--version-file packages/lians-easy/windows-version-info.txt" in build_contract
        assert "Verify Windows package identity" in build_contract
        assert "artifact_app_smoke.py" in build_contract


def test_packaged_control_center_is_source_pinned_and_bounded() -> None:
    app_root = PACKAGE_ROOT / "lians_easy" / "app"
    manifest = json.loads((app_root / "manifest.json").read_text(encoding="utf-8"))

    assert manifest["source"]["commit"] == "27c8a2cd23e3e241d9125818a87ff1295a32e369"
    assert manifest["source"]["sites_version"] == 26
    assert manifest["source"]["build"] == "npm run build:local"

    expected = set(manifest["files"])
    actual = {
        path.relative_to(app_root).as_posix()
        for path in app_root.rglob("*")
        if path.is_file() and path.name != "manifest.json"
    }
    assert actual == expected

    total_bytes = 0
    for relative, record in manifest["files"].items():
        payload = (app_root / relative).read_bytes()
        assert len(payload) == record["bytes"]
        assert hashlib.sha256(payload).hexdigest() == record["sha256"]
        total_bytes += len(payload)
    assert total_bytes < 400_000

    script = (app_root / "assets" / "index-Cefa2sSe.js").read_text(encoding="utf-8")
    assert "\u00b7" in script
    assert "\u00c2\u00b7" not in script
