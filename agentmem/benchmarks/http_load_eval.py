"""Concurrent HTTP load gate for a running Lians recall deployment.

The API key is accepted only through ``--api-key-file`` and is never printed.
Queries are read from JSONL objects with a required ``query`` field, or from a
small built-in smoke set when no file is supplied.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import math
import statistics
import time
from collections import Counter
from pathlib import Path

import httpx

DEFAULT_QUERIES = (
    "What does the user prefer?",
    "What changed recently?",
    "What workflow should the agent follow?",
    "What was known at the time?",
)
DEFAULT_P95_MS = {"fast": 100.0, "deep": 800.0, "reconstruct": 2000.0}


def percentile(values: list[float], quantile: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    index = max(0, min(len(ordered) - 1, math.ceil(quantile * len(ordered)) - 1))
    return ordered[index]


def summarize(samples: list[dict], mode: str) -> dict:
    latencies = [float(sample["latency_ms"]) for sample in samples]
    statuses = Counter(str(sample["status"]) for sample in samples)
    successes = [sample for sample in samples if sample["status"] == 200]
    return {
        "mode": mode,
        "requests": len(samples),
        "success_rate": round(len(successes) / len(samples), 6) if samples else 0.0,
        "p50_ms": round(statistics.median(latencies), 3) if latencies else 0.0,
        "p95_ms": round(percentile(latencies, 0.95), 3),
        "p99_ms": round(percentile(latencies, 0.99), 3),
        "status_counts": dict(statuses),
        "deadline_exceeded_rate": round(
            sum(bool(sample.get("deadline_exceeded")) for sample in successes)
            / len(successes),
            6,
        ) if successes else 0.0,
        "mean_provenance_coverage": round(
            statistics.fmean(
                float(sample.get("provenance_coverage", 0.0))
                for sample in successes
            ),
            6,
        ) if successes else 0.0,
        "receipt_coverage": round(
            sum(len(str(sample.get("receipt_sha256", ""))) == 64 for sample in successes)
            / len(successes),
            6,
        ) if successes else 0.0,
    }


def apply_gate(
    report: dict,
    *,
    p95_ms: float,
    min_success_rate: float = 1.0,
    min_provenance_coverage: float = 1.0,
    min_receipt_coverage: float = 1.0,
    max_deadline_exceeded_rate: float = 0.0,
) -> dict:
    thresholds = {
        "p95_ms": p95_ms,
        "min_success_rate": min_success_rate,
        "min_provenance_coverage": min_provenance_coverage,
        "min_receipt_coverage": min_receipt_coverage,
        "max_deadline_exceeded_rate": max_deadline_exceeded_rate,
    }
    checks = {
        "p95": report["p95_ms"] <= p95_ms,
        "success_rate": report["success_rate"] >= min_success_rate,
        "provenance_coverage": (
            report["mean_provenance_coverage"] >= min_provenance_coverage
        ),
        "receipt_coverage": report["receipt_coverage"] >= min_receipt_coverage,
        "deadline_exceeded_rate": (
            report["deadline_exceeded_rate"] <= max_deadline_exceeded_rate
        ),
    }
    return {**report, "thresholds": thresholds, "checks": checks, "passed": all(checks.values())}


def load_queries(path: Path | None) -> list[str]:
    if path is None:
        return list(DEFAULT_QUERIES)
    queries: list[str] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        data = json.loads(line)
        query = str(data["query"]).strip()
        if query:
            queries.append(query)
    if not queries:
        raise ValueError("query file did not contain any non-empty queries")
    return queries


async def run(args) -> dict:
    api_key = args.api_key_file.read_text(encoding="utf-8").strip()
    if not api_key:
        raise ValueError("API key file is empty")
    queries = load_queries(args.query_file)
    headers = {"X-API-Key": api_key, "Content-Type": "application/json"}
    semaphore = asyncio.Semaphore(args.concurrency)
    samples: list[dict] = []
    timeout = httpx.Timeout(args.timeout_seconds)

    async with httpx.AsyncClient(
        base_url=args.base_url.rstrip("/"),
        headers=headers,
        timeout=timeout,
    ) as client:
        async def one(index: int) -> None:
            async with semaphore:
                body = {
                    "agent_id": args.agent_id,
                    "query": queries[index % len(queries)],
                    "k": args.k,
                    "mode": args.mode,
                }
                started = time.perf_counter()
                try:
                    response = await client.post("/v1/recall", json=body)
                    latency_ms = (time.perf_counter() - started) * 1000
                    payload = response.json() if response.status_code == 200 else {}
                    samples.append({
                        "status": response.status_code,
                        "latency_ms": latency_ms,
                        "deadline_exceeded": payload.get("deadline_exceeded"),
                        "provenance_coverage": payload.get("provenance_coverage"),
                        "receipt_sha256": payload.get("receipt_sha256"),
                    })
                except Exception as exc:
                    samples.append({
                        "status": f"error:{type(exc).__name__}",
                        "latency_ms": (time.perf_counter() - started) * 1000,
                    })

        for index in range(args.warmup):
            await one(index)
        samples.clear()
        started = time.perf_counter()
        await asyncio.gather(*(one(index) for index in range(args.requests)))
        wall_seconds = time.perf_counter() - started

    report = summarize(samples, args.mode)
    report.update({
        "benchmark": "lians-http-load-v1",
        "base_url": args.base_url,
        "agent_id": args.agent_id,
        "concurrency": args.concurrency,
        "wall_seconds": round(wall_seconds, 3),
        "throughput_rps": round(args.requests / wall_seconds, 3) if wall_seconds else 0.0,
        "api_key_source": "file",
    })
    return apply_gate(
        report,
        p95_ms=args.p95_threshold_ms or DEFAULT_P95_MS[args.mode],
        min_success_rate=args.min_success_rate,
        min_provenance_coverage=args.min_provenance_coverage,
        min_receipt_coverage=args.min_receipt_coverage,
        max_deadline_exceeded_rate=args.max_deadline_exceeded_rate,
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", required=True)
    parser.add_argument("--api-key-file", required=True, type=Path)
    parser.add_argument("--agent-id", required=True)
    parser.add_argument("--query-file", type=Path)
    parser.add_argument("--mode", choices=("fast", "deep", "reconstruct"), default="fast")
    parser.add_argument("--requests", type=int, default=1000)
    parser.add_argument("--concurrency", type=int, default=50)
    parser.add_argument("--warmup", type=int, default=20)
    parser.add_argument("--k", type=int, default=10)
    parser.add_argument("--timeout-seconds", type=float, default=10.0)
    parser.add_argument("--p95-threshold-ms", type=float)
    parser.add_argument("--min-success-rate", type=float, default=1.0)
    parser.add_argument("--min-provenance-coverage", type=float, default=1.0)
    parser.add_argument("--min-receipt-coverage", type=float, default=1.0)
    parser.add_argument("--max-deadline-exceeded-rate", type=float, default=0.0)
    parser.add_argument("--out", type=Path)
    args = parser.parse_args()
    if args.requests < 1 or args.concurrency < 1 or args.warmup < 0:
        parser.error("requests/concurrency must be positive and warmup cannot be negative")
    report = asyncio.run(run(args))
    rendered = json.dumps(report, indent=2) + "\n"
    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(rendered, encoding="utf-8")
    print(rendered, end="")
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
