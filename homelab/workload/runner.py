"""Continuously produce a recall + OTLP + decision Evidence Pack proof."""

from __future__ import annotations

import json
import os
import secrets
import sys
import time
from pathlib import Path
from typing import Any
from urllib.parse import urlencode

from common import (
    atomic_write_bytes,
    atomic_write_json,
    emit,
    endpoint,
    env_bool,
    env_float,
    http_json,
    http_request,
    sha256_json,
    sha256_text,
    utc_now,
    wait_for_file,
    wait_for_http,
)

LIANS_URL = os.getenv("LIANS_URL", "http://lians:8000")
ALLOY_URL = os.getenv("ALLOY_URL", "http://alloy:12345")
OTLP_URL = os.getenv("OTLP_URL", "http://alloy:4318/v1/traces")
NAMESPACE = os.getenv("NAMESPACE", "lians-homelab")
AGENT_ID = os.getenv("AGENT_ID", "risk-demo")
STATE_DIR = Path(os.getenv("STATE_DIR", "/state"))
API_KEY_PATH = STATE_DIR / "api-key"
PROOF_PATH = STATE_DIR / "latest-proof.json"
READY_PATH = STATE_DIR / "ready"
STARTUP_TIMEOUT = env_float("STARTUP_TIMEOUT_SECONDS", 180.0, minimum=1.0)
EVIDENCE_TIMEOUT = env_float("EVIDENCE_TIMEOUT_SECONDS", 45.0, minimum=1.0)
RUN_INTERVAL = env_float("RUN_INTERVAL_SECONDS", 30.0, minimum=1.0)
RETRY_INTERVAL = env_float("RETRY_INTERVAL_SECONDS", 5.0, minimum=0.5)
RUN_ONCE = env_bool("RUN_ONCE")

QUERY = "What is the current NVDA counterparty exposure limit and why was it revised?"
OUTCOME = "approve_with_usd_35m_limit"


def api_headers(key: str) -> dict[str, str]:
    return {"X-API-Key": key}


def otlp_attribute(key: str, value: str) -> dict[str, Any]:
    return {"key": key, "value": {"stringValue": value}}


def otlp_string_array_attribute(key: str, values: list[str]) -> dict[str, Any]:
    return {
        "key": key,
        "value": {"arrayValue": {"values": [{"stringValue": value} for value in values]}},
    }


def build_otlp_payload(
    *,
    trace_id: str,
    span_id: str,
    envelope_id: str,
    receipt_sha256: str,
    started_ns: int,
    ended_ns: int,
) -> dict[str, Any]:
    """Build standards-shaped OTLP/HTTP JSON without an SDK dependency."""

    return {
        "resourceSpans": [
            {
                "resource": {
                    "attributes": [
                        otlp_attribute("service.name", "lians-homelab-risk-demo"),
                        otlp_attribute("service.namespace", NAMESPACE),
                        otlp_attribute("deployment.environment.name", "homelab"),
                    ]
                },
                "scopeSpans": [
                    {
                        "scope": {"name": "lians.homelab", "version": "1.0.0"},
                        "spans": [
                            {
                                "traceId": trace_id,
                                "spanId": span_id,
                                "name": "gen_ai.decision counterparty_exposure_review",
                                "kind": 3,
                                "startTimeUnixNano": str(started_ns),
                                "endTimeUnixNano": str(max(ended_ns, started_ns + 1)),
                                "attributes": [
                                    otlp_attribute("gen_ai.operation.name", "invoke_agent"),
                                    otlp_attribute("gen_ai.request.model", "lians-risk-demo-v1"),
                                    otlp_string_array_attribute(
                                        "gen_ai.response.finish_reasons", ["stop"]
                                    ),
                                    otlp_attribute(
                                        "lians.decision.type", "counterparty_exposure_review"
                                    ),
                                    otlp_attribute("lians.decision.envelope_id", envelope_id),
                                    otlp_attribute("lians.recall.receipt_sha256", receipt_sha256),
                                    otlp_attribute("lians.demo.scenario", "finance-risk-revision"),
                                ],
                                "status": {"code": 1},
                            }
                        ],
                    }
                ],
            }
        ]
    }


