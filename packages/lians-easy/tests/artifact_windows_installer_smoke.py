"""Install, exercise, and silently remove the real Windows package."""

from __future__ import annotations

import argparse
import json
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import winreg
from pathlib import Path
from urllib.error import URLError
from urllib.request import Request, urlopen

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


def _pe_subsystem(path: Path) -> int:
    """Read the PE optional-header subsystem without external SDK tools."""

    payload = path.read_bytes()
    if payload[:2] != b"MZ" or len(payload) < 0x40:
        raise AssertionError(f"Not a Windows PE executable: {path}")
    pe_offset = int.from_bytes(payload[0x3C:0x40], "little")
    optional_header = pe_offset + 4 + 20
    if payload[pe_offset : pe_offset + 4] != b"PE\0\0":
        raise AssertionError(f"Invalid PE signature: {path}")
    return int.from_bytes(payload[optional_header + 68 : optional_header + 70], "little")


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


def _stop_process_tree(process: subprocess.Popen[bytes]) -> None:
    if process.poll() is not None:
        return
    taskkill = Path(os.environ.get("SYSTEMROOT", r"C:\Windows")) / "System32" / "taskkill.exe"
    subprocess.run(
        [str(taskkill), "/PID", str(process.pid), "/T", "/F"],
        check=False,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        timeout=10,
    )
    process.wait(timeout=10)


def _available_port() -> int:
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        return listener.getsockname()[1]


def _wait_for_bridge(origin: str, process: subprocess.Popen[bytes]) -> str:
    deadline = time.monotonic() + 20
    while True:
        if process.poll() is not None:
            raise AssertionError(f"Installed Bridge exited with {process.returncode}")
        try:
            with urlopen(origin, timeout=2) as response:
                response.read()
                return response.headers["Set-Cookie"].split(";", 1)[0]
        except URLError:
            if time.monotonic() >= deadline:
                raise AssertionError("Installed Bridge did not become ready in time")
            time.sleep(0.1)


def _remember(origin: str, cookie: str, content: str) -> None:
    request = Request(
        f"{origin}/v1/remember",
        data=json.dumps(
            {
                "content": content,
                "scope": "global",
                "kind": "preference",
                "source": "Windows upgrade smoke",
            }
        ).encode(),
        headers={
            "Content-Type": "application/json",
            "Cookie": cookie,
            "Origin": origin,
        },
        method="POST",
    )
    with urlopen(request, timeout=5) as response:
        assert response.status == 201


def _recall_memory(
    binary: Path, data_path: Path, content: str, *, environment: dict[str, str]
) -> None:
    recalled = _run(
        [
            str(binary),
            "context",
            "--client",
            "upgrade-smoke",
            "--prompt",
            content,
            "--data",
            str(data_path),
            "--json",
        ],
        environment=environment,
        timeout=60,
    )
    assert recalled.returncode == 0, (recalled.stdout, recalled.stderr)
    assert content in json.loads(recalled.stdout)["context"]


