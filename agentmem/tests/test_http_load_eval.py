import json

import pytest

from benchmarks.http_load_eval import apply_gate, load_queries, percentile, summarize


def test_percentile_uses_nearest_rank():
    values = [1, 2, 3, 4, 5, 100]
    assert percentile(values, 0.50) == 3
    assert percentile(values, 0.95) == 100
    assert percentile(values, 0.99) == 100


def test_summary_covers_slo_and_governance_signals():
    samples = [
        {
            "status": 200,
            "latency_ms": 10,
            "deadline_exceeded": False,
            "provenance_coverage": 1.0,
            "receipt_sha256": "a" * 64,
        },
        {
            "status": 200,
            "latency_ms": 20,
            "deadline_exceeded": True,
            "provenance_coverage": 0.5,
            "receipt_sha256": "",
        },
        {"status": 500, "latency_ms": 30},
    ]
    result = summarize(samples, "fast")
    assert result["success_rate"] == pytest.approx(2 / 3, abs=1e-6)
    assert result["deadline_exceeded_rate"] == 0.5
    assert result["mean_provenance_coverage"] == 0.75
    assert result["receipt_coverage"] == 0.5


def test_load_queries_reads_jsonl(tmp_path):
    path = tmp_path / "queries.jsonl"
    path.write_text(
        json.dumps({"query": "one"}) + "\n" + json.dumps({"query": "two"}) + "\n",
        encoding="utf-8",
    )
    assert load_queries(path) == ["one", "two"]


def test_apply_gate_requires_latency_reliability_and_provenance():
    passing = {
        "p95_ms": 90,
        "success_rate": 1.0,
        "mean_provenance_coverage": 1.0,
        "receipt_coverage": 1.0,
        "deadline_exceeded_rate": 0.0,
    }
    assert apply_gate(passing, p95_ms=100)["passed"]
    assert not apply_gate({**passing, "p95_ms": 101}, p95_ms=100)["passed"]
