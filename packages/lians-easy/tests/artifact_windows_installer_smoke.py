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


def _verify_authenticode(path: Path, thumbprint: str, *, environment: dict[str, str]) -> None:
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
    parser.add_argument("--baseline-installer", type=Path)
    parser.add_argument("--baseline-version")
    arguments = parser.parse_args()
    installer = arguments.installer.resolve()
    rollback_fixture = arguments.rollback_fixture.resolve()
    if not installer.is_file():
        raise SystemExit(f"Windows installer was not found: {installer}")
    if not rollback_fixture.is_file():
        raise SystemExit(f"Windows rollback fixture was not found: {rollback_fixture}")
    if bool(arguments.baseline_installer) != bool(arguments.baseline_version):
        raise SystemExit("--baseline-installer and --baseline-version must be supplied together")
    baseline_installer = (
        arguments.baseline_installer.resolve() if arguments.baseline_installer is not None else None
    )
    if baseline_installer is not None and not baseline_installer.is_file():
        raise SystemExit(f"Windows baseline installer was not found: {baseline_installer}")
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
        running_bridge: subprocess.Popen[bytes] | None = None
        try:
            initial_installer = baseline_installer or installer
            initial_version = arguments.baseline_version or arguments.expected_version
            installed = _run(
                [str(initial_installer), "/S", f"/D={install_root}"],
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
                assert winreg.QueryValueEx(registry, "DisplayVersion")[0] == initial_version
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

            connected = _run(
                [
                    str(binary),
                    "install",
                    "--clients",
                    "cursor",
                    "--yes",
                    "--json",
                ],
                environment=environment,
                timeout=60,
            )
            assert connected.returncode == 0, (connected.stdout, connected.stderr)
            assert json.loads(connected.stdout)["status"] == "installed"
            cursor_config = home / ".cursor" / "mcp.json"
            connected_config = cursor_config.read_bytes()

            preserved = install_root / "memory.sqlite3"
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

            original_runtime = binary.read_bytes()
            upgraded = _run(
                [str(installer), "/S", f"/D={install_root}"],
                environment=environment,
            )
            assert upgraded.returncode == 0, (upgraded.stdout, upgraded.stderr)
            running_bridge.wait(timeout=15)
            assert running_bridge.returncode == 0
            if baseline_installer is None:
                assert binary.read_bytes() == original_runtime
            else:
                assert binary.read_bytes() != original_runtime
            assert not (install_root / ".lians-previous-runtime.exe").exists()
            assert cursor_config.read_bytes() == connected_config
            if arguments.expected_signer_thumbprint:
                _verify_authenticode(
                    binary,
                    arguments.expected_signer_thumbprint,
                    environment=environment,
                )
            _recall_memory(binary, preserved, memory_content, environment=environment)

            rejected = _run(
                [str(rollback_fixture), "/S", f"/D={install_root}"],
                environment=environment,
            )
            assert rejected.returncode != 0, (rejected.stdout, rejected.stderr)
            assert binary.read_bytes() == original_runtime
            assert not (install_root / ".lians-previous-runtime.exe").exists()
            _recall_memory(binary, preserved, memory_content, environment=environment)

            with winreg.OpenKey(
                winreg.HKEY_CURRENT_USER,
                UNINSTALL_KEY,
                access=winreg.KEY_READ | winreg.KEY_WOW64_64KEY,
            ) as registry:
                assert (
                    winreg.QueryValueEx(registry, "DisplayVersion")[0] == arguments.expected_version
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

            preserved_files = {
                path: path.read_bytes()
                for path in install_root.iterdir()
                if path.is_file() and path.name.startswith("memory")
            }
            assert preserved in preserved_files
            removed = _run([str(uninstaller), "/S"], environment=environment)
            assert removed.returncode == 0, (removed.stdout, removed.stderr)
            _wait_for_uninstall(binary, uninstaller)
            assert not binary.exists()
            assert not uninstaller.exists()
            for path, content in preserved_files.items():
                assert path.read_bytes() == content
            assert not _uninstall_registration_exists()

            print(
                json.dumps(
                    {
                        "app_opened_from_installed_binary": True,
                        "baseline_version": arguments.baseline_version,
                        "expected_version": arguments.expected_version,
                        "historical_upgrade_verified": baseline_installer is not None,
                        "integration_preserved_across_upgrade": True,
                        "per_user_install": True,
                        "running_bridge_stopped_for_upgrade": True,
                        "runtime_detected": True,
                        "runtime_health_checked_before_commit": True,
                        "runtime_rollback_verified": True,
                        "runtime_signature_verified": bool(arguments.expected_signer_thumbprint),
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
