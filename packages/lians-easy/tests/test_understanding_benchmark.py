from __future__ import annotations

from pathlib import Path


def test_understanding_benchmark_keeps_claims_bounded(tmp_path: Path) -> None:
    from benchmarks.understanding_continuity import run_benchmark

    report = run_benchmark(tmp_path)
    aggregate = report["aggregate"]

    assert "not a benchmark of Mem0" in report["claim_boundary"]
    assert aggregate["prompt_only"]["essential_fact_recall_percent"] == 0
    assert aggregate["full_replay"]["essential_fact_recall_percent"] == 100
    assert aggregate["lians"]["context_tokens_estimate"] < aggregate["full_replay"]["context_tokens_estimate"]
    assert aggregate["lians"]["essential_fact_recall_percent"] > 0
    assert all(row["intent"] for row in report["scenarios"])
