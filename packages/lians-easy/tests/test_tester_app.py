from __future__ import annotations

import hashlib
import json
import threading
from collections.abc import Iterator
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen

import pytest
from lians_easy.agent_experiment import AgentExperimentError
from lians_easy.tester_app import TesterApplication as LocalTesterApplication

PACKAGE_ROOT = Path(__file__).resolve().parents[1]


def fake_report() -> dict[str, object]:
    def run(passed: bool = True) -> dict[str, object]:
        return {"quality": {"passed": passed}}

    return {
        "status": "completed",
        "provider": "claude",
        "measurement": {
            "label": "Claude CLI reported input tokens",
        },
        "fixture": {"name": "synthetic-test"},
        "auth": {
            "logged_in": True,
            "auth_method": "claude.ai",
            "provider": "firstParty",
        },
        "results": {
            "full_replay": {
                "average_provider_reported_total_input_tokens": 12000.0,
                "runs": [run(), run()],
            },
            "lians_bounded": {
                "average_provider_reported_total_input_tokens": 3000.0,
                "runs": [run(), run()],
            },
        },
        "comparison": {
            "provider_reported_input_token_reduction_percent": 75.0,
        },
        "evidence_gate": {"met": True},
    }


@pytest.fixture
def running_app() -> Iterator[tuple[LocalTesterApplication, str]]:
    def preflight(provider: str) -> dict[str, object]:
        assert provider == "claude"
        return {
            "logged_in": True,
            "auth_method": "claude.ai",
            "provider": "firstParty",
            "executable": "C:/private/claude.exe",
            "email": "private@example.com",
        }

    def runner(**kwargs: object) -> dict[str, object]:
        assert kwargs == {
            "provider": "claude",
            "scenario": "market-research",
            "repetitions": 2,
        }
        return fake_report()

    app = LocalTesterApplication(
        token="fixed-test-token-123456",
        preflight=preflight,
        experiment_runner=runner,
        auto_close_seconds=0,
    )
    thread = threading.Thread(target=app.serve_forever, daemon=True)
    thread.start()
    try:
        yield app, app.base_url
    finally:
        app.shutdown()
        thread.join(timeout=3)


def read(url: str) -> tuple[int, dict[str, str], bytes]:
    with urlopen(url, timeout=3) as response:
        return response.status, dict(response.headers), response.read()


def post(
    base_url: str,
    route: str,
    *,
    payload: dict[str, object] | None = None,
    origin: str | None = None,
) -> dict[str, object]:
    request = Request(
        base_url + route,
        data=json.dumps(payload or {}).encode("utf-8"),
        method="POST",
        headers={
            "Content-Type": "application/json",
            "Origin": origin or base_url.rstrip("/"),
        },
    )
    with urlopen(request, timeout=3) as response:
        return json.loads(response.read())


def test_tester_uses_the_product_theme_and_plain_large_copy() -> None:
    files_to_check = [
        PACKAGE_ROOT / "lians_easy" / "tester" / "index.html",
        PACKAGE_ROOT / "lians_easy" / "tester" / "style.css",
        PACKAGE_ROOT / "lians_easy" / "tester" / "app.js",
        PACKAGE_ROOT / "lians_easy" / "tester_app.py",
        PACKAGE_ROOT / "tester-package" / "START-HERE.html",
    ]
    combined = "\n".join(path.read_text(encoding="utf-8") for path in files_to_check)
    css = files_to_check[1].read_text(encoding="utf-8")
    guide = files_to_check[-1].read_text(encoding="utf-8")

    assert "#3777ff" in combined
    assert "#05070b" in combined
    assert "#ffffff" in combined
    assert 'font-family: "Sora"' in css
    assert 'font-family: "Sora"' in guide
    assert "clamp(48px, 8vw, 88px)" in css
    assert "<h1>See how much context Lians can cut</h1>" in combined
    assert "<h1>Test Lians with your AI account</h1>" in combined
    assert "eyebrow" not in combined.lower()
    assert "\N{EM DASH}" not in combined


