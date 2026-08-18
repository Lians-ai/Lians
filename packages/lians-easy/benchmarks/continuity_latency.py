"""Measure Lians-owned latency without implying control over provider inference."""

from __future__ import annotations

import json
import statistics
from pathlib import Path
from tempfile import TemporaryDirectory
from time import perf_counter
from typing import Any

from lians_easy.continuity import build_continuity_graph
from lians_easy.project import Project
from lians_easy.store import MemoryStore
from lians_easy.work_brief import compile_session_brief


def _milliseconds(started: float) -> float:
    return (perf_counter() - started) * 1000


def _distribution(values: list[float]) -> dict[str, float]:
    ordered = sorted(values)
    p95_index = min(len(ordered) - 1, max(0, round(len(ordered) * 0.95) - 1))
    return {
        "median_ms": round(statistics.median(ordered), 3),
        "p95_ms": round(ordered[p95_index], 3),
        "max_ms": round(max(ordered), 3),
    }


def run(*, memory_count: int = 200, iterations: int = 25) -> dict[str, Any]:
    """Run a local synthetic benchmark for recall, graph, and session compilation."""

    if not 10 <= memory_count <= 2_000:
        raise ValueError("memory_count must be between 10 and 2000")
    if not 5 <= iterations <= 200:
        raise ValueError("iterations must be between 5 and 200")

    with TemporaryDirectory(prefix="lians-continuity-latency-") as directory:
        root = Path(directory)
        project = Project("latency-project", "Latency", str(root), None)
        store = MemoryStore(root / "memory.sqlite3")
        for index in range(memory_count):
            store.remember(
                f"Research finding {index:04d}: cohort {index % 12} prefers workflow {index % 7}.",
                kind="research",
                scope="project",
                project_id=project.id,
                source="synthetic benchmark fixture",
                source_client="codex" if index % 2 else "claude",
                topic=f"cohort-{index % 12}",
            )
        store.set_current(
            "research/active-cohort",
            "Active cohort is cohort 7",
            project_id=project.id,
            source_client="cursor",
        )

        recall_times = []
        for _ in range(iterations):
            started = perf_counter()
            store.context_pack(
                "Summarize cohort 7 research and active cohort",
                project=project,
                client="latency-benchmark",
                limit=3,
                max_tokens=512,
            )
            recall_times.append(_milliseconds(started))

        started = perf_counter()
        graph = build_continuity_graph(store, project_id=project.id, limit=memory_count + 1)
        graph_ms = _milliseconds(started)

        events = [
            {
                "kind": "completed" if index % 3 else "decision",
                "content": f"Session event {index:04d} for cohort {index % 12}",
                "agent": "Codex" if index % 2 else "Claude",
                "session_id": f"session-{index // 20}",
            }
            for index in range(memory_count)
        ]
        started = perf_counter()
        brief = compile_session_brief(events)
        brief_ms = _milliseconds(started)

    return {
        "schema": "https://lians.ai/schemas/continuity-latency-benchmark/v0.1",
        "fixture": {
            "synthetic": True,
            "memory_count": memory_count,
            "iterations": iterations,
        },
        "results": {
            "bounded_recall_and_signed_receipt": _distribution(recall_times),
            "work_graph_ms": round(graph_ms, 3),
            "session_brief_ms": round(brief_ms, 3),
            "graph_nodes": graph["summary"]["node_count"],
            "brief_input_events": brief["receipt"]["raw_record_count"],
        },
        "claim_boundary": (
            "Local synthetic wall-clock measurements for Lians-owned preprocessing only. "
            "They do not measure or accelerate provider model inference."
        ),
    }


if __name__ == "__main__":
    print(json.dumps(run(), indent=2, sort_keys=True))
