"""Adversarial correctness and 10,000-impact stress test for Lians state integrity."""

from __future__ import annotations

import argparse
import json
import statistics
import tempfile
import time
from pathlib import Path
from typing import Any

from lians_easy.project import Project
from lians_easy.state_integrity import StateIntegrityService
from lians_easy.store import MemoryStore


def _milliseconds(start: float) -> float:
    return round((time.perf_counter() - start) * 1_000, 3)


def correctness_benchmark(*, scenarios: int, fanout: int) -> dict[str, Any]:
    with tempfile.TemporaryDirectory(prefix="lians-state-correctness-") as directory:
        store = MemoryStore(Path(directory) / "memory.sqlite3")
        integrity = StateIntegrityService(store)
        project = Project("state-correctness", "State correctness", directory, None)
        expected_total = 0
        detected_total = 0
        false_total = 0
        stale_recalled = 0
        unaffected_missing = 0
        repair_to_affected_ratios: list[float] = []
        repair_to_project_ratios: list[float] = []
        latencies: list[float] = []

        for scenario in range(scenarios):
            root = store.set_current(
                f"requirements/scenario-{scenario}",
                f"Scenario {scenario} uses policy alpha.",
                project_id=project.id,
                event_time="2026-01-01T12:00:00Z",
            )
            dependent_memories: list[dict[str, Any]] = []
            expected_refs: set[str] = set()
            for branch in range(fanout):
                marker = f"derived-s{scenario}-b{branch}"
                memory = store.remember(
                    f"{marker} was produced under policy alpha.",
                    kind="project",
                    scope="project",
                    project_id=project.id,
                    event_time="2026-01-01T13:00:00Z",
                )
                dependent_memories.append(memory)
                artifact = f"artifacts/scenario-{scenario}/branch-{branch}.json"
                integrity.link(
                    root["id"],
                    memory["id"],
                    dependent_type="memory",
                    downstream_memory_id=memory["id"],
                    project_id=project.id,
                    label=marker,
                )
                integrity.link(
                    memory["id"],
                    artifact,
                    dependent_type="artifact",
                    project_id=project.id,
                    label=artifact,
                )
                expected_refs.update({memory["id"], artifact})
            unrelated = store.remember(
                f"unrelated-s{scenario} is independent of the policy.",
                kind="project",
                scope="project",
                project_id=project.id,
                event_time="2026-01-01T13:00:00Z",
            )

            started = time.perf_counter()
            replacement = store.set_current(
                f"requirements/scenario-{scenario}",
                f"Scenario {scenario} uses policy beta.",
                project_id=project.id,
                event_time="2026-01-02T12:00:00Z",
                reason="adversarial state change",
            )
            latencies.append(_milliseconds(started))
            invalidations = integrity.invalidations(
                project_id=project.id,
                root_trigger_memory_id=root["id"],
                limit=20_000,
            )
            actual_refs = {str(item["dependent_ref"]) for item in invalidations}
            expected_total += len(expected_refs)
            detected_total += len(actual_refs & expected_refs)
            false_total += len(actual_refs - expected_refs)
            for branch, memory in enumerate(dependent_memories):
                marker = f"derived-s{scenario}-b{branch}"
                recalled = store.recall(marker, project_id=project.id, limit=20)
                stale_recalled += memory["id"] in {item["id"] for item in recalled}
            unaffected = store.recall(
                f"unrelated-s{scenario}", project_id=project.id, limit=20
            )
            unaffected_missing += unrelated["id"] not in {item["id"] for item in unaffected}
            brief = integrity.repair_brief(
                project_id=project.id,
                root_trigger_memory_id=root["id"],
                max_tokens=512,
            )
            full_replay_tokens = sum(
                int(item["token_estimate"] or 0) for item in dependent_memories
            ) + int(replacement["token_estimate"] or 0)
            if full_replay_tokens:
                repair_to_affected_ratios.append(
                    brief["token_estimate"] / full_replay_tokens
                )
            _, full_project_tokens = store._available_memory_totals(
                project_id=project.id
            )
            if full_project_tokens:
                repair_to_project_ratios.append(
                    brief["token_estimate"] / full_project_tokens
                )

        recall_rate = detected_total / expected_total if expected_total else 1.0
        false_rate = false_total / max(1, detected_total + false_total)
        stale_block_rate = 1 - stale_recalled / max(1, scenarios * fanout)
        unaffected_preservation_rate = 1 - unaffected_missing / max(1, scenarios)
        return {
            "scenarios": scenarios,
            "fanout": fanout,
            "expected_impacts": expected_total,
            "detected_impacts": detected_total,
            "impact_recall": round(recall_rate, 4),
            "false_invalidation_rate": round(false_rate, 4),
            "stale_retrieval_block_rate": round(stale_block_rate, 4),
            "unaffected_preservation_rate": round(unaffected_preservation_rate, 4),
            "median_change_ms": round(statistics.median(latencies), 3),
            "p95_change_ms": round(sorted(latencies)[max(0, int(len(latencies) * 0.95) - 1)], 3),
            "median_repair_to_affected_content_ratio": round(
                statistics.median(repair_to_affected_ratios), 4
            ),
            "median_repair_to_full_project_ratio": round(
                statistics.median(repair_to_project_ratios), 4
            ),
            "pass": (
                recall_rate >= 0.95
                and false_rate <= 0.10
                and stale_block_rate >= 0.95
                and unaffected_preservation_rate >= 0.95
            ),
        }


