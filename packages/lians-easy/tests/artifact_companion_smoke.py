"""Prove the frozen no-argument executable stays alive as the Windows companion."""

from __future__ import annotations

import argparse
import json
import os
import shutil
import socket
import subprocess
import tempfile
import time
from contextlib import contextmanager
from pathlib import Path
from urllib.error import URLError
from urllib.request import urlopen

ORIGIN = "http://127.0.0.1:7317"


@contextmanager
def _temporary_directory() -> Path:
    root = Path(tempfile.mkdtemp(prefix="lians-companion-smoke-"))
    try:
        yield root
    finally:
        for attempt in range(20):
            try:
                shutil.rmtree(root)
                break
            except PermissionError:
                if attempt == 19:
                    raise
                time.sleep(0.25)


def _port_is_free() -> bool:
    with socket.socket() as candidate:
        try:
            candidate.bind(("127.0.0.1", 7317))
        except OSError:
            return False
    return True


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
    try:
        process.wait(timeout=10)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait(timeout=5)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--binary", required=True, type=Path)
    args = parser.parse_args()
    binary = args.binary.resolve()
    if not binary.is_file():
        raise SystemExit(f"Frozen companion was not found: {binary}")
    if os.name != "nt":
        raise SystemExit("The resident companion smoke test requires Windows")
    if not _port_is_free():
        raise SystemExit("Port 7317 is already in use; stop the existing Lians companion first")

    with _temporary_directory() as fixture:
        home = fixture / "home"
        roaming = fixture / "roaming"
        local = fixture / "local"
        config = roaming / "Claude" / "claude_desktop_config.json"
        config.parent.mkdir(parents=True)
        original = json.dumps(
            {
                "theme": "dark",
                "mcpServers": {
                    "lians": {"command": "fixture-lians", "args": ["mcp"]}
                },
            },
            separators=(",", ":"),
        ).encode()
        config.write_bytes(original)
        environment = os.environ.copy()
        environment.update(
            {
                "USERPROFILE": str(home),
                "HOME": str(home),
                "APPDATA": str(roaming),
                "LOCALAPPDATA": str(local),
                "LIANS_EASY_HOME": str(local / "Lians"),
            }
        )
        process = subprocess.Popen(
            [str(binary)],
            env=environment,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        second_process: subprocess.Popen[bytes] | None = None
        try:
            deadline = time.monotonic() + 25
            while True:
                if process.poll() is not None:
                    raise RuntimeError(f"Frozen companion exited with {process.returncode}")
                try:
                    with urlopen(ORIGIN, timeout=1) as response:
                        page = response.read()
                        server = response.headers.get("Server", "")
                    break
                except URLError:
                    if time.monotonic() >= deadline:
                        raise RuntimeError("Frozen companion did not start its local bridge")
                    time.sleep(0.15)
            assert server.startswith("LiansBridge/")
            assert b"<title>Lians Memory</title>" in page
            assert process.poll() is None
            assert config.read_bytes() == original

            second_process = subprocess.Popen(
                [str(binary)],
                env=environment,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            try:
                second_process.wait(timeout=10)
            except subprocess.TimeoutExpired as error:
                raise RuntimeError(
                    "A second launch created another resident process instead of "
                    "restoring the native Lians window"
                ) from error
            assert second_process.returncode == 0
            assert process.poll() is None
            assert config.read_bytes() == original
            print(
                json.dumps(
                    {
                        "companion_started": True,
                        "bridge_running": True,
                        "second_launch_restored_native_window": True,
                        "process_stayed_alive": True,
                        "existing_agent_settings_unchanged": True,
                    },
                    sort_keys=True,
                )
            )
        finally:
            if second_process is not None:
                _stop_process_tree(second_process)
            _stop_process_tree(process)


if __name__ == "__main__":
    main()