def wait_for_otel_evidence(key: str, envelope_id: str, trace_id: str) -> list[dict[str, Any]]:
    deadline = time.monotonic() + EVIDENCE_TIMEOUT
    last_evidence: list[dict[str, Any]] = []
    while time.monotonic() < deadline:
        response = http_json(
            "GET",
            endpoint(LIANS_URL, f"/v1/decision-envelopes/{envelope_id}/evidence"),
            headers=api_headers(key),
        )
        if isinstance(response, list):
            last_evidence = response
            if any(
                item.get("evidence_type") in {"otel_span", "otel_trace"}
                and str(item.get("source_id", "")).startswith(trace_id)
                for item in response
            ):
                return response
        time.sleep(0.5)
    evidence_types = sorted({str(item.get("evidence_type")) for item in last_evidence})
    raise TimeoutError(
        f"Alloy-forwarded trace {trace_id} was not linked to envelope; "
        f"observed evidence types: {evidence_types}"
    )


def run_decision(key: str) -> dict[str, Any]:
    trace_id = secrets.token_hex(16)
    span_id = secrets.token_hex(8)
    run_id = f"homelab-{trace_id[:12]}"
    knowledge_as_of = utc_now()
    started_ns = time.time_ns()

    envelope = http_json(
        "POST",
        endpoint(LIANS_URL, "/v1/decision-envelopes"),
        json_body={
            "agent_id": AGENT_ID,
            "decision_type": "counterparty_exposure_review",
            "regime": "enterprise_homelab",
            "subject_id": "NVDA",
            "session_id": run_id,
            "trace_id": trace_id,
            "run_id": run_id,
            "knowledge_as_of": knowledge_as_of,
            "completeness_profile": "regulated_recordkeeping",
            "metadata": {"scenario": "finance-risk-revision", "environment": "homelab"},
        },
        headers=api_headers(key),
    )
    envelope_id = str(envelope["id"])
    emit("envelope_opened", envelope_id=envelope_id, trace_id=trace_id)

    recall = http_json(
        "POST",
        endpoint(LIANS_URL, "/v1/recall"),
        json_body={
            "agent_id": AGENT_ID,
            "query": QUERY,
            "k": 5,
            "filters": {
                "ticker": "NVDA",
                "metric": "counterparty_exposure_limit",
            },
            "include_context": True,
            "mode": "reconstruct",
            "decision_envelope_id": envelope_id,
        },
        headers=api_headers(key),
    )
    memories = recall.get("memories", []) if isinstance(recall, dict) else []
    receipt_sha256 = recall.get("receipt_sha256", "") if isinstance(recall, dict) else ""
    if not memories or len(receipt_sha256) != 64:
        raise RuntimeError("bound recall did not return seeded memory evidence and a receipt")
    if not any("USD 35 million" in (memory.get("content") or "") for memory in memories):
        raise RuntimeError("bound recall did not select the active USD 35 million revision")
    emit(
        "recall_bound",
        envelope_id=envelope_id,
        memories=len(memories),
        receipt_sha256=receipt_sha256,
    )

    otlp_payload = build_otlp_payload(
        trace_id=trace_id,
        span_id=span_id,
        envelope_id=envelope_id,
        receipt_sha256=receipt_sha256,
        started_ns=started_ns,
        ended_ns=time.time_ns(),
    )
    otlp_status, _, _ = http_request(
        "POST",
        OTLP_URL,
        raw_body=json.dumps(otlp_payload, separators=(",", ":")).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        timeout=15.0,
    )
    evidence_before_seal = wait_for_otel_evidence(key, envelope_id, trace_id)
    emit("otel_linked", envelope_id=envelope_id, trace_id=trace_id, status=otlp_status)

    memory_ids = [str(memory["id"]) for memory in memories if memory.get("id")]
    seal_body = {
        "outcome": OUTCOME,
        "reason_codes": ["POLICY_LIMIT_REVISED", "VOLATILITY_REVIEW"],
        "decided_at": utc_now(),
        "knowledge_as_of": knowledge_as_of,
        "model_id": "lians-risk-demo-v1",
        "model_version": "1.0.0",
        "model_artifact_hash": sha256_text("lians-risk-demo-v1:1.0.0"),
        "policy_id": "counterparty-exposure-policy",
        "policy_version": "2.0.0",
        "policy_artifact_hash": sha256_text("counterparty-exposure-policy:2.0.0"),
        "prompt_id": "homelab-risk-review",
        "prompt_version": "1.0.0",
        "prompt_artifact_hash": sha256_text(QUERY),
        "runtime_version": "homelab-workload/1.0.0",
        "evidence_memory_ids": memory_ids,
        "input_hash": sha256_json(
            {"query": QUERY, "memory_ids": memory_ids, "receipt_sha256": receipt_sha256}
        ),
        "output_hash": sha256_text(OUTCOME),
        "replay_manifest_hash": sha256_json(
            {"trace_id": trace_id, "model": "lians-risk-demo-v1:1.0.0", "query": QUERY}
        ),
        "metadata": {"scenario": "finance-risk-revision", "trace_id": trace_id},
    }
    sealed = http_json(
        "POST",
        endpoint(LIANS_URL, f"/v1/decision-envelopes/{envelope_id}/seal"),
        json_body=seal_body,
        headers=api_headers(key),
    )
    decision_id = str(sealed["decision"]["id"])
    pack_url = endpoint(LIANS_URL, f"/v1/decisions/{decision_id}/evidence-pack")
    evidence_pack = http_json(
        "GET",
        f"{pack_url}?{urlencode({'version': 'v2'})}",
        headers=api_headers(key),
        timeout=30.0,
    )
    evidence_types = {
        item.get("evidence_type")
        for item in evidence_pack.get("evidence_graph", [])
        if isinstance(item, dict)
    }
    if "recall_receipt" not in evidence_types or not evidence_types.intersection(
        {"otel_span", "otel_trace"}
    ):
        raise RuntimeError(f"evidence pack is missing recall/OTLP evidence: {evidence_types}")

    proof = {
        "schema": "https://lians.ai/schemas/homelab-proof/v1",
        "generated_at": utc_now(),
        "namespace": NAMESPACE,
        "agent_id": AGENT_ID,
        "trace_id": trace_id,
        "span_id": span_id,
        "envelope_id": envelope_id,
        "decision_id": decision_id,
        "recall": recall,
        "alloy_export": {"endpoint": OTLP_URL, "status_code": otlp_status},
        "evidence_before_seal": evidence_before_seal,
        "seal": sealed,
        "evidence_pack": evidence_pack,
    }
    atomic_write_json(PROOF_PATH, proof)
    atomic_write_bytes(READY_PATH, f"{trace_id}\n".encode("ascii"), mode=0o644)
    emit(
        "proof_ready",
        trace_id=trace_id,
        decision_id=decision_id,
        grade=sealed.get("completeness", {}).get("grade"),
        proof_path=str(PROOF_PATH),
    )
    return proof


