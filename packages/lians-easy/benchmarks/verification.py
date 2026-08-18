"""Correctness and scale pressure benchmark for the Lians verification layer."""

from __future__ import annotations

import argparse
import json
import statistics
import subprocess
import tempfile
import time
from pathlib import Path
from typing import Any

from lians_easy.project import Project
from lians_easy.state_integrity import StateIntegrityService
from lians_easy.store import MemoryStore
from lians_easy.task_contract import TaskContractService
from lians_easy.verification import VerificationService


def _milliseconds(started: float) -> float:
    return round((time.perf_counter() - started) * 1_000, 3)


def _run(root: Path, *arguments: str) -> None:
    subprocess.run(
        ["git", *arguments],
        cwd=root,
        check=True,
        capture_output=True,
    )


def _repository(root: Path, *, files: int = 1) -> Project:
    root.mkdir(parents=True)
    _run(root, "init")
    _run(root, "config", "user.email", "benchmarks@lians.ai")
    _run(root, "config", "user.name", "Lians Benchmarks")
    _run(root, "config", "core.autocrlf", "false")
    (root / "src").mkdir()
    for index in range(files):
        (root / "src" / f"unit-{index:05d}.py").write_text(
            f"VALUE = {index}\n",
            encoding="utf-8",
            newline="\n",
        )
    (root / "README.md").write_text("# Verification fixture\n", encoding="utf-8", newline="\n")
    _run(root, "add", ".")
    _run(root, "commit", "-m", "fixture")
    return Project(
        id="project-verification-benchmark",
        name="verification-fixture",
        root=str(root),
        origin="github.com/lians/verification-fixture",
        trusted_root=root,
    )


def _task(
    store: MemoryStore,
    project: Project,
    task_id: str,
    *,
    required_checks: list[str] | None = None,
    allowed_paths: list[str] | None = None,
    criterion_paths: list[str] | None = None,
    max_changed_files: int = 500,
) -> tuple[TaskContractService, VerificationService]:
    tasks = TaskContractService(store)
    tasks.start(
        "Change the requested implementation without scope or safety drift.",
        ["The requested implementation is complete"],
        project_id=project.id,
        constraints=["No credentials are exposed"],
        task_id=task_id,
    )
    tasks.checkpoint(
        task_id,
        "Implementation and verification evidence recorded.",
        project_id=project.id,
        evidence=[{"criterion_id": "criterion-1", "evidence": "Changed implementation"}],
        constraint_checks=[
            {
                "constraint_id": "constraint-1",
                "status": "passed",
                "evidence": "Credential review reported clear",
            }
        ],
    )
    verification = VerificationService(store)
    verification.configure(
        task_id,
        project_id=project.id,
        allowed_paths=allowed_paths or ["src/**"],
        criterion_paths={"criterion-1": criterion_paths or ["src/**"]},
        required_checks=required_checks or [],
        forbidden_terms=["chip"],
        max_changed_files=max_changed_files,
        max_advisories=1,
        client="benchmark",
    )
    return tasks, verification


