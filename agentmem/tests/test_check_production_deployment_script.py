from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest

SCRIPT_PATH = Path(__file__).parents[2] / "scripts" / "check_production_deployment.py"
SPEC = importlib.util.spec_from_file_location("check_production_deployment", SCRIPT_PATH)
assert SPEC is not None
assert SPEC.loader is not None
MODULE = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = MODULE
SPEC.loader.exec_module(MODULE)


def response(status: int, payload: object) -> object:
    return MODULE.Response(
        status=status,
        body=json.dumps(payload).encode(),
        content_type="application/json",
    )


def test_validate_base_url_requires_secret_free_https() -> None:
    assert MODULE.validate_base_url("https://api.example.com") == "https://api.example.com/"
    for value in (
        "http://api.example.com",
        "https://user:secret@api.example.com",
        "https://api.example.com?token=secret",
    ):
        with pytest.raises(ValueError):
            MODULE.validate_base_url(value)


def test_validate_health_requires_sanitized_success() -> None:
    MODULE.validate_health(response(200, {"status": "ok"}), "Health")
    for payload in (
        {"status": "degraded"},
        {"status": "ok", "checks": {"db": "ok", "redis": "disabled"}},
    ):
        with pytest.raises(RuntimeError, match="sanitized healthy response"):
            MODULE.validate_health(response(200, payload), "Health")


def test_validate_hidden_requires_not_found() -> None:
    MODULE.validate_hidden(response(404, {"detail": "Not Found"}), "OpenAPI")
    with pytest.raises(RuntimeError, match="publicly exposed"):
        MODULE.validate_hidden(response(200, {"openapi": "3.1.0"}), "OpenAPI")


def test_run_validates_hardened_production_contract(monkeypatch) -> None:
    responses = {
        "/livez": response(200, {"status": "alive"}),
        "/health": response(200, {"status": "ok"}),
        "/readyz": response(200, {"status": "ok"}),
        "/docs": response(404, {"detail": "Not Found"}),
        "/openapi.json": response(404, {"detail": "Not Found"}),
        "/v1/decision-envelopes": response(401, {"detail": "Unauthorized"}),
    }
    requested: list[str] = []

    def fake_request(_base_url: str, path: str, **_kwargs):
        requested.append(path)
        return responses[path]

    monkeypatch.setattr(MODULE, "request", fake_request)
    result = MODULE.run("https://api.example.com")

    assert requested == list(responses)
    assert result["authentication_boundary"] == "ok"
    assert result["documentation_boundary"] == "ok"


def test_run_rejects_exposed_openapi(monkeypatch) -> None:
    responses = {
        "/livez": response(200, {"status": "alive"}),
        "/health": response(200, {"status": "ok"}),
        "/readyz": response(200, {"status": "ok"}),
        "/docs": response(404, {"detail": "Not Found"}),
        "/openapi.json": response(200, {"openapi": "3.1.0"}),
    }
    monkeypatch.setattr(
        MODULE,
        "request",
        lambda _base_url, path, **_kwargs: responses[path],
    )
    with pytest.raises(RuntimeError, match="OpenAPI was publicly exposed"):
        MODULE.run("https://api.example.com")


def test_validate_health_rejects_non_200() -> None:
    with pytest.raises(RuntimeError, match="HTTP 503"):
        MODULE.validate_health(
            response(503, {"status": "degraded"}),
            "Health",
        )
