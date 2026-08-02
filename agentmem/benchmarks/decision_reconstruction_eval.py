"""Pitch-ready benchmark for audit-grade AI decision reconstruction.

This benchmark exercises the public Lians API against an ephemeral SQLite
database. It proves functional correctness; its latency result is a local
development measurement, not a production throughput claim.

Run from the repository root:

    python agentmem/benchmarks/decision_reconstruction_eval.py
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from statistics import quantiles
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

# Keep the benchmark deterministic and offline.
os.environ.setdefault("EMBEDDING_PROVIDER", "local")
os.environ.setdefault("MASTER_ENCRYPTION_KEY", "")
os.environ.setdefault("KMS_PROVIDER", "env")
os.environ.setdefault("AGENTMEM_ALLOW_UNENCRYPTED", "true")
os.environ.setdefault("RLS_BARRIERS_ENABLED", "false")
os.environ.setdefault("RECALL_CACHE_ENABLED", "false")

from httpx import ASGITransport, AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from src.lians.config import get_settings
from src.lians.db import get_db
from src.lians.main import app
from src.lians.models import Base, EventLog


NAMESPACE = "riad-benchmark"
AGENT = "underwriting-agent"
SESSION = "loan-2026-0042"


def _iso(day: int, hour: int = 12) -> str:
    return datetime(2026, 7, day, hour, tzinfo=timezone.utc).isoformat()


def _sha(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def _canonical(value: dict[str, Any]) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), default=str)


async def _checked(response, expected: int = 200) -> dict[str, Any]:
    if response.status_code != expected:
        raise RuntimeError(
            f"{response.request.method} {response.request.url.path} returned "
            f"{response.status_code}: {response.text}"
        )
    return response.json()


async def run_benchmark(repetitions: int = 10) -> dict[str, Any]:
    engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )

    # The vector index is PostgreSQL-only.
    pg_indexes = [
        index
        for table in Base.metadata.tables.values()
        for index in list(table.indexes)
        if index.dialect_kwargs.get("postgresql_using") is not None
    ]
    for index in pg_indexes:
        index.table.indexes.discard(index)

    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)

    sessions = async_sessionmaker(engine, expire_on_commit=False)

    async def benchmark_db():
        async with sessions() as session:
            yield session

    app.dependency_overrides[get_db] = benchmark_db

    try:
        async with AsyncClient(
            transport=ASGITransport(app=app),
            base_url="http://benchmark",
        ) as client:
            provisioned = await _checked(
                await client.post(
                    "/v1/admin/api-keys",
                    json={
                        "namespace": NAMESPACE,
                        "scopes": ["read", "write", "admin"],
                        "label": "riad-ephemeral-benchmark",
                    },
                    headers={"X-Admin-Secret": get_settings().admin_secret},
                ),
                expected=201,
            )
            headers = {"X-API-Key": provisioned["key"]}
            client.headers.update(headers)

            evidence_payloads = [
                {
                    "content": "Applicant verified annual income is USD 120000.",
                    "source": "verified-income-service",
                    "event_time": _iso(20),
                    "metadata": {"field": "annual_income", "value": 120000},
                },
                {
                    "content": "Applicant debt-to-income ratio is 31 percent.",
                    "source": "credit-policy-engine",
                    "event_time": _iso(21),
                    "metadata": {"field": "dti", "value": 0.31},
                },
            ]
            evidence = []
            for payload in evidence_payloads:
                response = await client.post(
                    "/v1/memories", json={"agent_id": AGENT, **payload}
                )
                evidence.append(await _checked(response))

            # This fact was learned after the decision and must not leak backward.
            future = await client.post(
                "/v1/memories",
                json={
                    "agent_id": AGENT,
                    "content": "Applicant missed a payment after the decision.",
                    "source": "servicing-system",
                    "event_time": _iso(24),
                    "metadata": {"field": "payment_status", "value": "late"},
                },
            )
            future_memory = await _checked(future)

            trace_id = "0123456789abcdef0123456789abcdef"
            span_id = "0123456789abcdef"
            otlp = {
                "resourceSpans": [{
                    "resource": {"attributes": [{
                        "key": "service.name", "value": {"stringValue": AGENT}
                    }]},
                    "scopeSpans": [{"scope": {"name": "riad-benchmark"}, "spans": [{
                        "traceId": trace_id,
                        "spanId": span_id,
                        "name": "chat completion",
                        "startTimeUnixNano": "1784822400000000000",
                        "endTimeUnixNano": "1784822400500000000",
                        "attributes": [
                            {"key": "gen_ai.request.model",
                             "value": {"stringValue": "benchmark-model"}},
                            {"key": "session.id", "value": {"stringValue": SESSION}},
                        ],
                    }]}],
                }]
            }
            trace_result = await _checked(
                await client.post(
                    "/v1/traces",
                    json=otlp,
                    headers={**headers, "Content-Type": "application/json"},
                )
            )

            decision_payload = {
                "agent_id": AGENT,
                "decision_type": "credit_approval",
                "outcome": "approved",
                "reason_codes": ["income_verified", "dti_within_policy"],
                "regime": "ECOA",
                "subject_id": "applicant-0042",
                "session_id": SESSION,
                "model_id": "benchmark-model",
                "model_version": "2026-07-01",
                "policy_version": "credit-policy-17",
                "decided_at": _iso(23),
                "knowledge_as_of": _iso(23),
                "evidence_memory_ids": [item["id"] for item in evidence],
                "input_hash": _sha("normalized application input"),
                "output_hash": _sha("approved"),
                "metadata": {"trace_id": trace_id, "span_id": span_id},
            }
            decision = await _checked(
                await client.post("/v1/decisions", json=decision_payload)
            )

            first_pack = await _checked(
                await client.get(f"/v1/decisions/{decision['id']}/evidence-pack")
            )

            snapshot_ids = {
                item["id"] for item in first_pack["knowledge_snapshot"]
            }
            cited_ids = {item["id"] for item in first_pack["cited_evidence"]}
            expected_ids = {item["id"] for item in evidence}
            exact_reconstruction = (
                expected_ids.issubset(snapshot_ids)
                and future_memory["id"] not in snapshot_ids
                and cited_ids == expected_ids
            )

            required_provenance = {
                "agent_id": decision["agent_id"],
                "session_id": decision["session_id"],
                "model_id": decision["model_id"],
                "model_version": decision["model_version"],
                "policy_version": decision["policy_version"],
                "decided_at": decision["decided_at"],
                "knowledge_as_of": decision["knowledge_as_of"],
                "evidence_memory_ids": decision["evidence_memory_ids"],
                "input_hash": decision["input_hash"],
                "output_hash": decision["output_hash"],
            }
            present = sum(value not in (None, "", []) for value in required_provenance.values())
            provenance_coverage = present / len(required_provenance)

            unsigned = dict(first_pack)
            observed_pack_hash = unsigned.pop("pack_hash")
            pack_hash_valid = _sha(_canonical(unsigned)) == observed_pack_hash

            latencies_ms = []
            for _ in range(repetitions):
                started = time.perf_counter()
                await _checked(
                    await client.get(
                        f"/v1/decisions/{decision['id']}/evidence-pack",
                        params={"verify": "false"},
                    )
                )
                latencies_ms.append((time.perf_counter() - started) * 1000)
            p95_ms = (
                quantiles(latencies_ms, n=20, method="inclusive")[18]
                if len(latencies_ms) > 1
                else latencies_ms[0]
            )

            # Deliberately corrupt the audit payload, then require verification
            # to detect it. The database is ephemeral.
            async with sessions() as tamper_session:
                event = (
                    await tamper_session.execute(
                        select(EventLog)
                        .where(EventLog.namespace == NAMESPACE)
                        .order_by(EventLog.created_at)
                        .limit(1)
                    )
                ).scalar_one()
                event.payload = {**(event.payload or {}), "tampered": True}
                await tamper_session.commit()

            tampered_pack = await _checked(
                await client.get(f"/v1/decisions/{decision['id']}/evidence-pack")
            )
            tamper_detected = tampered_pack["audit_chain"]["status"] == "tampered"

            checks = {
                "exact_point_in_time_reconstruction": exact_reconstruction,
                "provenance_coverage_100_percent": provenance_coverage == 1.0,
                "evidence_pack_hash_valid": pack_hash_valid,
                "otlp_genai_span_accepted": trace_result["acceptedSpans"] == 1,
                "audit_tamper_detected": tamper_detected,
                "local_p95_under_3000_ms": p95_ms < 3000,
            }
            return {
                "benchmark": "RIAD-1",
                "environment": "ephemeral SQLite; local functional benchmark",
                "checks": checks,
                "passed": sum(checks.values()),
                "total": len(checks),
                "metrics": {
                    "reconstruction_accuracy": 1.0 if exact_reconstruction else 0.0,
                    "provenance_coverage": provenance_coverage,
                    "tamper_detection_rate": 1.0 if tamper_detected else 0.0,
                    "evidence_pack_p95_ms": round(p95_ms, 2),
                    "latency_repetitions": repetitions,
                },
                "limitations": [
                    "Latency is local SQLite and is not a production load test.",
                    "This run proves tamper-evidence, not certified WORM storage or legal attestation.",
                    "Historical v1 audit rows exclude payload; new v2 rows hash canonical JSON payload.",
                    "The GenAI span is retained and correlated by IDs in decision metadata; "
                    "automatic trace-to-decision linking is not claimed.",
                ],
            }
    finally:
        app.dependency_overrides.pop(get_db, None)
        await engine.dispose()


def main() -> None:
    report = asyncio.run(run_benchmark())
    print(json.dumps(report, indent=2))
    if report["passed"] != report["total"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
