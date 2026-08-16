from __future__ import annotations

import json
import socket
import subprocess
import sys
import time
from pathlib import Path
from urllib.error import URLError
from urllib.request import Request, urlopen

TOKEN = "artifact-smoke-token-123456"


def available_port() -> int:
    with socket.socket() as candidate:
        candidate.bind(("127.0.0.1", 0))
        return int(candidate.getsockname()[1])


def wait_for_page(url: str, *, timeout: float = 20) -> bytes:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            with urlopen(url, timeout=1) as response:
                return response.read()
        except URLError:
            time.sleep(0.2)
    raise RuntimeError("The frozen tester did not open its local page")


def main() -> int:
    if len(sys.argv) != 2:
        raise SystemExit("usage: artifact_tester_smoke.py PATH_TO_EXE")
    executable = Path(sys.argv[1]).resolve()
    if not executable.is_file():
        raise SystemExit(f"tester executable was not found: {executable}")

    port = available_port()
    base_url = f"http://127.0.0.1:{port}/{TOKEN}/"
    process = subprocess.Popen(
        [str(executable), "--no-browser", "--port", str(port), "--token", TOKEN],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    try:
        page = wait_for_page(base_url)
        assert b"See how much context Lians can cut" in page
        assert b"eyebrow" not in page.lower()
        assert "\N{EM DASH}".encode("utf-8") not in page
        for asset in ("style.css", "app.js", "lotus.svg", "sora.woff2"):
            with urlopen(base_url + asset, timeout=3) as response:
                assert response.status == 200
                assert response.read()
        with urlopen(base_url + "api/status", timeout=10) as response:
            status = json.loads(response.read())
        assert isinstance(status["ready"], bool)

        close = Request(
            base_url + "api/close",
            data=b"{}",
            method="POST",
            headers={
                "Content-Type": "application/json",
                "Origin": base_url.rstrip("/"),
            },
        )
        with urlopen(close, timeout=3) as response:
            assert json.loads(response.read()) == {"closed": True}
        assert process.wait(timeout=10) == 0
    finally:
        if process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)
    print("Frozen tester smoke test passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
