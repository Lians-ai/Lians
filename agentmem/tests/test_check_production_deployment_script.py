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


def test_validate_health_requires_database() -> None:
    MODULE.validate_health(
        response(200, {"status": "ok", "checks": {"db": "ok", "redis": "disabled"}}),
        "Health",
    )
    with pytest.raises(RuntimeError):
        MODULE.validate_health(
            response(200, {"status": "degraded", "checks": {"db": "ok"}}),
            "Health",
        )


def test_validate_openapi_requires_release_paths() -> None:
    MODULE.validate_openapi(
        response(200, {"paths": {path: {} for path in MODULE.REQUIRED_OPENAPI_PATHS}})
    )
    with pytest.raises(RuntimeError, match="/v1/decision-envelopes"):
        MODULE.validate_openapi(response(200, {"paths": {}}))
