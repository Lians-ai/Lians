"""Smoke-test an installed wheel without importing the source checkout."""

from __future__ import annotations

import argparse
import json
import sys
import tempfile
import threading
import urllib.request
from pathlib import Path


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("target", type=Path)
    parser.add_argument("--expected-version", required=True)
    args = parser.parse_args()
    sys.path.insert(0, str(args.target.resolve()))

    import lians_finish
    from lians_finish.app import LiansApplication, create_server

    if lians_finish.__version__ != args.expected_version:
        raise RuntimeError(f"expected {args.expected_version}, imported {lians_finish.__version__}")
    imported = Path(lians_finish.__file__).resolve()
    if args.target.resolve() not in imported.parents:
        raise RuntimeError(f"wheel was not imported from target: {imported}")

    web = imported.parent / "web"
    for name in ("index.html", "styles.css", "app.js", "wordmark.png.b64"):
        if not (web / name).is_file():
            raise RuntimeError(f"missing packaged web asset: {name}")

    with tempfile.TemporaryDirectory() as directory:
        app = LiansApplication(
            Path(directory) / "data",
            default_repository=Path.cwd(),
            token="installed-wheel-token",
        )
        server = create_server(app, port=0)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        port = server.server_address[1]
        try:
            with urllib.request.urlopen(f"http://127.0.0.1:{port}/api/health") as response:
                health = json.load(response)
                if health != {"status": "ok", "scope": "loopback"}:
                    raise RuntimeError(f"unexpected health payload: {health}")
                if response.headers.get("X-Frame-Options") != "DENY":
                    raise RuntimeError("security headers missing from wheel server")
            readiness_request = urllib.request.Request(
                f"http://127.0.0.1:{port}/api/readiness",
                headers={"X-Lians-Token": "installed-wheel-token"},
            )
            with urllib.request.urlopen(readiness_request) as response:
                readiness = json.load(response)
                if readiness.get("status") not in {"ready", "degraded"}:
                    raise RuntimeError(f"unexpected readiness payload: {readiness}")
        finally:
            server.shutdown()
            server.server_close()
            thread.join(5)

    print(
        json.dumps(
            {
                "version": lians_finish.__version__,
                "imported_from": str(imported),
                "assets": ["index.html", "styles.css", "app.js", "wordmark.png.b64"],
                "server": "passed",
            },
            separators=(",", ":"),
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
