"""Verify the homelab control plane and its latest end-to-end proof artifact."""

from __future__ import annotations

import base64
import json
import os
import sys
import time
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import quote, urlencode

from common import (
    HttpFailure,
    atomic_write_json,
    endpoint,
    env_float,
    http_json,
    http_request,
    sha256_json,
    utc_now,
)
from scenario import LoadedScenario, load_scenario

LIANS_URL = os.getenv("LIANS_URL", "http://lians:8000")
PROMETHEUS_URL = os.getenv("PROMETHEUS_URL", "http://prometheus:9090")
PROMETHEUS_JOB = os.getenv("PROMETHEUS_JOB", "lians")
GRAFANA_URL = os.getenv("GRAFANA_URL", "http://grafana:3000")
GRAFANA_ADMIN_USER = os.getenv("GRAFANA_ADMIN_USER", "admin")
GRAFANA_ADMIN_PASSWORD = os.getenv("GRAFANA_ADMIN_PASSWORD", "lians-local-only")
TEMPO_URL = os.getenv("TEMPO_URL", "http://tempo:3200")
LOKI_URL = os.getenv("LOKI_URL", "http://loki:3100")
STATE_DIR = Path(os.getenv("STATE_DIR", "/state"))
PROOF_PATH = STATE_DIR / "latest-proof.json"
ARTIFACTS_DIR = Path(os.getenv("ARTIFACTS_DIR", "/artifacts"))
SAMPLE_PATH = Path(os.getenv("SAMPLE_PATH", "/sample/input.json"))
VERIFY_TIMEOUT = env_float("VERIFY_TIMEOUT_SECONDS", 45.0, minimum=1.0)
PROOF_MAX_AGE = env_float("PROOF_MAX_AGE_SECONDS", 180.0, minimum=1.0)
EXPECTED_COMPLETENESS_GRADE = os.getenv("EXPECTED_COMPLETENESS_GRADE", "replayable")
LAB_GIT_COMMIT = os.getenv("LAB_GIT_COMMIT", "unrecorded")
COMPONENT_IMAGES = {
    name.removeprefix("LAB_IMAGE_").lower(): value
    for name, value in os.environ.items()
    if name.startswith("LAB_IMAGE_") and value
}


class CheckFailure(RuntimeError):
    pass


def require(condition: bool, message: str) -> None:
    if not condition:
        raise CheckFailure(message)


def verify_mounted_sample_manifest(
    proof_sample: Any,
    sample_path: Path,
    *,
    acknowledgement: str | None = None,
) -> LoadedScenario:
    mounted_sample = load_scenario(sample_path, acknowledgement=acknowledgement)
    require(isinstance(proof_sample, dict), "proof does not contain a sample manifest")
    require(
        proof_sample == mounted_sample.manifest,
        "proof sample manifest does not match the independently validated mounted sample",
    )
    return mounted_sample


def check_lians() -> dict[str, Any]:
    health = http_json("GET", endpoint(LIANS_URL, "/health"))
    require(isinstance(health, dict), "Lians health response is not an object")
    checks = health.get("checks", {})
    require(not checks or checks.get("db") == "ok", f"Lians database is not healthy: {checks}")
    _, _, metrics_raw = http_request("GET", endpoint(LIANS_URL, "/metrics"))
    metrics = metrics_raw.decode("utf-8", errors="replace")
    require(
        "agentmem_memory_recalls_total" in metrics,
        "Lians metrics do not expose the recall counter",
    )
    return {"status": health.get("status"), "metrics_bytes": len(metrics_raw)}


