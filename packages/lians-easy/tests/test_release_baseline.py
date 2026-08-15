from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

SCRIPT = Path(__file__).parents[1] / "scripts" / "select_desktop_baseline.py"
SPEC = importlib.util.spec_from_file_location("select_desktop_baseline", SCRIPT)
assert SPEC and SPEC.loader
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


def release(tag: str, *asset_names: str, draft: bool = False, prerelease: bool = False):
    return {
        "tag_name": tag,
        "draft": draft,
        "prerelease": prerelease,
        "assets": [{"name": name} for name in asset_names],
    }


def test_selects_newest_older_windows_release_with_exact_checksum_pair() -> None:
    catalog = [
        release(
            "v0.5.0",
            "Lians-Setup-0.5.0.exe",
            "Lians-Setup-0.5.0.exe.sha256",
        ),
        release(
            "v0.6.1",
            "Lians-Setup-0.6.1.exe",
            "Lians-Setup-0.6.1.exe.sha256",
        ),
        release(
            "v0.6.2",
            "Lians-Setup-0.6.2.exe",
            "Lians-Setup-0.6.2.exe.sha256",
            prerelease=True,
        ),
        release("v0.6.3", "Lians-Setup-0.6.3.exe"),
        release(
            "v0.7.0",
            "Lians-Setup-0.7.0.exe",
            "Lians-Setup-0.7.0.exe.sha256",
        ),
    ]

    selected = MODULE.select_baseline(
        catalog,
        current_tag="v0.7.0",
        platform="windows",
    )

    assert selected == {
        "status": "selected",
        "tag": "v0.6.1",
        "version": "0.6.1",
        "artifact_name": "Lians-Setup-0.6.1.exe",
        "checksum_name": "Lians-Setup-0.6.1.exe.sha256",
    }


def test_accepts_paginated_catalog_and_selects_matching_macos_architecture() -> None:
    catalog = [
        [
            release(
                "v1.2.0",
                "Lians-1.2.0-macos-arm64.dmg",
                "Lians-1.2.0-macos-arm64.dmg.sha256",
                "Lians-1.2.0-macos-x86_64.dmg",
                "Lians-1.2.0-macos-x86_64.dmg.sha256",
            )
        ]
    ]

    selected = MODULE.select_baseline(
        catalog,
        current_tag="v1.3.0",
        platform="macos",
        architecture="x86_64",
    )

    assert selected["tag"] == "v1.2.0"
    assert selected["artifact_name"] == "Lians-1.2.0-macos-x86_64.dmg"


def test_reports_none_for_first_desktop_release() -> None:
    selected = MODULE.select_baseline(
        [release("v0.5.0", "lians-c-0.5.0.tar.gz")],
        current_tag="v0.6.0",
        platform="windows",
    )

    assert selected == {
        "status": "none",
        "current_tag": "v0.6.0",
        "platform": "windows",
        "architecture": None,
    }


@pytest.mark.parametrize("tag", ["0.6.0", "v1.2", "v01.2.3", "latest"])
def test_rejects_noncanonical_current_tag(tag: str) -> None:
    with pytest.raises(ValueError, match="stable vX.Y.Z"):
        MODULE.select_baseline([], current_tag=tag, platform="windows")


def test_macos_requires_supported_architecture() -> None:
    with pytest.raises(ValueError, match="requires arm64 or x86_64"):
        MODULE.select_baseline(
            [release("v1.0.0", "unused")],
            current_tag="v1.1.0",
            platform="macos",
        )
