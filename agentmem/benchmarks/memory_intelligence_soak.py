"""Deterministic multi-tenant isolation and concurrent-recall soak."""
from __future__ import annotations

import argparse
import json
import statistics
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "sdk" / "python"))
sys.path.insert(0, str(ROOT))

from lians import LocalLiansClient  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--tenants", type=int, default=8)
    parser.add_argument("--memories", type=int, default=100)
    parser.add_argument("--workers", type=int, default=8)
    parser.add_argument("--db", default=str(ROOT / "results" / "intelligence-soak.db"))
    parser.add_argument(
        "--out", default=str(ROOT / "results" / "intelligence-soak.json"),
    )
    args = parser.parse_args()
    if args.tenants < 1 or args.memories < 1 or args.workers < 1:
        parser.error("--tenants, --memories, and --workers must be positive")
    db = Path(args.db)
    db.parent.mkdir(parents=True, exist_ok=True)
    if db.exists():
        db.unlink()

    now = datetime.now(timezone.utc)
    sentinels: dict[str, str] = {}
    for tenant_index in range(args.tenants):
        namespace = f"tenant-{tenant_index:02d}"
        sentinel = f"PRIVATE-{tenant_index:02d}-ALPHA"
        sentinels[namespace] = sentinel
        with LocalLiansClient(
            db_path=str(db), namespace=namespace, embedding_provider="local",
        ) as client:
            items = [
                {
                    "content": f"{namespace} operational note {i}",
                    "event_time": now.isoformat(),
                    "metadata": {"tenant_index": tenant_index, "note": i},
                }
                for i in range(args.memories - 1)
            ]
            items.append({
                "content": f"The private account sentinel is {sentinel}.",
                "event_time": now.isoformat(),
                "metadata": {"kind": "sentinel"},
            })
            client.add_batch("shared-agent-id", items)

    def probe(namespace: str) -> dict:
        with LocalLiansClient(
            db_path=str(db), namespace=namespace, embedding_provider="local",
        ) as client:
            started = time.perf_counter()
            result = client.recall(
                agent_id="shared-agent-id",
                query="What is the private account sentinel?",
                k=10,
                strategy="adaptive",
            )
            recall_latency_ms = (time.perf_counter() - started) * 1000
            contents = [str(item.get("content") or "") for item in result["memories"]]
            own = sentinels[namespace]
            foreign = [
                value for other, value in sentinels.items()
                if other != namespace and any(value in content for content in contents)
            ]
            matching = next(
                item for item in result["memories"]
                if own in str(item.get("content") or "")
            )
            client.feedback(
                matching["id"], agent_id="shared-agent-id",
                signal="helpful", outcome="soak_probe",
            )
            summary = client.learning_summary(agent_id="shared-agent-id")
        return {
            "namespace": namespace,
            "own_found": any(own in content for content in contents),
            "foreign_leaks": foreign,
            "feedback_total": summary["total_feedback"],
            "latency_ms": recall_latency_ms,
        }

    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        probes = list(pool.map(probe, sentinels))
    latencies = sorted(item["latency_ms"] for item in probes)
    report = {
        "tenants": args.tenants,
        "memories_per_tenant": args.memories,
        "total_memories": args.tenants * args.memories,
        "workers": args.workers,
        "tenant_recall_success": sum(item["own_found"] for item in probes) / len(probes),
        "cross_tenant_leaks": sum(len(item["foreign_leaks"]) for item in probes),
        "feedback_isolated": all(item["feedback_total"] == 1 for item in probes),
        "latency_p50_ms": round(statistics.median(latencies), 2),
        "latency_p95_ms": round(latencies[min(len(latencies) - 1, int(len(latencies) * 0.95))], 2),
        "detail": probes,
    }
    output = Path(args.out)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(json.dumps({key: value for key, value in report.items() if key != "detail"}, indent=2))
    if (
        report["tenant_recall_success"] != 1.0
        or report["cross_tenant_leaks"]
        or not report["feedback_isolated"]
    ):
        raise SystemExit(1)


if __name__ == "__main__":
    main()