def test_assets_and_status_are_local_and_do_not_leak_private_auth_fields(
    running_app: tuple[LocalTesterApplication, str],
) -> None:
    _, base_url = running_app
    expected_content_types = {
        "": "text/html; charset=utf-8",
        "style.css": "text/css; charset=utf-8",
        "app.js": "text/javascript; charset=utf-8",
        "wordmark.png": "image/png",
        "favicon.png": "image/png",
        "sora.woff2": "font/woff2",
    }
    for route, expected_content_type in expected_content_types.items():
        status, headers, payload = read(base_url + route)
        assert status == 200
        assert payload
        assert headers["Content-Type"] == expected_content_type
        assert "\r" not in headers["Content-Type"]
        assert "\n" not in headers["Content-Type"]
        assert headers["Cache-Control"] == "no-store"
        assert "default-src 'self'" in headers["Content-Security-Policy"]

    with pytest.raises(HTTPError) as injected:
        read(base_url + "favicon.png%0d%0aX-Injected:%20true")
    assert injected.value.code == 404

    _, _, payload = read(base_url + "api/status?provider=claude")
    status_payload = json.loads(payload)
    assert status_payload == {
        "ready": True,
        "auth_method": "claude.ai",
        "provider": "firstParty",
        "provider_name": "Claude",
        "message": "Claude account is ready",
    }
    assert b"private@example.com" not in payload
    assert b"claude.exe" not in payload

    _, _, wordmark = read(base_url + "wordmark.png")
    _, _, favicon = read(base_url + "favicon.png")
    assert hashlib.sha256(wordmark).hexdigest() == (
        "51495b5fc3e9dd339e5d2a5d4f4ae4c82f703c7d2ded21254d087c36b836cd4d"
    )
    assert hashlib.sha256(favicon).hexdigest() == (
        "8c01e301e8c9a775f2bece5027cffcbb043d94c286bb10b2a6986ef9e4edb4f6"
    )


def test_run_returns_a_small_summary_and_downloads_the_full_report(
    running_app: tuple[LocalTesterApplication, str],
) -> None:
    _, base_url = running_app
    result = post(base_url, "api/run", payload={"provider": "claude"})

    assert result == {
        "provider": "claude",
        "provider_name": "Claude",
        "measurement_label": "Claude CLI reported input tokens",
        "reduction_percent": 75.0,
        "full_input_tokens": 12000.0,
        "lians_input_tokens": 3000.0,
        "saved_input_tokens": 9000.0,
        "exact_answers": 4,
        "total_answers": 4,
        "gate_met": True,
    }
    _, headers, payload = read(base_url + "api/report")
    assert "attachment" in headers["Content-Disposition"]
    assert json.loads(payload)["fixture"]["name"] == "synthetic-test"


def test_wrong_session_path_and_cross_origin_posts_are_refused(
    running_app: tuple[LocalTesterApplication, str],
) -> None:
    app, base_url = running_app
    wrong_url = base_url.replace(app.token, "wrong-session-token-1234")
    with pytest.raises(HTTPError) as missing:
        read(wrong_url)
    assert missing.value.code == 404

    with pytest.raises(HTTPError) as refused:
        post(base_url, "api/run", origin="https://example.com")
    assert refused.value.code == 403


def test_preflight_failure_returns_a_safe_readiness_result() -> None:
    secret = "never-return-this-secret"

    def preflight(provider: str) -> dict[str, object]:
        assert provider == "claude"
        raise AgentExperimentError("Claude Code is not signed in")

    app = LocalTesterApplication(
        token="failure-test-token-1234",
        preflight=preflight,
        auto_close_seconds=0,
    )
    thread = threading.Thread(target=app.serve_forever, daemon=True)
    thread.start()
    try:
        _, _, payload = read(app.base_url + "api/status?provider=claude")
        assert json.loads(payload) == {
            "ready": False,
            "auth_method": None,
            "provider": None,
            "provider_name": "Claude",
            "message": "Claude Code is not signed in",
        }
        assert secret.encode() not in payload
    finally:
        app.shutdown()
        thread.join(timeout=3)