def check_prometheus() -> dict[str, Any]:
    targets = http_json("GET", endpoint(PROMETHEUS_URL, "/api/v1/targets"))
    require(targets.get("status") == "success", "Prometheus target API failed")
    active = targets.get("data", {}).get("activeTargets", [])
    matching = [
        target
        for target in active
        if target.get("labels", {}).get("job") == PROMETHEUS_JOB
        or "lians" in str(target.get("scrapeUrl", "")).lower()
    ]
    require(matching, f"Prometheus has no active target for job {PROMETHEUS_JOB!r}")
    require(
        any(target.get("health") == "up" for target in matching),
        f"Prometheus Lians target is not up: {[target.get('health') for target in matching]}",
    )
    query = f'up{{job="{PROMETHEUS_JOB}"}}'
    query_url = endpoint(PROMETHEUS_URL, "/api/v1/query") + "?" + urlencode({"query": query})
    result = http_json("GET", query_url)
    series = result.get("data", {}).get("result", [])
    require(result.get("status") == "success" and series, "Prometheus Lians up query is empty")
    require(any(float(item["value"][1]) == 1.0 for item in series), "Prometheus reports Lians down")
    recall_url = (
        endpoint(PROMETHEUS_URL, "/api/v1/query")
        + "?"
        + urlencode({"query": "agentmem_memory_recalls_total"})
    )
    recalls = http_json("GET", recall_url)
    recall_series = recalls.get("data", {}).get("result", [])
    require(
        recalls.get("status") == "success" and recall_series,
        "Prometheus has no Lians recall metric data",
    )
    return {
        "matching_targets": len(matching),
        "up_series": len(series),
        "recall_series": len(recall_series),
    }


def check_grafana() -> dict[str, Any]:
    health = http_json("GET", endpoint(GRAFANA_URL, "/api/health"))
    require(isinstance(health, dict), "Grafana health response is not an object")
    require(health.get("database") == "ok", f"Grafana database is not healthy: {health}")

    token = base64.b64encode(f"{GRAFANA_ADMIN_USER}:{GRAFANA_ADMIN_PASSWORD}".encode()).decode(
        "ascii"
    )
    auth_headers = {"Authorization": f"Basic {token}"}
    datasources = http_json(
        "GET",
        endpoint(GRAFANA_URL, "/api/datasources"),
        headers=auth_headers,
    )
    require(isinstance(datasources, list), "Grafana datasource response is not a list")
    datasource_uids = {item.get("uid") for item in datasources if isinstance(item, dict)}
    expected_uids = {"prometheus", "tempo", "loki"}
    require(
        expected_uids <= datasource_uids,
        f"Grafana is missing provisioned datasources: {sorted(expected_uids - datasource_uids)}",
    )
    dashboard = http_json(
        "GET",
        endpoint(GRAFANA_URL, "/api/dashboards/uid/lians-homelab-proof"),
        headers=auth_headers,
    )
    require(isinstance(dashboard, dict), "Grafana dashboard response is not an object")
    require(
        dashboard.get("dashboard", {}).get("uid") == "lians-homelab-proof",
        "Grafana did not provision the Lians homelab dashboard",
    )
    bundled_dashboard = http_json(
        "GET",
        endpoint(GRAFANA_URL, "/api/dashboards/uid/lians-operations"),
        headers=auth_headers,
    )
    require(
        bundled_dashboard.get("dashboard", {}).get("uid") == "lians-operations",
        "Grafana did not load the dashboard bundled with the Lians app",
    )
    plugin = http_json(
        "GET",
        endpoint(GRAFANA_URL, "/api/plugins/lians-lians-app/settings"),
        headers=auth_headers,
    )
    require(isinstance(plugin, dict), "Grafana plugin settings response is not an object")
    require(plugin.get("id") == "lians-lians-app", "Grafana did not install the Lians app")
    require(plugin.get("enabled") is True, "Grafana did not enable the Lians app")
    return {
        "database": health.get("database"),
        "version": health.get("version"),
        "datasources": sorted(expected_uids),
        "dashboard_uid": "lians-homelab-proof",
        "bundled_dashboard_uid": "lians-operations",
        "plugin_id": "lians-lians-app",
        "plugin_enabled": True,
    }


