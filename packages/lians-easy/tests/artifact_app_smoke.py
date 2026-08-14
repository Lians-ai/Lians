"""Exercise the embedded Lians App through a frozen Bridge executable."""

from __future__ import annotations

import argparse
import json
import re
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
            if "MEMORY CONTROL CENTER" not in script or "/v1/context" not in script:
                raise RuntimeError("Frozen Lians App is not the Bridge-enabled control center")

            with _open(f"{origin}/v1/status", cookie=cookie) as status_response:
                status = json.loads(status_response.read())
            if status.get("bridge") != "ready" or not status.get("memory", {}).get("encrypted"):
                raise RuntimeError("Frozen Lians App could not reach encrypted Bridge state")

            print(
                json.dumps(
                    {
                        "packaged_app_served": True,
                        "bridge_api_ready": True,
                        "encrypted_memory": True,
                        "process_running": process.poll() is None,
                    },
                    sort_keys=True,
                )
            )
        finally:
            if process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=5)


if __name__ == "__main__":
    main()