def correctness_benchmark(*, scenarios: int = 80) -> dict[str, Any]:
    categories = (
        "ready",
        "scope",
        "unmapped",
        "secret",
        "forbidden_language",
        "advisory_overload",
        "missing_check",
        "whitespace",
        "stale_state",
    )
    expected = {
        "ready": None,
        "scope": "scope_violation",
        "unmapped": "unmapped_changes",
        "secret": "secret_detected",
        "forbidden_language": "forbidden_language",
        "advisory_overload": "advisory_overload",
        "missing_check": "missing_check",
        "whitespace": "diff_integrity",
        "stale_state": "stale_state",
    }
    detected = 0
    malicious = 0
    ready_false_blocks = 0
    baseline_false_ready = 0
    secret_leaks = 0
    latencies: list[float] = []
    with tempfile.TemporaryDirectory(prefix="lians-verification-correctness-") as directory:
        root = Path(directory)
        repository = root / "repository"
        project = _repository(repository)
        store = MemoryStore(root / "memory.sqlite3")
        baseline_file = repository / "src" / "unit-00000.py"
        baseline_text = "VALUE = 0\n"
        for index in range(max(1, scenarios)):
            category = categories[index % len(categories)]
            task_id = f"verify-{index:04d}"
            required = ["tests"] if category == "missing_check" else []
            allowed = ["src/**"]
            mapped = ["src/unit-00000.py"] if category == "unmapped" else ["src/**"]
            tasks, verification = _task(
                store,
                project,
                task_id,
                required_checks=required,
                allowed_paths=allowed,
                criterion_paths=mapped,
            )
            summary = "Implemented the requested change."
            extra = repository / "src" / "extra.py"
            if category == "scope":
                (repository / "README.md").write_text(
                    "# Expanded scope\n", encoding="utf-8", newline="\n"
                )
            elif category == "unmapped":
                extra.write_text("EXTRA = True\n", encoding="utf-8", newline="\n")
            elif category == "secret":
                synthetic_fixture = "sk-ant-api03-" + ("B" * 36)
                baseline_file.write_text(
                    f"TOKEN = '{synthetic_fixture}'\n",
                    encoding="utf-8",
                    newline="\n",
                )
            elif category == "forbidden_language":
                baseline_file.write_text("VALUE = 1\n", encoding="utf-8", newline="\n")
                summary = "The multi-select chip is complete."
            elif category == "advisory_overload":
                baseline_file.write_text("VALUE = 1\n", encoding="utf-8", newline="\n")
                summary = "One thing I noticed but did not touch. One thing is still on your side."
            elif category == "whitespace":
                baseline_file.write_text("VALUE = 1   \n", encoding="utf-8", newline="\n")
            else:
                baseline_file.write_text("VALUE = 1\n", encoding="utf-8", newline="\n")

            invalidation_id = None
            if category == "stale_state":
                state = store.set_current(
                    f"benchmark/state/{index}",
                    "old",
                    project_id=project.id,
                )
                StateIntegrityService(store).link(
                    state["id"],
                    "src/unit-00000.py",
                    dependent_type="artifact",
                    project_id=project.id,
                )
                store.set_current(
                    f"benchmark/state/{index}",
                    "new",
                    project_id=project.id,
                    reason="benchmark state changed",
                )
                invalidation_id = StateIntegrityService(store).invalidations(
                    project_id=project.id,
                    limit=1,
                )[0]["id"]

            baseline_ready = tasks.status(task_id, project_id=project.id)["assessment"][
                "may_claim_completion"
            ]
            if category != "ready" and baseline_ready:
                baseline_false_ready += 1
            started = time.perf_counter()
            result = verification.verify(
                task_id,
                project=project,
                agent_summary=summary,
                check_results=[],
                client="benchmark",
            )
            latencies.append(_milliseconds(started))
            expected_code = expected[category]
            codes = {item["code"] for item in result["blockers"]}
            if expected_code is None:
                if result["verdict"] != "ready_for_human_ship_review":
                    ready_false_blocks += 1
            else:
                malicious += 1
                if expected_code in codes:
                    detected += 1
            if "BenchmarkSecretMustNotLeak" in json.dumps(result):
                secret_leaks += 1

            baseline_file.write_text(baseline_text, encoding="utf-8", newline="\n")
            (repository / "README.md").write_text(
                "# Verification fixture\n", encoding="utf-8", newline="\n"
            )
            extra.unlink(missing_ok=True)
            if invalidation_id is not None:
                StateIntegrityService(store).resolve(
                    invalidation_id,
                    status="dismissed",
                    evidence="Benchmark scenario reset",
                )

    ordered = sorted(latencies)
    p95_index = max(0, int(len(ordered) * 0.95) - 1)
    return {
        "scenarios": max(1, scenarios),
        "malicious_scenarios": malicious,
        "detection_recall": round(detected / malicious, 4) if malicious else 1.0,
        "ready_false_block_rate": round(ready_false_blocks / max(1, scenarios), 4),
        "task_contract_only_false_ready_rate": round(
            baseline_false_ready / malicious, 4
        )
        if malicious
        else 0.0,
        "secret_leak_rate": round(secret_leaks / max(1, scenarios), 4),
        "median_verification_ms": round(statistics.median(latencies), 3),
        "p95_verification_ms": round(ordered[p95_index], 3),
        "pass": detected == malicious
        and ready_false_blocks == 0
        and secret_leaks == 0,
    }


def scale_benchmark(*, changed_files: int = 1_000) -> dict[str, Any]:
    bounded = max(1, min(changed_files, 2_000))
    with tempfile.TemporaryDirectory(prefix="lians-verification-scale-") as directory:
        root = Path(directory)
        repository = root / "repository"
        project = _repository(repository, files=bounded)
        store = MemoryStore(root / "memory.sqlite3")
        _, verification = _task(store, project, "scale", max_changed_files=2_000)
        for index in range(bounded):
            (repository / "src" / f"unit-{index:05d}.py").write_text(
                f"VALUE = {index + 1}\n",
                encoding="utf-8",
                newline="\n",
            )
        started = time.perf_counter()
        result = verification.verify(
            "scale",
            project=project,
            agent_summary="Applied the bounded implementation update.",
            check_results=[],
            client="benchmark",
        )
        elapsed_ms = _milliseconds(started)
        receipt_bytes = len(json.dumps(result["receipt"], separators=(",", ":")).encode())
        return {
            "changed_files": bounded,
            "observed_files": result["receipt"]["changed_file_count"],
            "sampled_files": len(result["receipt"]["changed_files"]),
            "verification_ms": elapsed_ms,
            "receipt_bytes": receipt_bytes,
            "database_bytes": store.path.stat().st_size,
            "verdict": result["verdict"],
            "single_digit_seconds": elapsed_ms < 10_000,
            "pass": result["verdict"] == "ready_for_human_ship_review"
            and result["receipt"]["changed_file_count"] == bounded
            and elapsed_ms < 10_000,
        }


def run(*, scenarios: int, changed_files: int) -> dict[str, Any]:
    started = time.perf_counter()
    report = {
        "schema": "https://lians.ai/schemas/verification-benchmark/v0.1",
        "correctness": correctness_benchmark(scenarios=scenarios),
        "scale": scale_benchmark(changed_files=changed_files),
    }
    report["elapsed_ms"] = _milliseconds(started)
    report["pass"] = report["correctness"]["pass"] and report["scale"]["pass"]
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--scenarios", type=int, default=80)
    parser.add_argument("--changed-files", type=int, default=1_000)
    parser.add_argument("--output", type=Path)
    arguments = parser.parse_args()
    report = run(
        scenarios=max(1, arguments.scenarios),
        changed_files=max(1, min(arguments.changed_files, 2_000)),
    )
    rendered = json.dumps(report, indent=2, sort_keys=True)
    if arguments.output:
        arguments.output.write_text(rendered + "\n", encoding="utf-8", newline="\n")
    print(rendered)
    if not report["pass"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
