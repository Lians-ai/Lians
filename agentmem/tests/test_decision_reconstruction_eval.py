"""Contract test for the pitch-facing RIAD-1 benchmark."""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "benchmarks"))

from decision_reconstruction_eval import run_benchmark


async def test_riad_1_holds_end_to_end():
    report = await run_benchmark(repetitions=3)

    assert report["benchmark"] == "RIAD-1"
    assert report["passed"] == report["total"]
    assert report["metrics"]["reconstruction_accuracy"] == 1.0
    assert report["metrics"]["provenance_coverage"] == 1.0
    assert report["metrics"]["tamper_detection_rate"] == 1.0