def naive_baseline(*, scenarios: int) -> dict[str, Any]:
    """Measure ordinary memory after a source fact changes without dependency tracking."""

    with tempfile.TemporaryDirectory(prefix="lians-state-baseline-") as directory:
        store = MemoryStore(Path(directory) / "memory.sqlite3")
        project_id = "naive-baseline"
        stale_recalled = 0
        for scenario in range(scenarios):
            store.set_current(
                f"requirements/{scenario}",
                "Use policy alpha.",
                project_id=project_id,
                event_time="2026-01-01T12:00:00Z",
            )
            marker = f"staleartifacttoken{scenario}"
            derived = store.remember(
                f"{marker} was produced under policy alpha.",
                scope="project",
                project_id=project_id,
                event_time="2026-01-01T13:00:00Z",
            )
            store.set_current(
                f"requirements/{scenario}",
                "Use policy beta.",
                project_id=project_id,
                event_time="2026-01-02T12:00:00Z",
            )
            recalled = store.recall(marker, project_id=project_id, limit=20)
            stale_recalled += derived["id"] in {item["id"] for item in recalled}
        return {
            "scenarios": scenarios,
            "stale_retrieval_rate": round(stale_recalled / max(1, scenarios), 4),
            "stale_retrieval_block_rate": round(1 - stale_recalled / max(1, scenarios), 4),
        }


def scale_benchmark(*, impact_count: int) -> dict[str, Any]:
    with tempfile.TemporaryDirectory(prefix="lians-state-scale-") as directory:
        path = Path(directory) / "memory.sqlite3"
        store = MemoryStore(path)
        integrity = StateIntegrityService(store)
        project = Project("state-scale", "State scale", directory, None)
        root = store.set_current(
            "corpus/classification-policy",
            "Use policy alpha.",
            project_id=project.id,
            event_time="2026-01-01T12:00:00Z",
        )
        dependents = [
            {
                "ref": f"video-analysis/{index:05d}.json",
                "type": "analysis",
                "label": f"Video analysis {index:05d}",
            }
            for index in range(impact_count)
        ]
        started = time.perf_counter()
        integrity.link_many(root["id"], dependents, project_id=project.id)
        link_ms = _milliseconds(started)

        started = time.perf_counter()
        store.set_current(
            "corpus/classification-policy",
            "Use policy beta.",
            project_id=project.id,
            event_time="2026-01-02T12:00:00Z",
            reason="classification policy changed",
        )
        change_ms = _milliseconds(started)

        started = time.perf_counter()
        brief = integrity.repair_brief(project_id=project.id, max_tokens=768)
        brief_ms = _milliseconds(started)

        started = time.perf_counter()
        store.context_pack(
            "What is the current classification policy?",
            project=project,
            client="benchmark",
            max_tokens=256,
        )
        recall_ms = _milliseconds(started)
        observed_count = integrity.invalidation_count(project_id=project.id)
        return {
            "impacts": impact_count,
            "observed_impacts": observed_count,
            "link_ms": link_ms,
            "change_and_invalidate_ms": change_ms,
            "bounded_repair_brief_ms": brief_ms,
            "ordinary_recall_ms": recall_ms,
            "repair_brief_tokens": brief["token_estimate"],
            "repair_brief_included_impacts": brief["included_impact_count"],
            "database_bytes": path.stat().st_size,
            "single_digit_seconds": change_ms < 10_000,
            "pass": observed_count == impact_count and change_ms < 10_000 and recall_ms < 250,
        }


def run(*, scenarios: int, fanout: int, impacts: int) -> dict[str, Any]:
    started = time.perf_counter()
    report = {
        "schema": "https://lians.ai/schemas/state-integrity-benchmark/v0.1",
        "correctness": correctness_benchmark(scenarios=scenarios, fanout=fanout),
        "naive_memory_baseline": naive_baseline(scenarios=min(scenarios, 100)),
        "scale": scale_benchmark(impact_count=impacts),
    }
    report["elapsed_ms"] = _milliseconds(started)
    report["pass"] = report["correctness"]["pass"] and report["scale"]["pass"]
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--scenarios", type=int, default=100)
    parser.add_argument("--fanout", type=int, default=3)
    parser.add_argument("--impacts", type=int, default=10_000)
    parser.add_argument("--output", type=Path)
    arguments = parser.parse_args()
    report = run(
        scenarios=max(1, arguments.scenarios),
        fanout=max(1, arguments.fanout),
        impacts=max(1, min(arguments.impacts, 20_000)),
    )
    rendered = json.dumps(report, indent=2, sort_keys=True)
    if arguments.output:
        arguments.output.write_text(rendered + "\n", encoding="utf-8")
    print(rendered)
    if not report["pass"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
