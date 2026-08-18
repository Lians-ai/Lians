from __future__ import annotations

from benchmarks.verification import correctness_benchmark, scale_benchmark


def test_verification_benchmark_detects_agent_failure_modes() -> None:
    result = correctness_benchmark(scenarios=9)

    assert result["pass"] is True
    assert result["detection_recall"] == 1.0
    assert result["ready_false_block_rate"] == 0.0
    assert result["task_contract_only_false_ready_rate"] == 1.0
    assert result["secret_leak_rate"] == 0.0


def test_verification_scale_smoke_is_bounded() -> None:
    result = scale_benchmark(changed_files=100)

    assert result["pass"] is True
    assert result["observed_files"] == 100
    assert result["verification_ms"] < 10_000