def eventually(operation: Callable[[], Any], *, timeout: float) -> Any:
    deadline = time.monotonic() + timeout
    last_error: Exception | None = None
    while time.monotonic() < deadline:
        try:
            return operation()
        except (HttpFailure, CheckFailure) as exc:
            last_error = exc
            time.sleep(1.0)
    raise CheckFailure(str(last_error or "timed out"))


def check_tempo(trace_id: str) -> dict[str, Any]:
    _, _, ready_raw = http_request("GET", endpoint(TEMPO_URL, "/ready"))
    require("ready" in ready_raw.decode("utf-8", errors="replace").lower(), "Tempo is not ready")

    search_state = "ok"
    try:
        search = http_json("GET", endpoint(TEMPO_URL, "/api/search") + "?limit=20")
        require(isinstance(search, dict), "Tempo search response is not an object")
    except HttpFailure as exc:
        if exc.status == 404:
            search_state = "unsupported"
        else:
            raise

    def fetch_trace() -> dict[str, Any]:
        trace = http_json(
            "GET",
            endpoint(TEMPO_URL, f"/api/traces/{quote(trace_id, safe='')}"),
            headers={"Accept": "application/json"},
        )
        require(isinstance(trace, dict) and trace, f"Tempo returned no data for trace {trace_id}")
        return trace

    trace = eventually(fetch_trace, timeout=VERIFY_TIMEOUT)
    return {"ready": True, "search": search_state, "trace_bytes": len(json.dumps(trace))}


def check_loki() -> dict[str, Any]:
    _, _, body = http_request("GET", endpoint(LOKI_URL, "/ready"))
    text = body.decode("utf-8", errors="replace")
    require("ready" in text.lower(), f"Loki readiness response was unexpected: {text[:100]}")
    end_ns = time.time_ns()
    query_url = (
        endpoint(LOKI_URL, "/loki/api/v1/query_range")
        + "?"
        + urlencode(
            {
                "query": '{lab="lians-homelab",service=~"lians|workload"}',
                "start": str(end_ns - 10 * 60 * 1_000_000_000),
                "end": str(end_ns),
                "limit": "20",
                "direction": "backward",
            }
        )
    )
    result = http_json("GET", query_url)
    streams = result.get("data", {}).get("result", [])
    require(result.get("status") == "success" and streams, "Loki has no recent Lians/workload logs")
    return {"ready": True, "recent_streams": len(streams)}


