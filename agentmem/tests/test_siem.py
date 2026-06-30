"""
SIEM audit streaming forwarder — delivers events to a collector, disabled by
default, and never raises on failure.
"""
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from types import SimpleNamespace

import pytest

from src.lians.siem import stream_event


class _Collector(BaseHTTPRequestHandler):
    received: list = []

    def do_POST(self):
        n = int(self.headers.get("Content-Length", 0) or 0)
        _Collector.received.append((self.headers.get("Authorization"), self.rfile.read(n)))
        self.send_response(200)
        self.end_headers()

    def log_message(self, *args):
        pass


@pytest.fixture
def collector():
    _Collector.received = []
    srv = HTTPServer(("127.0.0.1", 0), _Collector)
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    try:
        yield srv
    finally:
        srv.shutdown()


async def test_stream_event_delivers(collector, monkeypatch):
    port = collector.server_address[1]
    monkeypatch.setattr(
        "src.lians.siem.get_settings",
        lambda: SimpleNamespace(siem_url=f"http://127.0.0.1:{port}/intake", siem_token="Bearer tok"),
    )
    ok = await stream_event({"op": "add", "id": "evt-1"})
    assert ok is True
    assert len(_Collector.received) == 1
    auth, body = _Collector.received[0]
    assert auth == "Bearer tok"
    assert b"evt-1" in body and b"lians.audit" in body


async def test_stream_event_disabled_returns_false(monkeypatch):
    monkeypatch.setattr(
        "src.lians.siem.get_settings",
        lambda: SimpleNamespace(siem_url="", siem_token=""),
    )
    assert await stream_event({"op": "x"}) is False


async def test_stream_event_swallows_errors(monkeypatch):
    # Unroutable URL — must return False, never raise.
    monkeypatch.setattr(
        "src.lians.siem.get_settings",
        lambda: SimpleNamespace(siem_url="http://127.0.0.1:1/down", siem_token=""),
    )
    assert await stream_event({"op": "x"}) is False