def main() -> int:
    try:
        STATE_DIR.mkdir(parents=True, exist_ok=True)
        # A named volume survives container replacement. Never let a previous
        # successful run make a new or broken workload look healthy.
        READY_PATH.unlink(missing_ok=True)
        PROOF_PATH.unlink(missing_ok=True)
        wait_for_http(endpoint(LIANS_URL, "/readyz"), "lians", timeout=STARTUP_TIMEOUT)
        wait_for_http(endpoint(ALLOY_URL, "/-/ready"), "alloy", timeout=STARTUP_TIMEOUT)
        wait_for_file(API_KEY_PATH, timeout=STARTUP_TIMEOUT)
        key = API_KEY_PATH.read_text(encoding="utf-8").strip()
        if not key:
            raise RuntimeError("API key file is empty")
    except Exception as exc:  # noqa: BLE001 - top-level container boundary
        emit("runner_start_failed", level="error", error=str(exc), error_type=type(exc).__name__)
        return 1

    while True:
        cycle_started = time.monotonic()
        try:
            run_decision(key)
            if RUN_ONCE:
                return 0
            time.sleep(max(0.0, RUN_INTERVAL - (time.monotonic() - cycle_started)))
        except KeyboardInterrupt:
            emit("runner_stopped")
            return 0
        except Exception as exc:  # noqa: BLE001 - a failed cycle should self-heal
            emit(
                "decision_cycle_failed",
                level="error",
                error=str(exc),
                error_type=type(exc).__name__,
            )
            if RUN_ONCE:
                return 1
            time.sleep(RETRY_INTERVAL)


if __name__ == "__main__":
    sys.exit(main())
