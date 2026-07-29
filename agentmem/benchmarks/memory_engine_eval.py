"""Deterministic end-to-end gate for the governed memory engine.

This gate proves local engine contracts, not competitive SOTA. It performs no
network or paid model calls and emits one machine-readable JSON document.
"""
from __future__ import annotations

import hashlib
import json
import os
import statistics
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "sdk" / "python"))
sys.path.insert(0, str(ROOT))

os.environ["EMBEDDING_PROVIDER"] = "local"
os.environ["RECALL_CACHE_ENABLED"] = "false"
os.environ["AGENTMEM_ALLOW_UNENCRYPTED"] = "true"
os.environ["MASTER_ENCRYPTION_KEY"] = ""
os.environ["RLS_BARRIERS_ENABLED"] = "false"

from lians import LocalLiansClient  # noqa: E402
from src.lians.memory_compiler import METADATA_KEY, classify_memory  # noqa: E402

T0 = datetime(2026, 1, 1, tzinfo=timezone.utc)
T1 = T0 + timedelta(days=30)


def _check(name: str, passed: bool, detail: dict) -> dict:
    return {"name": name, "passed": bool(passed), "detail": detail}


def main() -> int:
    checks: list[dict] = []
    classifier_cases = {
        "preference": "Alice prefers concise answers.",
        "procedure": "First open the incident, then export the evidence.",
        "policy": "Policy requires human approval before deployment.",
        "outcome": "The migration completed and reduced latency.",
        "relationship": "Alice reports to Bob.",
        "episode": "Yesterday we investigated the timeout.",
        "reflection": "Lesson learned: validate the index before cutover.",
        "fact": "The database engine is PostgreSQL.",
    }
    predicted = {
        expected: classify_memory(text)[0]
        for expected, text in classifier_cases.items()
    }
    classifier_accuracy = sum(
        expected == actual for expected, actual in predicted.items()
    ) / len(classifier_cases)
    checks.append(_check(
        "typed_memory_compilation",
        classifier_accuracy == 1.0,
        {"accuracy": classifier_accuracy, "predictions": predicted},
    ))

    latencies: dict[str, list[float]] = {"fast": [], "deep": [], "reconstruct": []}
    with LocalLiansClient(namespace="memory-engine-gate", embedding_provider="local") as client:
        old = client.add(
            "engine",
            "ACME policy version is v1.",
            T0,
            source="policy://acme/v1",
            metadata={"entity": "ACME", "metric": "policy_version"},
        )
        new = client.add(
            "engine",
            "ACME policy version is v2.",
            T1,
            source="policy://acme/v2",
            metadata={"entity": "ACME", "metric": "policy_version"},
        )
        preference = client.add(
            "engine",
            "Alice prefers concise incident reports.",
            T1,
            source="conversation://turn/7",
        )

        compiled = preference["metadata"].get(METADATA_KEY, {})
        checks.append(_check(
            "lossless_compiler_provenance",
            compiled.get("kind") == "preference"
            and compiled.get("source", {}).get("content_sha256") == preference["content_hash"],
            {"compiled": compiled},
        ))

        results: dict[str, dict] = {}
        for mode, query, as_of in (
            ("fast", "What report style does Alice prefer?", None),
            ("deep", "What changed in the policy and what does Alice prefer?", None),
            ("reconstruct", "What was the ACME policy version?", T0 + timedelta(days=1)),
        ):
            for _ in range(3):
                started = time.perf_counter()
                result = client.recall(
                    "engine",
                    query,
                    k=10,
                    as_of=as_of,
                    mode=mode,
                )
                latencies[mode].append((time.perf_counter() - started) * 1000)
            results[mode] = result

        past_ids = {memory["id"] for memory in results["reconstruct"]["memories"]}
        checks.append(_check(
            "point_in_time_reconstruction",
            str(old["id"]) in past_ids and str(new["id"]) not in past_ids,
            {
                "old_id": str(old["id"]),
                "new_id": str(new["id"]),
                "returned_ids": sorted(past_ids),
            },
        ))
        checks.append(_check(
            "explicit_serving_modes",
            results["fast"]["mode"] == "fast"
            and results["deep"]["mode"] == "deep"
            and results["reconstruct"]["mode"] == "reconstruct"
            and results["deep"]["strategy"] == "adaptive"
            and results["reconstruct"]["strategy"] == "adaptive",
            {
                mode: {
                    "strategy": result["strategy"],
                    "variants": result["query_variants"],
                    "latency_budget_ms": result["latency_budget_ms"],
                }
                for mode, result in results.items()
            },
        ))
        checks.append(_check(
            "content_addressed_recall_receipts",
            all(
                len(result.get("receipt_sha256", "")) == 64
                and result.get("provenance_coverage") == 1.0
                and hashlib.sha256(
                    json.dumps(
                        result.get("receipt", {}),
                        sort_keys=True,
                        separators=(",", ":"),
                        default=str,
                    ).encode()
                ).hexdigest() == result.get("receipt_sha256")
                for result in results.values()
            ),
            {
                mode: {
                    "receipt_sha256": result.get("receipt_sha256"),
                    "provenance_coverage": result.get("provenance_coverage"),
                }
                for mode, result in results.items()
            },
        ))

    latency_summary = {
        mode: {
            "p50_ms": round(statistics.median(values), 3),
            "p95_ms": round(sorted(values)[max(0, int(len(values) * 0.95) - 1)], 3),
        }
        for mode, values in latencies.items()
    }
    report = {
        "benchmark": "lians-memory-engine-contract-v1",
        "environment": "ephemeral SQLite; deterministic local embeddings",
        "network_or_paid_judges": False,
        "scope": "functional engine contracts, not production load or competitive SOTA",
        "checks": checks,
        "passed": sum(check["passed"] for check in checks),
        "total": len(checks),
        "metrics": {
            "compiler_accuracy": classifier_accuracy,
            "provenance_coverage": min(
                results[mode]["provenance_coverage"] for mode in results
            ),
            "latency": latency_summary,
        },
    }
    print(json.dumps(report, indent=2, default=str))
    return 0 if report["passed"] == report["total"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
