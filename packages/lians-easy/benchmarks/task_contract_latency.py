"""Measure local task-contract overhead outside provider inference."""

from __future__ import annotations

import json
import statistics
import tempfile
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

from lians_easy.store import MemoryStore
from lians_easy.task_contract import TaskContractService


def _measure(operation: Callable[[], Any], *, iterations: int) -> dict[str, float]:
    samples: list[float] = []
    for _ in range(iterations):
        started = time.perf_counter()
        operation()
        samples.append((time.perf_counter() - started) * 1_000)
    ordered = sorted(samples)
    p95_index = max(0, min(len(ordered) - 1, int(len(ordered) * 0.95) - 1))
    return {
        "median_ms": round(statistics.median(samples), 3),
        "p95_ms": round(ordered[p95_index], 3),
        "max_ms": round(max(samples), 3),
    }


def run_benchmark(database: str | Path, *, iterations: int = 40) -> dict[str, Any]:
    service = TaskContractService(MemoryStore(database))
    project_id = "latency-project"
    service.start(
        "Complete a long-running research workflow",
        [f"Verify research output {index}" for index in range(1, 11)],
        constraints=["Keep sources local", "Do not expose credentials"],
        task_id="long-work",
        project_id=project_id,
    )
    service.checkpoint(
        "long-work",
        "Initial research pass is underway",
        project_id=project_id,
        current_action="Review source evidence",
    )

    status = _measure(
        lambda: service.status("long-work", project_id=project_id),
        iterations=iterations,
    )
    context = _measure(
        lambda: service.context(
            "long-work",
            project_id=project_id,
            client="benchmark",
            max_tokens=768,
        ),
        iterations=iterations,
    )
    return {
        "benchmark": "lians-task-contract-latency-v0.1",
        "iterations": iterations,
        "status": status,
        "signed_context": context,
        "claim_boundary": (
            "Measures local encrypted state resolution, contract assessment, rendering, and "
            "receipt signing. Provider inference and network latency are excluded."
        ),
    }


def main() -> None:
    with tempfile.TemporaryDirectory(prefix="lians-task-latency-") as directory:
        result = run_benchmark(Path(directory) / "memory.sqlite3")
    print(json.dumps(result, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
