"""SDK retries only mutations backed by a transactional server claim.

This module lives under the public SDK project deliberately.  The deployable
server and client SDK are separate distributions that both own the top-level
``lians`` import and must never be imported in one test process.
"""
# ruff: noqa: E402
import sys
import threading
from datetime import UTC, datetime
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from typing import ClassVar

import httpx
import pytest

SDK_ROOT = Path(__file__).resolve().parents[1]
if str(SDK_ROOT) not in sys.path:
    sys.path.insert(0, str(SDK_ROOT))
from lians import AsyncLiansClient, LiansClient
from lians.client import _parse_retry_after


class _Handler(BaseHTTPRequestHandler):
    idem_keys: ClassVar[list] = []

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


async def test_transactionally_idempotent_add_retries_with_one_stable_key(server):
    port = server.server_address[1]
    client = AsyncLiansClient(
        base_url=f"http://127.0.0.1:{port}", api_key="k", backoff_factor=0.01
    )
    result = await client.add(
        agent_id="a",
        content="x",
        event_time=datetime(2026, 1, 1, tzinfo=UTC),
    )
    await client.aclose()
    assert result["id"] == "m-1"
    assert len(_Handler.idem_keys) == 2
    assert _Handler.idem_keys[0] is not None
    assert _Handler.idem_keys[0] == _Handler.idem_keys[1]


async def test_arbitrary_mutation_remains_non_retrying(server):
    port = server.server_address[1]
    client = AsyncLiansClient(
        base_url=f"http://127.0.0.1:{port}", api_key="k", backoff_factor=0.01
    )
    with pytest.raises(httpx.HTTPStatusError):
        await client._req("POST", "/unsafe", json={"value": 1})
    await client.aclose()
    assert len(_Handler.idem_keys) == 1


@pytest.mark.parametrize(
    ("argument", "value"),
    (
        ("timeout", 0),
        ("timeout", float("inf")),
        ("max_retries", -1),
        ("max_retries", 11),
        ("backoff_factor", -0.1),
        ("max_retry_delay", 0),
        ("max_retry_delay", 301),
    ),
)
def test_retry_configuration_is_finite_and_bounded(argument, value):
    with pytest.raises(ValueError):
        AsyncLiansClient(base_url="https://lians.test", **{argument: value})


@pytest.mark.parametrize(
    "base_url",
    (
        "lians.test",
        "ftp://lians.test",
        "https://user:secret@lians.test",
        "https://lians.test?tenant=hidden",
        "https://lians.test#fragment",
    ),
)
def test_base_url_rejects_ambiguous_or_credential_bearing_values(base_url):
    with pytest.raises(ValueError):
        AsyncLiansClient(base_url=base_url)


def test_empty_admin_secret_is_not_transmitted():
    client = AsyncLiansClient(base_url="https://lians.test")
    assert "X-Admin-Secret" not in client._admin_headers


def test_sync_client_forwards_bounded_retry_configuration():
    client = LiansClient(
        base_url="https://lians.test",
        max_retries=4,
        backoff_factor=1.5,
        max_retry_delay=12,
    )
    try:
        assert client._async._max_retries == 4
        assert client._async._backoff_factor == 1.5
        assert client._async._max_retry_delay == 12
    finally:
        client.close()


def test_retry_after_is_a_floor_and_excessive_floor_disables_retry(monkeypatch):
    monkeypatch.setattr("lians.client.random.random", lambda: 0.0)
    client = AsyncLiansClient(
        base_url="https://lians.test",
        backoff_factor=1,
        max_retry_delay=5,
    )

    assert client._retry_delay(0, "3") == 3
    assert client._retry_delay(0, "6") is None


def test_retry_after_parser_accepts_http_date_and_rejects_malformed_values():
    assert _parse_retry_after("0") == 0
    assert _parse_retry_after("Wed, 21 Oct 2015 07:28:00 GMT") == 0
    assert _parse_retry_after("not-a-delay") is None


async def test_nontransient_server_status_is_not_retried(monkeypatch):
    attempts = 0

    async def fake_request(self, method, url, **kwargs):
        nonlocal attempts
        attempts += 1
        return httpx.Response(
            501,
            json={"detail": "unsupported"},
            request=httpx.Request(method, url),
        )

    monkeypatch.setattr(httpx.AsyncClient, "request", fake_request)
    client = AsyncLiansClient(base_url="https://lians.test", max_retries=3)
    with pytest.raises(httpx.HTTPStatusError):
        await client._req("GET", "/unsupported")
    await client.aclose()

    assert attempts == 1


async def test_decision_methods_serialize_recording_cutoff_and_impact_agent(monkeypatch):
    requests: list[tuple[str, dict]] = []

    async def fake_request(self, method, url, **kwargs):
        requests.append((url, kwargs["json"]))
        return httpx.Response(200, json={"ok": True}, request=httpx.Request(method, url))

    monkeypatch.setattr(httpx.AsyncClient, "request", fake_request)
    client = AsyncLiansClient(base_url="https://lians.test", api_key="k")
    event_cutoff = datetime(2026, 7, 1, 14, 30, tzinfo=UTC)
    recorded_cutoff = datetime(2026, 7, 1, 14, 31, tzinfo=UTC)

    await client.record_decision(
        agent_id="underwriter",
        decision_type="credit_application",
        outcome="manual_review",
        decided_at=event_cutoff,
        knowledge_as_of=event_cutoff,
        knowledge_recorded_as_of=recorded_cutoff,
    )
    await client.assess_decision_impact(
        "policy", "credit-policy-17", agent_id="receipt-impact-monitor"
    )
    await client.aclose()

    assert requests[0][1]["knowledge_as_of"] == event_cutoff.isoformat()
    assert requests[0][1]["knowledge_recorded_as_of"] == recorded_cutoff.isoformat()
    assert requests[1][1]["agent_id"] == "receipt-impact-monitor"
