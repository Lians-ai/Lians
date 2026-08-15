"""Mount, inspect, install, and exercise the real macOS package."""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path


def _run(
    command: list[str], *, timeout: int = 60, check: bool = True
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        command,
        check=check,
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=timeout,
    )


def _plist_value(plist: Path, key: str) -> str:
    return _run(["plutil", "-extract", key, "raw", str(plist)]).stdout.strip()


def _signature_details(path: Path) -> str:
    result = _run(
        ["codesign", "-dv", "--verbose=4", str(path)],
        check=False,
    )
    if result.returncode != 0:
        raise AssertionError(result.stderr or result.stdout)
    return f"{result.stdout}\n{result.stderr}"


def main() -> None:
    if sys.platform != "darwin":
        raise SystemExit("The macOS DMG smoke test must run on macOS")

    parser = argparse.ArgumentParser()
    parser.add_argument("--dmg", required=True, type=Path)
    parser.add_argument("--expected-version", required=True)
    parser.add_argument(
        "--expected-architecture", required=True, choices=("arm64", "x86_64")
    )
    parser.add_argument("--expected-signing-identity")
    parser.add_argument("--expected-team-id")
    parser.add_argument("--require-notarized", action="store_true")
    arguments = parser.parse_args()

    dmg = arguments.dmg.resolve()
    if not dmg.is_file():
        raise SystemExit(f"Lians DMG was not found: {dmg}")

    _run(["codesign", "--verify", "--strict", "--verbose=2", str(dmg)])
    if arguments.require_notarized:
        _run(["xcrun", "stapler", "validate", str(dmg)])
        _run(
            [
                "spctl",
                "--assess",
                "--type",
                "open",
                "--context",
                "context:primary-signature",
                "--verbose=4",
                str(dmg),
            ]
        )

    with tempfile.TemporaryDirectory(prefix="lians-macos-package-") as directory:
        fixture = Path(directory)
        mount = fixture / "mounted"
        installed_app = fixture / "Applications" / "Lians.app"
        mount.mkdir()
        attached = False
        try:
            _run(
                [
                    "hdiutil",
                    "attach",
                    "-readonly",
                    "-nobrowse",
                    "-mountpoint",
                    str(mount),
                    str(dmg),
                ]
            )
            attached = True
            app = mount / "Lians.app"
            applications = mount / "Applications"
            executable = app / "Contents" / "MacOS" / "LiansMemory"
            plist = app / "Contents" / "Info.plist"

            assert app.is_dir()
            assert applications.is_symlink()
            assert os.readlink(applications) == "/Applications"
            assert executable.is_file() and os.access(executable, os.X_OK)
            assert _plist_value(plist, "CFBundleIdentifier") == "ai.lians.memory"
            assert (
                _plist_value(plist, "CFBundleShortVersionString")
                == arguments.expected_version
            )
            assert _plist_value(plist, "LSMinimumSystemVersion") == "13.0"
            architectures = _run(["lipo", "-archs", str(executable)]).stdout.strip()
            assert architectures == arguments.expected_architecture
            _run(
                [
                    "codesign",
                    "--verify",
                    "--deep",
                    "--strict",
                    "--verbose=2",
                    str(app),
                ]
            )

            if arguments.expected_signing_identity or arguments.expected_team_id:
                app_signature = _signature_details(app)
                dmg_signature = _signature_details(dmg)
                for details in (app_signature, dmg_signature):
                    if arguments.expected_signing_identity:
                        assert (
                            f"Authority={arguments.expected_signing_identity}" in details
                        )
                    if arguments.expected_team_id:
                        assert f"TeamIdentifier={arguments.expected_team_id}" in details

            installed_app.parent.mkdir()
            _run(["ditto", str(app), str(installed_app)])
            installed_executable = (
                installed_app / "Contents" / "MacOS" / "LiansMemory"
            )
            smoke = _run(
                [
                    sys.executable,
                    str(Path(__file__).with_name("artifact_app_smoke.py")),
                    "--binary",
                    str(installed_executable),
                ],
                timeout=90,
            )
            runtime_result = json.loads(smoke.stdout)
            assert runtime_result["packaged_app_served"] is True
            assert runtime_result["bridge_api_ready"] is True
            assert runtime_result["encrypted_memory"] is True

            shutil.rmtree(installed_app)
            assert not installed_app.exists()
            summary = {
                "applications_shortcut": True,
                "architecture": architectures,
                "bundle_identifier": "ai.lians.memory",
                "bundle_removed_after_test": True,
                "dmg_signature_valid": True,
                "minimum_macos": "13.0",
                "notarization_required": arguments.require_notarized,
                "packaged_app_served": True,
                "version": arguments.expected_version,
            }
        finally:
            if attached:
                detached = _run(["hdiutil", "detach", str(mount)], check=False)
                if detached.returncode != 0:
                    forced = _run(
                        ["hdiutil", "detach", "-force", str(mount)], check=False
                    )
                    if forced.returncode != 0:
                        raise RuntimeError(forced.stderr or detached.stderr)

    print(json.dumps(summary, sort_keys=True))


if __name__ == "__main__":
    main()
