"""Exercise the install-free Linux AppImage and its embedded Lians Bridge."""

from __future__ import annotations

import argparse
import json
import platform
import subprocess
import sys
import tempfile
from pathlib import Path


def _run(command: list[str], **kwargs) -> subprocess.CompletedProcess[str]:
    return subprocess.run(command, check=True, text=True, **kwargs)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--appimage", required=True, type=Path)
    parser.add_argument("--expected-version", required=True)
    parser.add_argument("--expected-architecture", default="x86_64")
    arguments = parser.parse_args()

    appimage = arguments.appimage.resolve()
    expected_name = (
        f"Lians-{arguments.expected_version}-linux-"
        f"{arguments.expected_architecture}.AppImage"
    )
    if not appimage.is_file() or appimage.name != expected_name:
        raise SystemExit(f"Expected the exact Lians AppImage name {expected_name}: {appimage}")
    if arguments.expected_architecture != "x86_64":
        raise SystemExit("The current Lians AppImage contract supports x86_64 only")
    if platform.machine() != arguments.expected_architecture:
        raise SystemExit(
            f"Smoke host architecture {platform.machine()} does not match "
            f"{arguments.expected_architecture}"
        )

    appimage.chmod(0o755)
    direct_version = _run([str(appimage), "--version"], capture_output=True).stdout.strip()
    if direct_version != f"lians {arguments.expected_version}":
        raise RuntimeError(f"AppImage did not launch directly: {direct_version}")
    with tempfile.TemporaryDirectory(prefix="lians-appimage-smoke-") as directory:
        extraction_root = Path(directory)
        _run(
            [str(appimage), "--appimage-extract"],
            cwd=extraction_root,
            stdout=subprocess.DEVNULL,
        )
        app_directory = extraction_root / "squashfs-root"
        runtime = app_directory / "usr" / "bin" / "LiansMemory"
        desktop = app_directory / "lians.desktop"
        icon = app_directory / "lians.png"
        app_run = app_directory / "AppRun"
        for required in (runtime, desktop, icon, app_run):
            if not required.is_file():
                raise RuntimeError(f"AppImage is missing {required.relative_to(app_directory)}")

        desktop_text = desktop.read_text(encoding="utf-8")
        expected_fields = (
            "Type=Application",
            "Name=Lians",
            "Exec=LiansMemory",
            "Icon=lians",
            "Terminal=false",
            f"X-AppImage-Version={arguments.expected_version}",
        )
        for field in expected_fields:
            if field not in desktop_text.splitlines():
                raise RuntimeError(f"AppImage desktop identity is missing {field}")

        version = _run([str(runtime), "--version"], capture_output=True).stdout.strip()
        if version != f"lians {arguments.expected_version}":
            raise RuntimeError(f"Unexpected AppImage runtime version: {version}")
        file_details = _run(["file", "--brief", str(runtime)], capture_output=True).stdout
        if "x86-64" not in file_details:
            raise RuntimeError(f"AppImage runtime has the wrong architecture: {file_details}")

        smoke = Path(__file__).with_name("artifact_app_smoke.py")
        _run([sys.executable, str(smoke), "--binary", str(runtime)])
        print(
            json.dumps(
                {
                    "appimage_identity_valid": True,
                    "architecture": arguments.expected_architecture,
                    "bridge_smoke_passed": True,
                    "install_required": False,
                    "version": arguments.expected_version,
                },
                sort_keys=True,
            )
        )


if __name__ == "__main__":
    main()
