"""Offline checks for the LangGraph integration example."""

from pathlib import Path

from main import REMEMBERED_FACT, run_demo


def test_memory_persists_across_graph_invocations(tmp_path: Path) -> None:
    result = run_demo(tmp_path / "memory.db")

    assert result["remembered"] is not None
    assert result["remembered"]["content"] == REMEMBERED_FACT
    assert any(memory["content"] == REMEMBERED_FACT for memory in result["memories"])
