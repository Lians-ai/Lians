from __future__ import annotations

from benchmarks.task_contract_correctness import run_benchmark as run_correctness
from benchmarks.task_contract_latency import run_benchmark as run_latency


def test_task_contract_adversarial_benchmark(tmp_path) -> None:
    result = run_correctness(tmp_path / "correctness.sqlite3")

    assert result["passed"] == result["total"] == 8
    assert result["accuracy"] == 1.0
    assert "does not measure semantic" in result["claim_boundary"]


def test_task_contract_latency_benchmark_is_measured_not_claimed(tmp_path) -> None:
    result = run_latency(tmp_path / "latency.sqlite3", iterations=3)

    assert result["iterations"] == 3
    assert result["status"]["median_ms"] >= 0
    assert result["signed_context"]["median_ms"] >= 0
    assert "Provider inference" in result["claim_boundary"]