def main() -> None:
    if sys.platform != "win32":
        raise SystemExit("The Windows installer smoke test must run on Windows")

    parser = argparse.ArgumentParser()
    parser.add_argument("--installer", required=True, type=Path)
    parser.add_argument("--rollback-fixture", required=True, type=Path)
    parser.add_argument("--expected-version", required=True)
    parser.add_argument("--expected-signer-thumbprint")
    arguments = parser.parse_args()
    installer = arguments.installer.resolve()
    rollback_fixture = arguments.rollback_fixture.resolve()
    if not installer.is_file():
        raise SystemExit(f"Windows installer was not found: {installer}")
    if not rollback_fixture.is_file():
        raise SystemExit(f"Windows rollback fixture was not found: {rollback_fixture}")
    if _uninstall_registration_exists():
        raise SystemExit(
            "Refusing to overwrite an existing Lians Windows installation during smoke testing"
        )

    with tempfile.TemporaryDirectory(prefix="lians-windows-package-") as directory:
        fixture = Path(directory)
        local = fixture / "local"
        install_root = local / "Programs" / "Lians"
        data_root = local / "Lians"
        home = fixture / "home"
        roaming = fixture / "roaming"
        environment = os.environ.copy()
        environment.update(
            {
                "USERPROFILE": str(home),
                "HOME": str(home),
                "APPDATA": str(roaming),
                "LOCALAPPDATA": str(local),
                "LIANS_EASY_HOME": str(data_root),
            }
        )
        home.mkdir()
        roaming.mkdir()
        local.mkdir()

        launcher = install_root / "Lians.exe"
        windowed_app = install_root / "LiansApp" / "Lians.exe"
        binary = install_root / "LiansApp" / "LiansMemory.exe"
        uninstaller = install_root / "Uninstall Lians.exe"
        running_bridge: subprocess.Popen[bytes] | None = None
        try:
            installed = _run(
                [str(installer), "/S", f"/D={install_root}"],
                environment=environment,
            )
            assert installed.returncode == 0, (installed.stdout, installed.stderr)
            assert launcher.is_file()
            assert windowed_app.is_file()
            assert binary.is_file()
            assert uninstaller.is_file()
            assert _pe_subsystem(launcher) == 2
            assert _pe_subsystem(windowed_app) == 2
            assert _pe_subsystem(binary) == 3
            if arguments.expected_signer_thumbprint:
                for executable in (launcher, windowed_app, binary):
                    _verify_authenticode(
                        executable,
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
            assert Path(report["runtime"]["running_from"]).resolve() == binary.resolve()
            assert Path(report["runtime"]["command"]).resolve() == (
                data_root / "LiansMemory.exe"
            ).resolve()
            assert report["runtime"]["installed"] is False

            preserved = data_root / "memory.sqlite3"
            memory_content = "Upgrade smoke preference: always preserve this encrypted memory."
            port = _available_port()
            origin = f"http://127.0.0.1:{port}"
            running_bridge = subprocess.Popen(
                [
                    str(binary),
                    "bridge",
                    "--host",
                    "127.0.0.1",
                    "--port",
                    str(port),
                    "--data",
                    str(preserved),
                ],
                env=environment,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            cookie = _wait_for_bridge(origin, running_bridge)
            _remember(origin, cookie, memory_content)

            original_launcher = launcher.read_bytes()
            original_windowed_app = windowed_app.read_bytes()
            original_runtime = binary.read_bytes()
            upgraded = _run(
                [str(installer), "/S", f"/D={install_root}"],
                environment=environment,
            )
            assert upgraded.returncode == 0, (upgraded.stdout, upgraded.stderr)
            running_bridge.wait(timeout=15)
            assert running_bridge.returncode == 0
            assert launcher.read_bytes() == original_launcher
            assert windowed_app.read_bytes() == original_windowed_app
            assert binary.read_bytes() == original_runtime
            assert not (install_root / ".lians-previous-app").exists()
            assert not (install_root / ".lians-previous-launcher.exe").exists()
            _recall_memory(binary, preserved, memory_content, environment=environment)

            rejected = _run(
                [str(rollback_fixture), "/S", f"/D={install_root}"],
                environment=environment,
            )
            assert rejected.returncode != 0, (rejected.stdout, rejected.stderr)
            assert launcher.read_bytes() == original_launcher
            assert windowed_app.read_bytes() == original_windowed_app
            assert binary.read_bytes() == original_runtime
            assert not (install_root / ".lians-previous-app").exists()
            assert not (install_root / ".lians-previous-launcher.exe").exists()
            _recall_memory(binary, preserved, memory_content, environment=environment)

            with winreg.OpenKey(
                winreg.HKEY_CURRENT_USER,
                UNINSTALL_KEY,
                access=winreg.KEY_READ | winreg.KEY_WOW64_64KEY,
            ) as registry:
                assert (
                    winreg.QueryValueEx(registry, "DisplayVersion")[0]
                    == arguments.expected_version
                )

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

            configured = _run(
                [
                    str(binary),
                    "optimize",
                    "--clients",
                    "codex",
                    "--yes",
                    "--json",
                ],
                environment=environment,
            )
            assert configured.returncode == 0, (configured.stdout, configured.stderr)
            configured_report = json.loads(configured.stdout)
            assert configured_report["status"] == "installed"
            data_runtime = data_root / "LiansMemory.exe"
            assert data_runtime.is_file()
            codex_config = home / ".codex" / "config.toml"
            assert "# >>> Lians Memory" in codex_config.read_text(encoding="utf-8")

            preserved_files = {
                path: path.read_bytes()
                for path in data_root.iterdir()
                if path.is_file() and path.name.startswith("memory")
            }
            assert preserved in preserved_files
            removed = _run([str(uninstaller), "/S"], environment=environment)
            assert removed.returncode == 0, (removed.stdout, removed.stderr)
            _wait_for_uninstall(binary, uninstaller)
            assert not launcher.exists()
            assert not windowed_app.exists()
            assert not binary.exists()
            assert not uninstaller.exists()
            assert not data_runtime.exists()
            assert "# >>> Lians Memory" not in codex_config.read_text(encoding="utf-8")
            for path, content in preserved_files.items():
                assert path.read_bytes() == content
            assert not _uninstall_registration_exists()

            print(
                json.dumps(
                    {
                        "app_opened_from_installed_binary": True,
                        "companion_bundle_installed": True,
                        "expected_version": arguments.expected_version,
                        "human_executables_are_windowed": True,
                        "per_user_install": True,
                        "running_bridge_stopped_for_upgrade": True,
                        "runtime_detected": True,
                        "runtime_health_checked_before_commit": True,
                        "runtime_rollback_verified": True,
                        "runtime_signature_verified": bool(
                            arguments.expected_signer_thumbprint
                        ),
                        "separate_app_and_data_roots": True,
                        "successful_upgrade_preserved_memory": True,
                        "silent_uninstall_preserved_memory": True,
                        "uninstaller_removed_runtime": True,
                    },
                    sort_keys=True,
                )
            )
        finally:
            if running_bridge is not None:
                _stop_process_tree(running_bridge)
            if uninstaller.is_file():
                cleanup = _run([str(uninstaller), "/S"], environment=environment)
                if cleanup.returncode == 0:
                    _wait_for_uninstall(binary, uninstaller)


if __name__ == "__main__":
    main()