def load_and_check_proof() -> tuple[dict[str, Any], dict[str, Any]]:
    require(PROOF_PATH.is_file(), f"proof artifact does not exist: {PROOF_PATH}")
    try:
        proof = json.loads(PROOF_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise CheckFailure(f"proof artifact is unreadable: {exc}") from exc
    require(proof.get("schema", "").endswith("homelab-proof/v1"), "unexpected proof schema")
    generated_at = str(proof.get("generated_at", ""))
    try:
        generated = datetime.fromisoformat(generated_at)
    except ValueError as exc:
        raise CheckFailure("proof generated_at is not a valid timestamp") from exc
    age_seconds = (datetime.now(UTC) - generated.astimezone(UTC)).total_seconds()
    require(age_seconds >= -5.0, "proof timestamp is unexpectedly in the future")
    require(
        age_seconds <= PROOF_MAX_AGE,
        f"proof is stale ({age_seconds:.1f}s old; maximum is {PROOF_MAX_AGE:.1f}s)",
    )
    trace_id = proof.get("trace_id", "")
    require(len(trace_id) == 32, "proof does not contain a valid trace id")

    sample = proof.get("sample")
    verify_mounted_sample_manifest(
        sample,
        SAMPLE_PATH,
        acknowledgement=os.getenv("LAB_SAMPLE_POLICY_ACK"),
    )
    require(
        sample.get("schema") == "https://lians.ai/schemas/homelab-sample/v1",
        "proof sample schema is unsupported",
    )
    require(
        sample.get("classification") in {"synthetic", "deidentified"},
        "proof sample classification is invalid",
    )
    require(len(str(sample.get("sample_sha256", ""))) == 64, "proof sample hash is invalid")
    require(
        isinstance(sample.get("memory_count"), int) and sample["memory_count"] > 0,
        "proof sample memory count is invalid",
    )

    recall = proof.get("recall")
    require(isinstance(recall, dict), "proof does not contain the bound recall")
    require(recall.get("memories"), "proof recall contains no memories")
    require(len(recall.get("receipt_sha256", "")) == 64, "proof recall receipt is invalid")

    pack = proof.get("evidence_pack")
    require(isinstance(pack, dict), "proof does not contain an evidence pack")
    require(pack.get("schema", "").endswith("evidence-pack/v2"), "proof is not Evidence Pack v2")
    require(len(str(pack.get("manifest_hash", ""))) == 64, "evidence manifest hash is invalid")
    require(len(str(pack.get("pack_hash", ""))) == 64, "evidence pack hash is invalid")
    manifest = {
        key: value
        for key, value in pack.items()
        if key not in {"manifest_hash", "signature", "pack_hash"}
    }
    require(
        sha256_json(manifest) == pack.get("manifest_hash"),
        "evidence manifest hash does not match canonical contents",
    )
    pack_without_hash = {key: value for key, value in pack.items() if key != "pack_hash"}
    require(
        sha256_json(pack_without_hash) == pack.get("pack_hash"),
        "evidence pack hash does not match canonical contents",
    )
    require(
        pack.get("decision", {}).get("id") == proof.get("decision_id"), "pack decision ID mismatch"
    )
    require(
        pack.get("envelope", {}).get("id") == proof.get("envelope_id"), "pack envelope ID mismatch"
    )
    require(
        pack.get("envelope", {}).get("decision_id") == proof.get("decision_id"),
        "pack envelope is not linked to the reported decision",
    )
    require(pack.get("envelope", {}).get("status") == "sealed", "pack envelope is not sealed")
    grade = pack.get("completeness", {}).get("grade")
    require(
        grade == EXPECTED_COMPLETENESS_GRADE,
        f"evidence completeness grade is {grade!r}, expected {EXPECTED_COMPLETENESS_GRADE!r}",
    )
    graph = pack.get("evidence_graph", [])
    evidence_types = {item.get("evidence_type") for item in graph if isinstance(item, dict)}
    require("recall_receipt" in evidence_types, "evidence pack lacks recall receipt evidence")
    otel_items = [
        item
        for item in graph
        if isinstance(item, dict) and item.get("evidence_type") in {"otel_span", "otel_trace"}
    ]
    require(otel_items, "evidence pack lacks OTLP evidence")
    require(
        any(str(item.get("source_id", "")).startswith(trace_id) for item in otel_items),
        "OTLP evidence is not correlated to the proof trace",
    )
    require(
        pack.get("envelope", {}).get("trace_id") == trace_id,
        "evidence-pack envelope trace does not match the proof",
    )
    evidence_sources = [
        {
            "evidence_type": str(item.get("evidence_type")),
            "source_id": str(item.get("source_id")),
        }
        for item in graph
        if isinstance(item, dict) and item.get("evidence_type") and item.get("source_id")
    ]
    detail = {
        "trace_id": trace_id,
        "decision_id": proof.get("decision_id"),
        "envelope_id": proof.get("envelope_id"),
        "proof_age_seconds": round(age_seconds, 3),
        "memories": len(recall["memories"]),
        "evidence_types": sorted(str(item) for item in evidence_types),
        "evidence_sources": evidence_sources,
        "evidence_pack_schema": pack.get("schema"),
        "manifest_hash": pack.get("manifest_hash"),
        "pack_hash": pack.get("pack_hash"),
        "signature_status": pack.get("signature", {}).get("status"),
        "grade": grade,
        "sample": {
            "schema": sample.get("schema"),
            "classification": sample.get("classification"),
            "scenario_id": sample.get("scenario_id"),
            "sample_sha256": sample.get("sample_sha256"),
            "memory_count": sample.get("memory_count"),
            "decision_type": sample.get("decision_type"),
        },
    }
    return proof, detail


def main() -> int:
    report: dict[str, Any] = {
        "ts": utc_now(),
        "event": "homelab_verification",
        "ok": False,
        "git_commit": LAB_GIT_COMMIT,
        "component_images": COMPONENT_IMAGES,
        "checks": {},
    }
    proof: dict[str, Any] | None = None
    proof_detail: dict[str, Any] | None = None
    checks: list[tuple[str, Callable[[], Any]]] = [
        ("proof", lambda: load_and_check_proof()),
        ("lians", lambda: eventually(check_lians, timeout=VERIFY_TIMEOUT)),
        ("prometheus", lambda: eventually(check_prometheus, timeout=VERIFY_TIMEOUT)),
        ("grafana", lambda: eventually(check_grafana, timeout=VERIFY_TIMEOUT)),
        ("loki", lambda: eventually(check_loki, timeout=VERIFY_TIMEOUT)),
    ]
    failed = False
    for name, operation in checks:
        try:
            value = operation()
            if name == "proof":
                proof, proof_detail = value
                report["checks"][name] = {"ok": True, **proof_detail}
            else:
                report["checks"][name] = {"ok": True, **value}
        except Exception as exc:  # noqa: BLE001 - aggregate all independent checks
            failed = True
            report["checks"][name] = {
                "ok": False,
                "error": str(exc),
                "error_type": type(exc).__name__,
            }

    try:
        require(proof is not None, "Tempo trace check requires a valid proof")
        report["checks"]["tempo"] = {
            "ok": True,
            **eventually(lambda: check_tempo(proof["trace_id"]), timeout=VERIFY_TIMEOUT),
        }
    except Exception as exc:  # noqa: BLE001 - final independent check
        failed = True
        report["checks"]["tempo"] = {
            "ok": False,
            "error": str(exc),
            "error_type": type(exc).__name__,
        }

    report["ok"] = not failed
    stamp = report["ts"].replace("-", "").replace(":", "").replace(".", "")
    latest_path = ARTIFACTS_DIR / "latest-verification.json"
    latest_receipt_path = ARTIFACTS_DIR / "latest-receipt.json"
    receipt_path = ARTIFACTS_DIR / f"verification-{stamp}.json"
    report["checks"]["artifacts"] = {
        "ok": True,
        "latest": str(latest_path),
        "latest_receipt": str(latest_receipt_path),
        "receipt": str(receipt_path),
    }
    receipt = {
        "schema": "https://lians.ai/schemas/homelab-verification-receipt/v1",
        "generated_at": report["ts"],
        "ok": report["ok"],
        "trace_id": proof.get("trace_id") if proof else None,
        "decision_id": proof.get("decision_id") if proof else None,
        "envelope_id": proof.get("envelope_id") if proof else None,
        "git_commit": LAB_GIT_COMMIT,
        "component_images": COMPONENT_IMAGES,
        "sample": proof_detail.get("sample") if proof_detail else None,
        "evidence": proof_detail,
        "checks": {name: bool(detail.get("ok")) for name, detail in report["checks"].items()},
    }
    try:
        atomic_write_json(receipt_path, receipt)
        atomic_write_json(latest_receipt_path, receipt)
        atomic_write_json(latest_path, report)
    except Exception as exc:  # noqa: BLE001 - artifact export is part of verification
        failed = True
        report["ok"] = False
        report["checks"]["artifacts"] = {
            "ok": False,
            "error": str(exc),
            "error_type": type(exc).__name__,
        }
        receipt["ok"] = False
        receipt["checks"]["artifacts"] = False
        for path, value in (
            (receipt_path, receipt),
            (latest_receipt_path, receipt),
            (latest_path, report),
        ):
            try:
                atomic_write_json(path, value)
            except OSError as retry_exc:
                report["checks"]["artifacts"].setdefault("retry_errors", []).append(str(retry_exc))
    print(json.dumps(report, sort_keys=True, separators=(",", ":")), flush=True)
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
