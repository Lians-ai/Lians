"""
Regulated memory eval — every compliance invariant must hold against a real
LocalLiansClient. These are the checks an accumulate-everything store fails.
"""
# ruff: noqa: E402
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "benchmarks"))

import regulated_eval
from lians import LocalLiansClient


def test_all_regulated_invariants_hold():
    with LocalLiansClient() as client:
        report = regulated_eval.run_regulated_eval(client)

    assert report["total"] == 5
    failed = [r["check"] for r in report["checks"] if not r["passed"]]
    assert report["passed"] == report["total"], f"failed invariants: {failed}"


def test_individual_invariants():
    with LocalLiansClient() as client:
        report = regulated_eval.run_regulated_eval(client)
    by_name = {r["check"]: r for r in report["checks"]}

    # The three an accumulate-everything store would fail:
    assert by_name["stale_revision_suppression"]["passed"]
    assert by_name["erasure_proof"]["passed"]
    assert by_name["lookahead_contamination_detection"]["passed"]
    # And the temporal-reconstruction ones:
    assert by_name["point_in_time_reconstruction"]["passed"]
    assert by_name["audit_state_reconstruction"]["passed"]
