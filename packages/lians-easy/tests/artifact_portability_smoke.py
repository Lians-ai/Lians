"""Exercise encrypted backup and cross-device import through a frozen runtime."""

from __future__ import annotations

import argparse
import json
import os
import shutil
import signal
import socket
import sqlite3
import subprocess
import tempfile
import time
from contextlib import closing, contextmanager
from pathlib import Path
from urllib.error import URLError
from urllib.request import Request, urlopen


@contextmanager
def _temporary_directory() -> Path:
    root = Path(tempfile.mkdtemp(prefix="lians-portability-smoke-"))
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


def _available_port() -> int:
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        return listener.getsockname()[1]


def _run(argv: list[str], *, timeout: int = 120) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        argv,
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=timeout,
    )


def _stop_process_tree(process: subprocess.Popen[bytes]) -> None:
    if process.poll() is not None:
        return
    if os.name == "nt":
        taskkill = Path(os.environ.get("SYSTEMROOT", r"C:\Windows")) / "System32" / "taskkill.exe"
        subprocess.run(
            [str(taskkill), "/PID", str(process.pid), "/T", "/F"],
            check=False,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=10,
        )
    else:
        os.killpg(os.getpgid(process.pid), signal.SIGTERM)
    try:
        process.wait(timeout=10)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait(timeout=5)


def _wait_for_bridge(origin: str, process: subprocess.Popen[bytes]) -> str:
    deadline = time.monotonic() + 20
    while True:
        if process.poll() is not None:
            raise AssertionError(f"Frozen Bridge exited with {process.returncode}")
        try:
            with urlopen(origin, timeout=2) as response:
                response.read()
                return response.headers["Set-Cookie"].split(";", 1)[0]
        except URLError:
            if time.monotonic() >= deadline:
                raise AssertionError("Frozen Bridge did not become ready")
            time.sleep(0.1)


def _remember(origin: str, cookie: str, content: str) -> None:
    request = Request(
        f"{origin}/v1/remember",
        data=json.dumps(
            {
                "content": content,
                "scope": "global",
                "kind": "preference",
                "source": "frozen portability smoke",
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


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--binary", type=Path, required=True)
    arguments = parser.parse_args()
    binary = arguments.binary.resolve()
    if not binary.is_file():
        raise SystemExit(f"Frozen Bridge was not found: {binary}")

    with _temporary_directory() as root:
        source = root / "source" / "memory.sqlite3"
        target = root / "target" / "memory.sqlite3"
        backup = root / "portable.liansbackup"
        passphrase_file = root / "passphrase.txt"
        passphrase_file.write_text("frozen artifact portable passphrase\n", encoding="utf-8")
        if os.name != "nt":
            passphrase_file.chmod(0o600)
        content = "Portable frozen preference: use short, direct status updates."
        port = _available_port()
        origin = f"http://127.0.0.1:{port}"
        process = subprocess.Popen(
            [
                str(binary),
                "bridge",
                "--host",
                "127.0.0.1",
                "--port",
                str(port),
                "--data",
                str(source),
            ],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=os.name != "nt",
        )
        try:
            cookie = _wait_for_bridge(origin, process)
            _remember(origin, cookie, content)
        finally:
            _stop_process_tree(process)

        exported = _run(
            [
                str(binary),
                "backup",
                "export",
                "--output",
                str(backup),
                "--data",
                str(source),
                "--passphrase-file",
                str(passphrase_file),
                "--json",
            ]
        )
        assert exported.returncode == 0, (exported.stdout, exported.stderr)
        assert json.loads(exported.stdout)["status"] == "exported"
        assert content.encode() not in backup.read_bytes()

        verified = _run(
            [
                str(binary),
                "backup",
                "verify",
                "--input",
                str(backup),
                "--passphrase-file",
                str(passphrase_file),
                "--json",
            ]
        )
        assert verified.returncode == 0, (verified.stdout, verified.stderr)
        assert json.loads(verified.stdout)["status"] == "verified"

        imported = _run(
            [
                str(binary),
                "backup",
                "import",
                "--input",
                str(backup),
                "--data",
                str(target),
                "--passphrase-file",
                str(passphrase_file),
                "--yes",
                "--json",
            ]
        )
        assert imported.returncode == 0, (imported.stdout, imported.stderr)
        result = json.loads(imported.stdout)
        assert result["imported"]["memories"] == 1
        assert result["re_encrypted_for_this_device"] is True

        recalled = _run(
            [
                str(binary),
                "context",
                "--client",
                "portable-smoke",
                "--prompt",
                content,
                "--data",
                str(target),
                "--json",
            ]
        )
        assert recalled.returncode == 0, (recalled.stdout, recalled.stderr)
        assert content in json.loads(recalled.stdout)["context"]

        with closing(sqlite3.connect(source)) as source_database, closing(
            sqlite3.connect(target)
        ) as target_database:
            source_ciphertext = source_database.execute(
                "SELECT content_cipher FROM memories LIMIT 1"
            ).fetchone()[0]
            target_ciphertext = target_database.execute(
                "SELECT content_cipher FROM memories LIMIT 1"
            ).fetchone()[0]
        assert source_ciphertext != target_ciphertext
        print(
            json.dumps(
                {
                    "backup_encrypted": True,
                    "backup_verified": True,
                    "memory_recalled_after_import": True,
                    "re_encrypted_for_destination": True,
                },
                sort_keys=True,
            )
        )


if __name__ == "__main__":
    main()
