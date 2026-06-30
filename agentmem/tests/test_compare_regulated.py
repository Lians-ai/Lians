"""
Guards the head-to-head regulated-eval comparison so its published numbers can't
silently rot. Lians must pass all five invariants live; the renderer must produce a
table whose scores match the scored capability maps.
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "sdk" / "python"))

from benchmarks import compare_regulated as cr  # noqa: E402
from benchmarks.adapters import PASS, PARTIAL, ABSENT, SCORE  # type: ignore # noqa: E402


def test_lians_passes_all_invariants_live():
    caps = cr._lians_live()
    assert all(v == PASS for v in caps.values()), caps
    assert len(caps) == len(cr.INVARIANTS)


def test_table_scores_are_consistent():
    columns = cr.build_table()
    names = [c[0] for c in columns]
    assert names[0] == "Lians"
    # Lians is the strict leader.
    scores = {
        name: sum(SCORE[caps.get(k, ABSENT)] for k, _ in cr.INVARIANTS)
        for name, caps, _ in columns
    }
    assert scores["Lians"] == float(len(cr.INVARIANTS))
    assert all(scores["Lians"] > s for n, s in scores.items() if n != "Lians")


def test_competitors_credited_not_strawmanned():
    # Capability maps must include at least one PARTIAL each — we credit real strengths.
    from benchmarks.adapters import mem0_adapter, zep_adapter

    assert PARTIAL in mem0_adapter.CAPABILITIES.values()
    assert PARTIAL in zep_adapter.CAPABILITIES.values()
    # Zep (temporal leader) must score at least as high as mem0.
    z = sum(SCORE[v] for v in zep_adapter.CAPABILITIES.values())
    m = sum(SCORE[v] for v in mem0_adapter.CAPABILITIES.values())
    assert z >= m


def test_markdown_renders():
    md = cr.render_markdown(cr.build_table())
    assert "Regulated invariant" in md
    assert "Lians" in md and "mem0" in md and "Zep" in md
