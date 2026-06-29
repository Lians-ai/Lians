"""
Memory evaluation harness — runs the bundled sample through LocalLiansClient and
checks that the mechanism works, including the supersession invariant (the stale
value must be excluded, the current one retrieved).
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "sdk" / "python"))
sys.path.insert(0, str(ROOT / "benchmarks"))

from lians import LocalLiansClient
import memory_eval


def test_sample_eval_runs_and_scores():
    dataset = memory_eval.load_dataset()
    with LocalLiansClient() as client:
        report = memory_eval.run_eval(client, dataset, k=5)

    assert report["total"] == 5
    assert 0.0 <= report["answer_recall_at_k"] <= 1.0
    assert report["answer_recall_at_k"] >= 0.6  # sample is small + keyword-aligned


def test_supersession_invariant_holds():
    """The differentiator: a revised fact returns the current value, not the stale one."""
    dataset = memory_eval.load_dataset()
    with LocalLiansClient() as client:
        report = memory_eval.run_eval(client, dataset, k=5)

    sup = next(d for d in report["detail"] if d["category"] == "temporal-supersession")
    assert sup["found"] is True           # current salary (140000) retrieved
    assert sup["stale_excluded"] is True  # stale salary (120000) NOT retrieved
    assert sup["ok"] is True
