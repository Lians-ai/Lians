"""Synthetic end-to-end Universal Recorder + Gate + remediation example.

This example prints only identifiers, readiness, and dispositions. It never
prints credentials or captured payloads.
"""

from __future__ import annotations

import hashlib
import json
import os
from datetime import UTC, datetime
from uuid import uuid4

from lians import (
    LiansClient,
    a2a_event,
    lians_event,
    mcp_jsonrpc_event,
    otlp_genai_span,
)


def main() -> None:
    base_url = os.getenv("LIANS_URL", "http://localhost:8000")
    api_key = os.environ.get("LIANS_API_KEY", "")
    access_token = os.environ.get("LIANS_ACCESS_TOKEN", "")
    mediator_api_key = os.environ.get("LIANS_MEDIATOR_API_KEY", "")
    if not api_key and not access_token:
        raise SystemExit("Set LIANS_API_KEY or LIANS_ACCESS_TOKEN (the value is never printed).")
    if mediator_api_key and api_key and mediator_api_key == api_key:
        raise SystemExit("LIANS_MEDIATOR_API_KEY must be a separate credential.")

    now = datetime.now(UTC)
    suffix = uuid4().hex[:8]
    run_id = f"synthetic-order-review-{suffix}"
    trace_id = "0123456789abcdef0123456789abcdef"
    span_id = "0123456789abcdef"
    events = [
        lians_event(
            "decision.started",
            {
                "name": "synthetic-order-review",
                "phase": "started",
                "model_id": "synthetic-model",
                "policy_version": "quickstart-1",
                "input": {"order_id": "SYNTHETIC-001", "amount": 125},
                "evidence": ["synthetic-source:catalog-v1"],
            },
            run_id=run_id,
            trace_id=trace_id,
            span_id=span_id,
            agent_id="synthetic-review-agent",
            principal_id="synthetic-workload",
            occurred_at=now,
            idempotency_key=f"{run_id}:decision-started",
        ),
        otlp_genai_span(
            trace_id=trace_id,
            span_id="1123456789abcdef",
            parent_span_id=span_id,
            operation="chat",
            model="synthetic-model",
            input=[{"role": "user", "content": "Review synthetic order SYNTHETIC-001"}],
            output=[{"role": "assistant", "content": "Synthetic review completed"}],
            agent_id="synthetic-review-agent",
            run_id=run_id,
            occurred_at=now,
            ended_at=now,
        ),
        mcp_jsonrpc_event(
            {
                "jsonrpc": "2.0",
                "id": "synthetic-tool-call-1",
                "method": "tools/call",
                "params": {
                    "name": "synthetic_catalog_lookup",
                    "arguments": {"sku": "SYNTHETIC-SKU"},
                },
            },
            run_id=run_id,
            tool_name="synthetic_catalog_lookup",
            agent_id="synthetic-review-agent",
            occurred_at=now,
        ),
        a2a_event(
            {
                "kind": "task",
                "id": "synthetic-a2a-task-1",
                "contextId": run_id,
                "status": {"state": "completed", "timestamp": now.isoformat()},
                "artifacts": [{"name": "synthetic-review", "parts": [{"text": "approved"}]}],
            },
            run_id=run_id,
            agent_id="synthetic-review-agent",
        ),
    ]

    with LiansClient(
        base_url=base_url,
        api_key=api_key,
        access_token=access_token,
    ) as client:
        principal = client.whoami()
        if mediator_api_key:
            with LiansClient(base_url=base_url, api_key=mediator_api_key) as mediator:
                mediator_principal_id = mediator.whoami()["principal_id"]
        else:
            # This must be the canonical whoami principal of a separate
            # broker/sidecar credential. The zero UUID is intentionally
            # unredeemable when no mediator credential was configured.
            mediator_principal_id = os.getenv(
                "LIANS_MEDIATOR_PRINCIPAL_REF",
                "lians:principal:v1:api-key:00000000-0000-0000-0000-000000000000",
            )
        if mediator_principal_id == principal["principal_id"]:
            raise SystemExit("The evaluator and mediator must be separate identities.")
        capabilities = client.platform_capabilities()
        platform = client.platform_readiness()
        print(
            "platform",
            {
                "status": platform["status"],
                "recorder_version": capabilities["components"]["recorder"]["version"],
            },
        )
        batch = client.ingest_recorder_batch(events)
        recorder_run_id = batch["results"][0]["readiness"]["run_id"]
        readiness = client.recorder_run_readiness(recorder_run_id)
        print(
            "recorder",
            {
                "accepted": batch["accepted"],
                "score": readiness["score"],
                "ready": readiness["receipt_ready"],
            },
        )

        policy = client.create_gate_policy(
            {
                "name": f"synthetic-release-gate-{suffix}",
                "version": "quickstart-1",
                "default_disposition": "deny",
                "protected_actions": ["synthetic.order.release"],
                "target_ref_prefixes": ["urn:lians:synthetic-order:"],
                "enforcement_principal_ids": [mediator_principal_id],
                "maximum_permit_ttl_seconds": 30,
                "rules": [
                    {
                        "name": "require-recorded-policy",
                        "applies_to_risk_levels": ["high", "critical"],
                        "require_policy_attached": True,
                        "action_on_failure": "deny",
                    }
                ],
                "metadata": {"synthetic": True},
            }
        )
        policy = client.activate_gate_policy(policy["id"])
        decision = client.record_decision(
            agent_id="synthetic-review-agent",
            decision_type="order_release",
            outcome="approved",
            decided_at=now,
            policy_version="quickstart-1",
            metadata={"risk_level": "high", "synthetic": True},
        )
        provider_request = {
            "action": "synthetic.order.release",
            "decision_id": decision["id"],
            "target_ref": "urn:lians:synthetic-order:SYNTHETIC-001",
            "arguments": {"synthetic": True, "order_id": "SYNTHETIC-001"},
        }
        execution_request_hash = hashlib.sha256(
            json.dumps(
                provider_request,
                sort_keys=True,
                separators=(",", ":"),
                ensure_ascii=False,
            ).encode("utf-8")
        ).hexdigest()
        verdict = client.evaluate_gate(
            {
                "action": "synthetic.order.release",
                "target_ref": "urn:lians:synthetic-order:SYNTHETIC-001",
                "decision_id": decision["id"],
                "enforcement_principal_id": mediator_principal_id,
                "permit_ttl_seconds": 30,
                "execution_request_hash": execution_request_hash,
                "risk_level": "high",
                "policy_set_id": policy["id"],
                "context": {"synthetic": True, "recorder_run_id": recorder_run_id},
            }
        )
        permit = verdict.get("execution_permit")
        permit_consumed = False
        if permit is not None and mediator_api_key:
            # A real mediator derives these claims from the normalized request
            # it is about to dispatch and recomputes the digest independently.
            mediator_request_hash = hashlib.sha256(
                json.dumps(
                    provider_request,
                    sort_keys=True,
                    separators=(",", ":"),
                    ensure_ascii=False,
                ).encode("utf-8")
            ).hexdigest()
            with LiansClient(base_url=base_url, api_key=mediator_api_key) as mediator:
                mediator.consume_gate_execution_permit(
                    {
                        "permit_id": permit["permit_id"],
                        "token": permit["token"],
                        "action": provider_request["action"],
                        "target_ref": provider_request["target_ref"],
                        "decision_id": provider_request["decision_id"],
                        "execution_request_hash": mediator_request_hash,
                    }
                )
            permit_consumed = True
        print(
            "gate",
            {
                "principal": principal["principal_id"],
                "disposition": verdict["disposition"],
                "permit_id": permit["permit_id"] if permit is not None else None,
                "permit_consumed": permit_consumed,
            },
        )

        case = client.create_investigation_case(
            {
                "title": "Synthetic control-plane exercise",
                "description": "Synthetic data only; validates owned remediation flow.",
                "severity": "low",
                "gate_decision_id": verdict["id"],
                "metadata": {"synthetic": True},
            }
        )
        task = client.create_remediation_task(
            case["id"],
            {
                "expected_case_updated_at": case["updated_at"],
                "title": "Attest synthetic evidence review",
                "description": "No customer or production data is used.",
                "owner_principal": principal["principal_id"],
                "metadata": {"synthetic": True},
            },
        )
        task_in_progress = client.update_remediation_task(
            task["id"],
            {
                "expected_updated_at": task["updated_at"],
                "status": "in_progress",
            },
        )
        task_closed = client.close_remediation_task(
            task["id"],
            {
                "expected_updated_at": task_in_progress["updated_at"],
                "statement": "Synthetic evidence review completed.",
                "evidence_refs": [f"gate:{verdict['id']}", f"recorder-run:{recorder_run_id}"],
            },
        )
        case_before_close = client.investigation_case(case["id"])
        case_closed = client.close_investigation_case(
            case["id"],
            {
                "expected_updated_at": case_before_close["updated_at"],
                "statement": "Synthetic exercise closed after the owned task was attested.",
                "evidence_refs": [f"attestation:{task_closed['attestation']['id']}"],
                "resolution_summary": "Synthetic control-plane path completed.",
            },
        )
        print(
            "investigation",
            {"case_id": case["id"], "status": case_closed["status"]},
        )


if __name__ == "__main__":
    main()
