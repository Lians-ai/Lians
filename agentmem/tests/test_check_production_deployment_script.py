from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest

SCRIPT_PATH = Path(__file__).parents[2] / "scripts" / "check_production_deployment.py"
WORKFLOW_PATH = Path(__file__).parents[2] / ".github" / "workflows" / "fly-deploy.yml"
DOCKERFILE_PATH = Path(__file__).parents[2] / "Dockerfile"
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


def test_fly_deploy_preserves_exact_image_and_build_evidence_contract() -> None:
    workflow = WORKFLOW_PATH.read_text(encoding="utf-8")
    assert '--image-label "$image_label"' in workflow
    assert '--build-arg "LIANS_BUILD_SHA=$GITHUB_SHA"' in workflow
    assert 'select_fly_production_machine.py \\\n' in workflow
    assert '--expected-build-sha "$GITHUB_SHA"' in workflow


def test_build_sha_does_not_invalidate_heavy_runtime_layers() -> None:
    dockerfile = DOCKERFILE_PATH.read_text(encoding="utf-8")
    assert dockerfile.index("RUN chown -R lians:lians") < dockerfile.index(
        "ARG LIANS_BUILD_SHA=unknown"
    )
    assert dockerfile.index("ARG LIANS_BUILD_SHA=unknown") < dockerfile.index(
        'ENV LIANS_BUILD_SHA="${LIANS_BUILD_SHA}"'
    )


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
    sha = "a" * 40
    responses = {
        "/livez": response(200, {"status": "alive"}),
        "/health": response(200, {"status": "ok"}),
        "/readyz": response(200, {"status": "ok"}),
        "/version": response(
            200,
            {
                "schema": "lians.deployment-evidence.v1",
                "version": "0.5.0",
                "build_sha": sha,
                "openapi_sha256": "b" * 64,
            },
        ),
        "/docs": response(404, {"detail": "Not Found"}),
        "/openapi.json": response(404, {"detail": "Not Found"}),
        "/v1/decision-envelopes": response(401, {"detail": "Unauthorized"}),
    }
    requested: list[str] = []

    def fake_request(_base_url: str, path: str, **_kwargs):
        requested.append(path)
        return responses[path]

    monkeypatch.setattr(MODULE, "request", fake_request)
    result = MODULE.run("https://api.example.com", expected_build_sha=sha)

    assert requested == list(responses)
    assert result["authentication_boundary"] == "ok"
    assert result["documentation_boundary"] == "ok"
    assert result["deployment_evidence"]["build_sha"] == sha


def test_run_rejects_exposed_openapi(monkeypatch) -> None:
    responses = {
        "/livez": response(200, {"status": "alive"}),
        "/health": response(200, {"status": "ok"}),
        "/readyz": response(200, {"status": "ok"}),
        "/version": response(
            200,
            {
                "schema": "lians.deployment-evidence.v1",
                "version": "0.5.0",
                "build_sha": "a" * 40,
                "openapi_sha256": "b" * 64,
            },
        ),
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


def test_validate_version_binds_build_and_openapi_digests() -> None:
    sha = "a" * 40
    payload = {
        "schema": "lians.deployment-evidence.v1",
        "version": "0.5.0",
        "build_sha": sha,
        "openapi_sha256": "b" * 64,
    }
    assert MODULE.validate_version(response(200, payload), sha)["build_sha"] == sha
    with pytest.raises(RuntimeError, match="does not match expected"):
        MODULE.validate_version(response(200, payload), "c" * 40)
    with pytest.raises(RuntimeError, match="exact build commit"):
        MODULE.validate_version(response(200, {**payload, "build_sha": "unknown"}))
