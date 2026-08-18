from __future__ import annotations

from benchmarks.state_integrity import correctness_benchmark, naive_baseline, scale_benchmark


def test_state_integrity_benchmark_beats_untracked_memory() -> None:
    result = correctness_benchmark(scenarios=5, fanout=2)
    baseline = naive_baseline(scenarios=5)

    assert result["pass"] is True
    assert result["impact_recall"] == 1.0
    assert result["false_invalidation_rate"] == 0.0
    assert result["stale_retrieval_block_rate"] == 1.0
    assert result["unaffected_preservation_rate"] == 1.0
    assert baseline["stale_retrieval_rate"] == 1.0
    assert baseline["stale_retrieval_block_rate"] == 0.0


def test_state_integrity_scale_smoke_is_bounded() -> None:
    result = scale_benchmark(impact_count=200)

    assert result["pass"] is True
    assert result["observed_impacts"] == 200
    assert result["change_and_invalidate_ms"] < 10_000
    assert result["repair_brief_tokens"] <= 768
    assert result["ordinary_recall_ms"] < 250
