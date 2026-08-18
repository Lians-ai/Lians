"""Reproducible adversarial checks for Lians' definition-of-done gate."""

from __future__ import annotations

import json
import tempfile
from pathlib import Path
from typing import Any

from lians_easy.store import MemoryStore
from lians_easy.task_contract import TaskContractService


def _record(cases: list[dict[str, Any]], name: str, passed: bool, detail: str) -> None:
    cases.append({"name": name, "passed": bool(passed), "detail": detail})


def run_benchmark(database: str | Path) -> dict[str, Any]:
    service = TaskContractService(MemoryStore(database))
    project_id = "task-contract-benchmark"
    service.start(
        "Release a verified agent companion",
        ["The launcher passes", "The runtime passes"],
        constraints=["No credentials are packaged"],
        task_id="release",
        project_id=project_id,
        event_time="2026-08-17T10:00:00Z",
    )
    cases: list[dict[str, Any]] = []

    started = service.status("release", project_id=project_id)
    _record(
        cases,
        "missing evidence cannot complete",
        started["assessment"]["status"] == "active"
        and not started["assessment"]["may_claim_completion"],
        started["assessment"]["status"],
    )

    rejected_unknown = False
    try:
        service.checkpoint(
            "release",
            "Claimed complete",
            project_id=project_id,
            evidence=[{"criterion_id": "criterion-99", "evidence": "unsupported"}],
        )
    except ValueError:
        rejected_unknown = True
    _record(cases, "unknown criterion rejected", rejected_unknown, str(rejected_unknown))

    all_evidence = service.checkpoint(
        "release",
        "Both technical checks passed",
        project_id=project_id,
        evidence=[
            {"criterion_id": "criterion-1", "evidence": "launcher exit code 0"},
            {"criterion_id": "criterion-2", "evidence": "runtime tool contract passed"},
        ],
        event_time="2026-08-17T11:00:00Z",
    )
    _record(
        cases,
        "unknown constraint keeps gate closed",
        all_evidence["assessment"]["status"] == "active",
        all_evidence["assessment"]["status"],
    )

    blocked = service.checkpoint(
        "release",
        "Signing is unavailable",
        project_id=project_id,
        constraint_checks=[
            {
                "constraint_id": "constraint-1",
                "status": "passed",
                "evidence": "secret scan passed",
            }
        ],
        blockers=["unsigned executable"],
        event_time="2026-08-17T12:00:00Z",
    )
    _record(
        cases,
        "blocker overrides complete evidence",
        blocked["assessment"]["status"] == "blocked",
        blocked["assessment"]["status"],
    )

    failed = service.checkpoint(
        "release",
        "A secret scanner match appeared",
        project_id=project_id,
        constraint_checks=[
            {
                "constraint_id": "constraint-1",
                "status": "failed",
                "evidence": "scanner matched packaged.log",
            }
        ],
        blockers=[],
        event_time="2026-08-17T13:00:00Z",
    )
    _record(
        cases,
        "failed constraint overrides complete evidence",
        failed["assessment"]["status"] == "at_risk",
        failed["assessment"]["status"],
    )

    ready = service.checkpoint(
        "release",
        "The package and scanner now pass",
        project_id=project_id,
        constraint_checks=[
            {
                "constraint_id": "constraint-1",
                "status": "passed",
                "evidence": "clean rebuild scan passed",
            }
        ],
        event_time="2026-08-17T14:00:00Z",
    )
    _record(
        cases,
        "complete evidence opens review gate",
        ready["assessment"]["status"] == "ready_for_review"
        and ready["assessment"]["may_claim_completion"],
        ready["assessment"]["status"],
    )

    stale_rejected = False
    try:
        service.checkpoint(
            "release",
            "Delayed agent says the old build is current",
            project_id=project_id,
            event_time="2026-08-17T13:30:00Z",
        )
    except ValueError:
        stale_rejected = True
    _record(cases, "stale agent checkpoint rejected", stale_rejected, str(stale_rejected))

    service.start(
        "Analyze student research interviews",
        ["Produce a cited theme report"],
        task_id="research",
        project_id=project_id,
    )
    drift = service.checkpoint(
        "research",
        "Compare beach resorts and airline meals for a vacation",
        project_id=project_id,
        current_action="Choose a hotel breakfast",
    )
    _record(
        cases,
        "unrelated action raises drift signal",
        drift["assessment"]["drift"]["signal"] == "possible_drift",
        drift["assessment"]["drift"]["signal"],
    )

    passed = sum(case["passed"] for case in cases)
    return {
        "benchmark": "lians-task-contract-correctness-v0.1",
        "passed": passed,
        "total": len(cases),
        "accuracy": round(passed / len(cases), 4),
        "cases": cases,
        "claim_boundary": (
            "Deterministic local contract checks only. This does not measure semantic "
            "reasoning quality or compare an external vendor."
        ),
    }


def main() -> None:
    with tempfile.TemporaryDirectory(prefix="lians-task-contract-") as directory:
        result = run_benchmark(Path(directory) / "memory.sqlite3")
    print(json.dumps(result, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
