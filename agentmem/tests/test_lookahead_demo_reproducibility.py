"""Keep the published lookahead demo tied to its deterministic evidence."""

from __future__ import annotations

import csv
import re
from pathlib import Path

DEMO = Path(__file__).resolve().parents[2] / "demo" / "lookahead-bias"


def _published_count(text: str) -> int:
    match = re.search(r"Contaminated retrievals:\s*\*\*(\d+)\*\*", text)
    assert match, "published contaminated-retrieval count is missing"
    return int(match.group(1))


def test_demo_pins_the_deterministic_embedding_provider():
    source = (DEMO / "run_demo.py").read_text(encoding="utf-8")
    assert 'EMBEDDING_PROVIDER = "local"' in source
    assert "embedding_provider=EMBEDDING_PROVIDER" in source


def test_published_receipt_count_matches_the_committed_artifact():
    with (DEMO / "results" / "receipts.csv").open(
        newline="", encoding="utf-8"
    ) as handle:
        receipt_count = sum(1 for _ in csv.DictReader(handle))

    summary = (DEMO / "results" / "summary.md").read_text(encoding="utf-8")
    readme = (DEMO / "README.md").read_text(encoding="utf-8")
    assert _published_count(summary) == receipt_count
    assert f"**{receipt_count} retrievals" in readme
