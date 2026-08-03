#!/usr/bin/env python3
"""Reject mutable container references in checked-in deployment examples."""

from __future__ import annotations

import re
import tomllib
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

import yaml

ROOT = Path(__file__).resolve().parents[2]
SCAN_ROOTS = (ROOT / "k8s", ROOT / "deploy/gate-mediator")
DIGEST_REFERENCE = re.compile(r"^[^\s@]+@sha256:[0-9a-f]{64}$")
DIGEST_VALUE = re.compile(r"^sha256:[0-9a-f]{64}$")
COMPOSE_REQUIRED_IMAGE = re.compile(r"^\$\{LIANS_IMAGE:\?[^}]+digest[^}]*}$")
WORKER_PREFIXES = (
    "IMPACT_ASSESSMENT_WORKER",
    "RECORDER_EVIDENCE_INDEX_WORKER",
    "SUBJECT_ERASURE_WORKER",
    "SCIM_RECONCILIATION_WORKER",
)
WORKER_FIELDS = (
    "ENABLED",
    "POLL_SECONDS",
    "BATCH_SIZE",
    "CONCURRENCY",
    "LEASE_SECONDS",
    "PAGE_SIZE",
    "MAX_PAGES_PER_CLAIM",
    "RETRY_BASE_SECONDS",
    "RETRY_MAX_SECONDS",
    "MAX_ATTEMPTS",
)
REQUIRED_RUNTIME_ENVIRONMENT = {
    "DEPLOYMENT_ENVIRONMENT": "production",
    "API_SURFACE": "public",
    "RATE_LIMIT_BACKEND_FAILURE_MODE": "deny",
    "PRODUCTION_ALLOW_LOCAL_DATA_SERVICE_SOCKETS": "false",
}


def _check_worker_contract(
    environment: Mapping[str, Any], *, logical: str, violations: list[str]
) -> None:
    for prefix in WORKER_PREFIXES:
        missing = [
            f"{prefix}_{field}"
            for field in WORKER_FIELDS
            if f"{prefix}_{field}" not in environment
        ]
        if missing:
            violations.append(f"{logical}: missing worker settings {missing}")
        if environment.get(f"{prefix}_ENABLED") != "true":
            violations.append(f"{logical}: {prefix}_ENABLED must be true")


def _check_fail_closed_environment(
    environment: Mapping[str, Any], *, logical: str, violations: list[str]
) -> None:
    for key, expected in REQUIRED_RUNTIME_ENVIRONMENT.items():
        if environment.get(key) != expected:
            violations.append(f"{logical}: {key} must be {expected!r}")
    for key in (
        "DATABASE_POOL_SIZE",
        "DATABASE_MAX_OVERFLOW",
        "DATABASE_POOL_TIMEOUT_SECONDS",
        "DATABASE_STATEMENT_TIMEOUT_MS",
        "DATABASE_LOCK_TIMEOUT_MS",
        "DATABASE_IDLE_TRANSACTION_TIMEOUT_MS",
        "RECEIPT_SIGNING_PROVIDER",
    ):
        if key not in environment:
            violations.append(f"{logical}: missing explicit {key}")


def _walk(value: Any, *, path: Path, logical: str, violations: list[str]) -> None:
    if isinstance(value, Mapping):
        for key, child in value.items():
            location = f"{logical}.{key}" if logical else str(key)
            if key == "image" and isinstance(child, str):
                if not (
                    DIGEST_REFERENCE.fullmatch(child)
                    or COMPOSE_REQUIRED_IMAGE.fullmatch(child)
                ):
                    violations.append(
                        f"{path.relative_to(ROOT).as_posix()}:{location}: {child!r}"
                    )
            if key == "images" and isinstance(child, Sequence):
                for index, entry in enumerate(child):
                    if not isinstance(entry, Mapping):
                        continue
                    digest = entry.get("digest")
                    if not isinstance(digest, str) or not DIGEST_VALUE.fullmatch(digest):
                        violations.append(
                            f"{path.relative_to(ROOT).as_posix()}:{location}[{index}] "
                            "must use a sha256 digest"
                        )
                    if entry.get("newTag") is not None:
                        violations.append(
                            f"{path.relative_to(ROOT).as_posix()}:{location}[{index}] "
                            "must not use newTag"
                        )
            _walk(child, path=path, logical=location, violations=violations)
    elif isinstance(value, Sequence) and not isinstance(value, (str, bytes)):
        for index, child in enumerate(value):
            _walk(
                child,
                path=path,
                logical=f"{logical}[{index}]",
                violations=violations,
            )


def main() -> None:
    files = sorted(
        path
        for root in SCAN_ROOTS
        for pattern in ("*.yaml", "*.yml")
        for path in root.rglob(pattern)
    )
    violations: list[str] = []
    for path in files:
        for index, document in enumerate(
            yaml.safe_load_all(path.read_text(encoding="utf-8"))
        ):
            _walk(
                document,
                path=path,
                logical=f"document[{index}]",
                violations=violations,
            )
    fly_path = ROOT / "fly.toml"
    fly = tomllib.loads(fly_path.read_text(encoding="utf-8"))
    if "build" in fly:
        violations.append("fly.toml must not permit an ad-hoc source build")
    if isinstance(fly.get("deploy"), Mapping) and fly["deploy"].get("release_command"):
        violations.append("fly.toml must not run migrations with runtime credentials")
    fly_environment = fly.get("env")
    if not isinstance(fly_environment, Mapping):
        violations.append("fly.toml must define an explicit production environment")
    else:
        _check_worker_contract(
            fly_environment,
            logical="fly.toml:env",
            violations=violations,
        )
        _check_fail_closed_environment(
            fly_environment,
            logical="fly.toml:env",
            violations=violations,
        )
    render_path = ROOT / "render.yaml"
    render = yaml.safe_load(render_path.read_text(encoding="utf-8"))
    for index, service in enumerate(render.get("services", [])):
        if not isinstance(service, Mapping):
            continue
        logical = f"render.yaml:services[{index}]"
        runtime = service.get("runtime")
        if runtime != "image" and service.get("autoDeployTrigger") != "off":
            violations.append(
                f"{logical}: source-built services must keep autoDeployTrigger off"
            )
        if runtime == "image":
            image_url = service.get("image", {}).get("url")
            if not isinstance(image_url, str) or not DIGEST_REFERENCE.fullmatch(
                image_url
            ):
                violations.append(f"{logical}: image.url must be digest-pinned")
        env = {
            item.get("key"): item.get("value")
            for item in service.get("envVars", [])
            if isinstance(item, Mapping)
        }
        if env.get("API_SURFACE") != "public":
            violations.append(f"{logical}: public service must set API_SURFACE=public")
        if "ADMIN_SECRET" in env:
            violations.append(f"{logical}: public service must not receive ADMIN_SECRET")
        _check_worker_contract(env, logical=f"{logical}:envVars", violations=violations)
        _check_fail_closed_environment(
            env,
            logical=f"{logical}:envVars",
            violations=violations,
        )
    if violations:
        print("Checked-in deployment images must be digest-pinned or fail closed:")
        for violation in violations:
            print(f"  {violation}")
        raise SystemExit(1)
    print(
        f"immutable image guard passed across {len(files)} deployment manifests "
        "plus Fly's image-only and Render's source-build-disabled postures"
    )


if __name__ == "__main__":
    main()
