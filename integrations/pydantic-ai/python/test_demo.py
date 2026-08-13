"""Executable checks for the Pydantic AI integration."""

from pathlib import Path

from main import _contents, run_demo


def test_current_and_historical_recall_stay_separate(tmp_path: Path) -> None:
    results = run_demo(tmp_path / "memory.db")
    current = _contents(results["current"])
    historical = _contents(results["historical"])

    assert any("Monday" in content for content in current)
    assert all("Friday" not in content for content in current)
    assert any("Friday" in content for content in historical)
    assert all("Monday" not in content for content in historical)
    assert results["current"]["receipt_sha256"]
    assert results["historical"]["receipt_sha256"]
