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
    # Capability maps must include at least one PARTIAL each - we credit real strengths.
    from benchmarks.adapters import (
        hindsight_adapter,
        letta_adapter,
        mem0_adapter,
        supermemory_adapter,
        zep_adapter,
    )

    for mod in (mem0_adapter, zep_adapter, letta_adapter, hindsight_adapter, supermemory_adapter):
        assert PARTIAL in mod.CAPABILITIES.values(), mod.NAME
        # Every capability map covers exactly the published invariants.
        assert set(mod.CAPABILITIES) == {k for k, _ in cr.INVARIANTS}, mod.NAME
    # Zep (temporal leader) must score at least as high as every other competitor.
    z = sum(SCORE[v] for v in zep_adapter.CAPABILITIES.values())
    for mod in (mem0_adapter, letta_adapter, hindsight_adapter, supermemory_adapter):
        assert z >= sum(SCORE[v] for v in mod.CAPABILITIES.values()), mod.NAME


def test_adapters_raise_capability_absent_for_missing_primitives():
    # The absent cells must correspond to primitives that literally throw.
    from benchmarks.adapters import (
        CapabilityAbsent,
        hindsight_adapter,
        letta_adapter,
        supermemory_adapter,
    )
    import pytest

    for cls in (letta_adapter.LettaAdapter, hindsight_adapter.HindsightAdapter,
                supermemory_adapter.SupermemoryAdapter):
        a = cls()
        with pytest.raises(CapabilityAbsent):
            a.recall_at("agent", "q", None)
        with pytest.raises(CapabilityAbsent):
            a.backtest_check("agent", None)
        with pytest.raises(CapabilityAbsent):
            a.snapshot("agent", None)
        with pytest.raises(CapabilityAbsent):
            a.erase("subj", "reason")


def test_markdown_renders():
    md = cr.render_markdown(cr.build_table())
    assert "Regulated invariant" in md
    assert "Lians" in md and "mem0" in md and "Zep" in md
    assert "Letta" in md and "Hindsight" in md and "Supermemory" in md
