"""Tests for honest RIAD-1 comparison rendering."""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "benchmarks"))

from riad_comparison import NA, NOT_RUN, PARTIAL, build_report, render_markdown


async def test_comparison_distinguishes_execution_from_capability_assessment():
    report = await build_report(repetitions=1)

    assert report["methodology"]["executed"] == ["Lians"]
    assert report["systems"]["Lians"]["exact_point_in_time_reconstruction"]["status"] == "pass"
    assert report["systems"]["Mem0 OSS"]["exact_point_in_time_reconstruction"]["status"] == NA
    assert report["systems"]["Graphiti OSS"]["exact_point_in_time_reconstruction"]["status"] == PARTIAL
    assert report["systems"]["Letta"]["local_p95_under_3000_ms"]["status"] == NOT_RUN

    markdown = render_markdown(report)
    assert "capability-assessed" in markdown
    assert "N/A means" in markdown
