"""Install, exercise, and silently remove the real Windows package."""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import winreg
from pathlib import Path

UNINSTALL_KEY = r"Software\Microsoft\Windows\CurrentVersion\Uninstall\Lians Bridge"


def _run(argv: list[str], *, environment: dict[str, str], timeout: int = 120):
    return subprocess.run(
        argv,
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        env=environment,
        timeout=timeout,
    )


def _uninstall_registration_exists() -> bool:
    try:
        key = winreg.OpenKey(
            winreg.HKEY_CURRENT_USER,
            UNINSTALL_KEY,
            access=winreg.KEY_READ | winreg.KEY_WOW64_64KEY,
        )
    except FileNotFoundError:
        return False
    key.Close()
    return True


def _verify_authenticode(
    path: Path, thumbprint: str, *, environment: dict[str, str]
) -> None:
    powershell = shutil.which("pwsh") or shutil.which("powershell")
    if powershell is None:
        raise RuntimeError("PowerShell is required to verify Authenticode")
    script = (
        "$signature = Get-AuthenticodeSignature -LiteralPath $args[0]; "
        "$expected = $args[1].Replace(' ', '').ToUpperInvariant(); "
        "if ($signature.Status -ne 'Valid' -or -not $signature.SignerCertificate -or "
        "$signature.SignerCertificate.Thumbprint.ToUpperInvariant() -ne $expected) { "
        "Write-Error 'Installed runtime signature is invalid or has the wrong publisher'; "
        "exit 1 }"
    )
    verified = _run(
        [powershell, "-NoProfile", "-NonInteractive", "-Command", script, str(path), thumbprint],
        environment=environment,
        timeout=60,
    )
    assert verified.returncode == 0, (verified.stdout, verified.stderr)


def _wait_for_uninstall(binary: Path, uninstaller: Path, *, timeout: int = 15) -> None:
    deadline = time.monotonic() + timeout
    while binary.exists() or uninstaller.exists() or _uninstall_registration_exists():
        if time.monotonic() >= deadline:
            raise AssertionError("Windows uninstaller did not finish cleanup in time")
        time.sleep(0.1)


def main() -> None:
    if sys.platform != "win32":
        raise SystemExit("The Windows installer smoke test must run on Windows")

    parser = argparse.ArgumentParser()
    parser.add_argument("--installer", required=True, type=Path)
    parser.add_argument("--expected-version", required=True)
    parser.add_argument("--expected-signer-thumbprint")
    arguments = parser.parse_args()
    installer = arguments.installer.resolve()
    if not installer.is_file():
        raise SystemExit(f"Windows installer was not found: {installer}")
    if _uninstall_registration_exists():
        raise SystemExit(
            "Refusing to overwrite an existing Lians Windows installation during smoke testing"
        )

    with tempfile.TemporaryDirectory(prefix="lians-windows-package-") as directory:
        fixture = Path(directory)
        install_root = fixture / "Lians"
        home = fixture / "home"
        roaming = fixture / "roaming"
        local = fixture / "local"
        environment = os.environ.copy()
        environment.update(
            {
                "USERPROFILE": str(home),
                "HOME": str(home),
                "APPDATA": str(roaming),
                "LOCALAPPDATA": str(local),
                "LIANS_EASY_HOME": str(install_root),
            }
        )
        home.mkdir()
        roaming.mkdir()
        local.mkdir()

        binary = install_root / "LiansMemory.exe"
        uninstaller = install_root / "Uninstall Lians.exe"
        try:
            installed = _run(
                [str(installer), "/S", f"/D={install_root}"],
                environment=environment,
            )
            assert installed.returncode == 0, (installed.stdout, installed.stderr)
            assert binary.is_file()
            assert uninstaller.is_file()
            if arguments.expected_signer_thumbprint:
                _verify_authenticode(
                    binary,
                    arguments.expected_signer_thumbprint,
                    environment=environment,
                )

            with winreg.OpenKey(
                winreg.HKEY_CURRENT_USER,
                UNINSTALL_KEY,
                access=winreg.KEY_READ | winreg.KEY_WOW64_64KEY,
            ) as registry:
                assert (
                    winreg.QueryValueEx(registry, "DisplayVersion")[0]
                    == arguments.expected_version
                )
                assert (
                    Path(winreg.QueryValueEx(registry, "InstallLocation")[0]).resolve()
                    == install_root.resolve()
                )

            doctor = _run(
                [str(binary), "doctor", "--json"],
                environment=environment,
                timeout=60,
            )
            assert doctor.returncode == 0, (doctor.stdout, doctor.stderr)
            report = json.loads(doctor.stdout)
            assert report["runtime"]["standalone"] is True
            assert Path(report["runtime"]["command"]).resolve() == binary.resolve()
            assert report["runtime"]["installed"] is True

            app_smoke = _run(
                [
                    sys.executable,
                    str(Path(__file__).with_name("artifact_app_smoke.py")),
                    "--binary",
                    str(binary),
                ],
                environment=environment,
            )
            assert app_smoke.returncode == 0, (app_smoke.stdout, app_smoke.stderr)

            preserved = install_root / "memory.sqlite3"
            sentinel = b"encrypted-memory-is-preserved-by-silent-uninstall"
            preserved.write_bytes(sentinel)
            removed = _run([str(uninstaller), "/S"], environment=environment)
            assert removed.returncode == 0, (removed.stdout, removed.stderr)
            _wait_for_uninstall(binary, uninstaller)
            assert not binary.exists()
            assert not uninstaller.exists()
            assert preserved.read_bytes() == sentinel
            assert not _uninstall_registration_exists()

            print(
                json.dumps(
                    {
                        "app_opened_from_installed_binary": True,
                        "expected_version": arguments.expected_version,
                        "per_user_install": True,
                        "runtime_detected": True,
                        "runtime_signature_verified": bool(
                            arguments.expected_signer_thumbprint
                        ),
                        "silent_uninstall_preserved_memory": True,
                        "uninstaller_removed_runtime": True,
                    },
                    sort_keys=True,
                )
            )
        finally:
            if uninstaller.is_file():
                cleanup = _run([str(uninstaller), "/S"], environment=environment)
                if cleanup.returncode == 0:
                    _wait_for_uninstall(binary, uninstaller)


if __name__ == "__main__":
    main()
