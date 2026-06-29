"""
SDK resilience: the HTTP client retries transient failures with backoff, and a
retried write reuses the same Idempotency-Key (so it can't double-write).
"""
import sys
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from datetime import datetime, timezone

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "sdk" / "python"))
from lians import AsyncLiansClient


class _Handler(BaseHTTPRequestHandler):
    idem_keys: list = []

    def do_POST(self):
        length = int(self.headers.get("Content-Length", 0) or 0)
        self.rfile.read(length)
        _Handler.idem_keys.append(self.headers.get("Idempotency-Key"))
        if len(_Handler.idem_keys) == 1:
            # First attempt: transient server error.
            self.send_response(503)
            self.end_headers()
            self.wfile.write(b'{"detail":"busy"}')
        else:
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(b'{"id":"m-1","content":"ok"}')

    def log_message(self, *args):  # silence test server logging
        pass


@pytest.fixture
def server():
    _Handler.idem_keys = []
    srv = HTTPServer(("127.0.0.1", 0), _Handler)
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    try:
        yield srv
    finally:
        srv.shutdown()


async def test_retry_on_503_then_success(server):
    port = server.server_address[1]
    client = AsyncLiansClient(
        base_url=f"http://127.0.0.1:{port}", api_key="k", backoff_factor=0.01
    )
    out = await client.add(
        agent_id="a", content="x",
        event_time=datetime(2026, 1, 1, tzinfo=timezone.utc),
    )
    assert out["id"] == "m-1"
    # Exactly one retry, and the retried request carried the SAME idempotency key.
    assert len(_Handler.idem_keys) == 2
    assert _Handler.idem_keys[0] is not None
    assert _Handler.idem_keys[0] == _Handler.idem_keys[1]
