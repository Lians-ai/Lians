"""Exercise the embedded Lians App through a frozen Bridge executable."""

from __future__ import annotations

import argparse
import base64
import json
import os
import re
import signal
import socket
import subprocess
import tempfile
import time
from pathlib import Path
from urllib.error import URLError
from urllib.request import Request, urlopen


def _available_port() -> int:
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        return listener.getsockname()[1]


def _open(url: str, *, cookie: str | None = None):
    headers = {"Cookie": cookie} if cookie else {}
    return urlopen(Request(url, headers=headers), timeout=2)


def _post(origin: str, path: str, cookie: str, data: dict):
    return urlopen(
        Request(
            f"{origin}{path}",
            data=json.dumps(data).encode(),
            headers={
                "Content-Type": "application/json",
                "Cookie": cookie,
                "Origin": origin,
            },
            method="POST",
        ),
        timeout=20,
    )


def _stop_process_tree(process: subprocess.Popen[bytes]) -> None:
    """Stop the PyInstaller bootloader and its extracted child process."""
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
        if os.name == "nt":
            process.kill()
        else:
            os.killpg(os.getpgid(process.pid), signal.SIGKILL)
        process.wait(timeout=5)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--binary", required=True, type=Path)
    args = parser.parse_args()
    binary = args.binary.resolve()
    if not binary.is_file():
        raise SystemExit(f"Frozen Bridge was not found: {binary}")

    port = _available_port()
    origin = f"http://127.0.0.1:{port}"
    with tempfile.TemporaryDirectory(prefix="lians-app-smoke-") as directory:
        data_path = Path(directory) / "memory.sqlite3"
        process = subprocess.Popen(
            [
                str(binary),
                "bridge",
                "--host",
                "127.0.0.1",
                "--port",
                str(port),
                "--data",
                str(data_path),
            ],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=os.name != "nt",
        )
        try:
            deadline = time.monotonic() + 20
            while True:
                if process.poll() is not None:
                    raise RuntimeError(f"Frozen Bridge exited with {process.returncode}")
                try:
                    response = _open(origin)
                    break
                except URLError:
                    if time.monotonic() >= deadline:
                        raise RuntimeError("Frozen Bridge did not serve the Lians App in time")
                    time.sleep(0.1)

            with response:
                html = response.read().decode("utf-8")
                cookie = response.headers["Set-Cookie"].split(";", 1)[0]
                policy = response.headers["Content-Security-Policy"]
            if "<title>Lians Memory</title>" not in html:
                raise RuntimeError("Frozen Bridge served the fallback page instead of Lians App")
            if "object-src 'none'" not in policy:
                raise RuntimeError("Frozen Lians App did not serve the expected content policy")

            script_match = re.search(r'src="([^"]+\.js)"', html)
            if script_match is None:
                raise RuntimeError("Frozen Lians App has no client bundle")
            with _open(f"{origin}{script_match.group(1)}") as script_response:
                script = script_response.read().decode("utf-8")
            if (
                "MEMORY CONTROL CENTER" not in script
                or "MOVE MEMORY SAFELY" not in script
                or "/v1/context" not in script
                or "/v1/backups/export" not in script
                or "/v1/backups/import" not in script
            ):
                raise RuntimeError("Frozen Lians App is not the Bridge-enabled control center")

            with _open(f"{origin}/v1/status", cookie=cookie) as status_response:
                status = json.loads(status_response.read())
            if status.get("bridge") != "ready" or not status.get("memory", {}).get("encrypted"):
                raise RuntimeError("Frozen Lians App could not reach encrypted Bridge state")

            memory_content = "Packaged App portability preference: keep status updates concise."
            with _post(
                origin,
                "/v1/remember",
                cookie,
                {"content": memory_content, "scope": "global", "kind": "preference"},
            ) as remember_response:
                if remember_response.status != 201:
                    raise RuntimeError("Frozen Lians App could not create portable memory")
            passphrase = "frozen app backup passphrase"
            with _post(
                origin,
                "/v1/backups/export",
                cookie,
                {"passphrase": passphrase, "confirmation": passphrase},
            ) as export_response:
                backup = export_response.read()
                if export_response.headers.get_content_type() != "application/vnd.lians.backup+json":
                    raise RuntimeError("Frozen Lians App did not return an encrypted backup")
            if memory_content.encode() in backup:
                raise RuntimeError("Frozen Lians App backup exposed plaintext memory")
            encoded_backup = base64.b64encode(backup).decode()
            with _post(
                origin,
                "/v1/backups/verify",
                cookie,
                {"passphrase": passphrase, "backup": encoded_backup},
            ) as verify_response:
                verified = json.loads(verify_response.read())
            if verified.get("status") != "verified" or verified.get("memories") != 1:
                raise RuntimeError("Frozen Lians App did not verify its encrypted backup")
            with _post(
                origin,
                "/v1/privacy/erase",
                cookie,
                {"confirmed": True, "confirmation": "ERASE ALL LIANS MEMORY"},
            ) as erase_response:
                erased = json.loads(erase_response.read())
            if erased.get("status") != "erased":
                raise RuntimeError("Frozen Lians App could not prepare the import fixture")
            with _post(
                origin,
                "/v1/backups/import",
                cookie,
                {"passphrase": passphrase, "backup": encoded_backup, "confirmed": True},
            ) as import_response:
                imported = json.loads(import_response.read())
            if imported.get("imported", {}).get("memories") != 1:
                raise RuntimeError("Frozen Lians App did not import its encrypted backup")
            with _post(
                origin,
                "/v1/context",
                cookie,
                {"prompt": memory_content, "client": "packaged-app-smoke"},
            ) as context_response:
                context = json.loads(context_response.read())
            if memory_content not in context.get("context", ""):
                raise RuntimeError("Frozen Lians App did not recall imported memory")

            print(
                json.dumps(
                    {
                        "packaged_app_served": True,
                        "bridge_api_ready": True,
                        "encrypted_memory": True,
                        "portable_backup_restored": True,
                        "process_running": process.poll() is None,
                    },
                    sort_keys=True,
                )
            )
        finally:
            _stop_process_tree(process)


if __name__ == "__main__":
    main()
