"""Contract test for the pitch-facing RIAD-1 benchmark."""
# ruff: noqa: E402
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "benchmarks"))

from decision_reconstruction_eval import run_benchmark
from lians.models import Base


async def test_riad_1_holds_end_to_end():
    indexes_before = {
        index.name
        for table in Base.metadata.tables.values()
        for index in table.indexes
    }
    report = await run_benchmark(repetitions=3)
    indexes_after = {
        index.name
        for table in Base.metadata.tables.values()
        for index in table.indexes
    }

    assert report["benchmark"] == "RIAD-1"
    assert report["passed"] == report["total"]
    assert report["metrics"]["reconstruction_accuracy"] == 1.0
    assert report["metrics"]["provenance_coverage"] == 1.0
    assert report["metrics"]["tamper_detection_rate"] == 1.0
    assert indexes_after == indexes_before
    assert "uq_otel_span_scope_trace_span" in indexes_after
