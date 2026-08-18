"""Deterministic local stress and recovery checks for the Lians data plane.

This is a product-engineering harness, not a vendor benchmark. It measures one
machine and one encrypted workspace at a time. Run it before packaging with::

    python benchmarks/product_stress.py --output product-stress-report.json
"""

from __future__ import annotations

import argparse
import json
import os
import sqlite3
import subprocess
import sys
import tempfile
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from statistics import median
from threading import Barrier
from time import perf_counter
from typing import Any

from lians_easy.project import Project
from lians_easy.store import ConcurrentUpdateError, MemoryStore


def _percentile(values: list[float], quantile: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    position = max(0, min(len(ordered) - 1, int(len(ordered) * quantile) - 1))
    return ordered[position]


def _database_facts(path: Path) -> dict[str, Any]:
    connection = sqlite3.connect(path)
    try:
        return {
            "memories": int(connection.execute("SELECT COUNT(*) FROM memories").fetchone()[0]),
            "receipts": int(
                connection.execute("SELECT COUNT(*) FROM context_receipts").fetchone()[0]
            ),
            "integrity": str(connection.execute("PRAGMA integrity_check").fetchone()[0]),
            "bytes": path.stat().st_size,
        }
    finally:
        connection.close()


def storage_concurrency(
    root: Path,
    *,
    workers: int,
    writes_per_worker: int,
    context_every: int,
) -> dict[str, Any]:
    directory = root / "concurrency"
    directory.mkdir(parents=True, exist_ok=True)
    database = directory / "memory.sqlite3"
    project = Project(
        id="stress-project",
        name="Stress project",
        root=str(directory),
        origin=None,
    )
    stores = [MemoryStore(database) for _ in range(workers)]

    def work(worker_id: int) -> tuple[list[float], int]:
        store = stores[worker_id]
        latencies: list[float] = []
        receipts = 0
        for item in range(writes_per_worker):
            started = perf_counter()
            store.remember(
                f"Worker {worker_id} record {item} preserves continuity evidence "
                f"alpha-{worker_id}-{item}.",
                project_id=project.id,
                scope="project",
                source_client="stress",
            )
            if context_every > 0 and item % context_every == 0:
                store.context_pack(
                    "continuity evidence alpha",
                    project=project,
                    client="stress",
                )
                receipts += 1
            latencies.append((perf_counter() - started) * 1_000)
        return latencies, receipts

    started = perf_counter()
    errors: list[str] = []
    latencies: list[float] = []
    receipt_count = 0
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = [pool.submit(work, worker_id) for worker_id in range(workers)]
        for future in as_completed(futures):
            try:
                worker_latencies, worker_receipts = future.result()
            except Exception as exc:  # noqa: BLE001 - a stress report must retain every failure
                errors.append(f"{type(exc).__name__}: {exc}")
            else:
                latencies.extend(worker_latencies)
                receipt_count += worker_receipts
    elapsed = perf_counter() - started
    facts = _database_facts(database)
    expected_writes = workers * writes_per_worker
    passed = (
        not errors
        and facts["memories"] == expected_writes
        and facts["receipts"] == receipt_count
        and facts["integrity"] == "ok"
    )
    return {
        "passed": passed,
        "workers": workers,
        "writes_requested": expected_writes,
        "writes_committed": facts["memories"],
        "receipts": facts["receipts"],
        "errors": errors[:20],
        "wall_seconds": round(elapsed, 3),
        "writes_per_second": round(expected_writes / elapsed, 2),
        "latency_ms": {
            "p50": round(median(latencies), 2) if latencies else None,
            "p95": round(_percentile(latencies, 0.95), 2) if latencies else None,
            "max": round(max(latencies), 2) if latencies else None,
        },
        "database": facts,
    }


def stale_writer_contention(root: Path, *, workers: int) -> dict[str, Any]:
    directory = root / "contention"
    directory.mkdir(parents=True, exist_ok=True)
    database = directory / "memory.sqlite3"
    stores = [MemoryStore(database) for _ in range(workers)]
    first = stores[0].set_current(
        "tasks/stress/state",
        "Initial state before concurrent checkpoints.",
        project_id="stress-project",
        expected_current_id=None,
    )
    barrier = Barrier(workers)

    def update(worker_id: int) -> str:
        barrier.wait()
        try:
            stores[worker_id].set_current(
                "tasks/stress/state",
                f"Checkpoint supplied by worker {worker_id}.",
                project_id="stress-project",
                expected_current_id=first["id"],
            )
        except ConcurrentUpdateError:
            return "conflict"
        return "committed"

    with ThreadPoolExecutor(max_workers=workers) as pool:
        outcomes = list(pool.map(update, range(workers)))
    history = stores[0].memory_history(
        "tasks/stress/state",
        project_id="stress-project",
    )
    committed = outcomes.count("committed")
    conflicts = outcomes.count("conflict")
    return {
        "passed": committed == 1 and conflicts == workers - 1 and len(history) == 2,
        "workers": workers,
        "committed": committed,
        "conflicts": conflicts,
        "history_versions": len(history),
        "integrity": _database_facts(database)["integrity"],
    }


def deep_retrieval(root: Path, *, records: int) -> dict[str, Any]:
    directory = root / "retrieval"
    directory.mkdir(parents=True, exist_ok=True)
    database = directory / "memory.sqlite3"
    store = MemoryStore(database)
    target = store.remember(
        "The irreplaceable zirconium rendezvous code is BLUE-ORCHID.",
        project_id="research",
        scope="project",
    )
    for index in range(records - 1):
        store.remember(
            f"Noise record {index} covers routine market research batch processing.",
            project_id="research",
            scope="project",
        )
    started = perf_counter()
    recalled = store.recall(
        "What is the zirconium rendezvous code?",
        project_id="research",
    )
    elapsed_ms = (perf_counter() - started) * 1_000
    found = any(item["id"] == target["id"] for item in recalled)
    return {
        "passed": found,
        "records": records,
        "target_found": found,
        "recall_ms": round(elapsed_ms, 2),
        "returned": len(recalled),
        "database": _database_facts(database),
    }


def abrupt_exit_recovery(root: Path, *, committed_writes: int) -> dict[str, Any]:
    directory = root / "recovery"
    directory.mkdir(parents=True, exist_ok=True)
    database = directory / "memory.sqlite3"
    package_root = Path(__file__).resolve().parents[1]
    child = "\n".join(
        (
            "import os, sys",
            "from lians_easy.store import MemoryStore",
            "store = MemoryStore(sys.argv[1])",
            f"for index in range({committed_writes}):",
            "    store.remember(f'Committed record {index}.')",
            "os._exit(37)",
        )
    )
    environment = os.environ.copy()
    environment["PYTHONPATH"] = str(package_root)
    process = subprocess.run(
        [sys.executable, "-c", child, str(database)],
        check=False,
        env=environment,
        timeout=60,
    )
    reopened = MemoryStore(database)
    facts = _database_facts(database)
    readable = len(reopened.list(state="current", limit=min(committed_writes, 200)))
    return {
        "passed": (
            process.returncode == 37
            and facts["memories"] == committed_writes
            and facts["integrity"] == "ok"
            and readable == committed_writes
        ),
        "process_exit_code": process.returncode,
        "committed_writes": committed_writes,
        "readable_writes": readable,
        "database": facts,
    }


def run(args: argparse.Namespace) -> dict[str, Any]:
    with tempfile.TemporaryDirectory(
        prefix="lians-product-stress-",
        ignore_cleanup_errors=True,
    ) as temporary:
        root = Path(temporary)
        checks = {
            "storage_concurrency": storage_concurrency(
                root,
                workers=args.workers,
                writes_per_worker=args.writes_per_worker,
                context_every=args.context_every,
            ),
            "stale_writer_contention": stale_writer_contention(
                root,
                workers=args.contention_workers,
            ),
            "deep_retrieval": deep_retrieval(root, records=args.records),
            "abrupt_exit_recovery": abrupt_exit_recovery(
                root,
                committed_writes=args.recovery_writes,
            ),
        }
    return {
        "benchmark": "Lians local product stress",
        "claim_boundary": (
            "One-machine engineering evidence for this build. It is not a hosted "
            "capacity claim, demand measurement, or comparison with another vendor."
        ),
        "passed": all(check["passed"] for check in checks.values()),
        "checks": checks,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--workers", type=int, default=16)
    parser.add_argument("--writes-per-worker", type=int, default=100)
    parser.add_argument("--context-every", type=int, default=10)
    parser.add_argument("--contention-workers", type=int, default=16)
    parser.add_argument("--records", type=int, default=2_000)
    parser.add_argument("--recovery-writes", type=int, default=100)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    report = run(args)
    rendered = json.dumps(report, indent=2, sort_keys=True)
    print(rendered)
    if args.output:
        args.output.write_text(rendered + "\n", encoding="utf-8")
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
