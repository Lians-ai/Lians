"""
Diagnose the low LOCOMO evidence_hit rate: ingest one conversation into a
persistent db, then answer three questions:

  1. How many of the ingested turns are actually retrievable at all?
     (query each evidence turn by its own text - near-verbatim recall)
  2. For failed questions, is the evidence memory stored but outranked,
     stored but validity-gated (superseded), or absent?
  3. Do retrieved memories carry dia_id metadata back out?

Usage:  python -m benchmarks.locomo_diag
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO_ROOT / "sdk" / "python"))

from benchmarks.locomo_eval import (  # noqa: E402
    _DATA, _turn_content, ingest_conversation, iter_sessions, _retrieved_ids,
)


def main() -> None:
    dataset = json.loads(Path(_DATA).read_text(encoding="utf-8"))
    sample = dataset[0]
    agent = f"diag-{sample['sample_id']}"

    from lians import LocalLiansClient

    db = str(Path(__file__).parent / "data" / "locomo_diag.sqlite")
    with LocalLiansClient(db_path=db) as client:
        # --- ingest (once; skip if db already has it) ---
        probe = client.recall(agent_id=agent, query="hello", k=1)
        already = bool(probe.get("memories")) if isinstance(probe, dict) else False
        if not already:
            n = ingest_conversation(client, agent, sample["conversation"])
            print(f"ingested {n} turns", flush=True)
        else:
            print("reusing existing diag db", flush=True)

        # build dia_id -> content map
        turns = {}
        for _, when, sess in iter_sessions(sample["conversation"]):
            for t in sess:
                turns[t["dia_id"]] = _turn_content(t)

        # --- 1. verbatim retrievability of every evidence turn ---
        evidence_ids = sorted({
            str(e) for q in sample["qa"] for e in (q.get("evidence") or [])
            if str(e) in turns
        })
        stored = 0
        missing: list[str] = []
        meta_ok = 0
        for eid in evidence_ids:
            res = client.recall(agent_id=agent, query=turns[eid][:200], k=5)
            ids = _retrieved_ids(res)
            if eid in ids:
                stored += 1
            else:
                # maybe stored but not top-5 even for its own text
                missing.append(eid)
            if any(ids):
                meta_ok += 1
        print(f"\n[1] evidence turns findable by their own text (top-5): "
              f"{stored}/{len(evidence_ids)}")
        print(f"    recalls returning at least one non-empty dia_id: "
              f"{meta_ok}/{len(evidence_ids)}")
        print(f"    not found by own text: {missing[:15]}"
              f"{' …' if len(missing) > 15 else ''}")

        # --- 2. drill into 5 failed headline questions ---
        print("\n[2] failed-question drilldown:")
        shown = 0
        for q in sample["qa"]:
            if shown >= 5 or q.get("category") == 5:
                continue
            evidence = [str(e) for e in (q.get("evidence") or [])]
            if not evidence:
                continue
            res = client.recall(agent_id=agent, query=q["question"], k=10)
            ids = _retrieved_ids(res)
            if any(e in ids for e in evidence):
                continue  # passed; we only want failures
            shown += 1
            print(f"  Q: {q['question'][:90]}")
            print(f"     evidence: {evidence} | answer: {str(q.get('answer'))[:60]}")
            ev0 = evidence[0]
            own = client.recall(agent_id=agent, query=turns.get(ev0, '')[:200], k=5)
            own_ids = _retrieved_ids(own)
            state = ("stored (found by own text) but OUTRANKED for the question"
                     if ev0 in own_ids else
                     "NOT retrievable even by its own text (dropped/merged/gated)")
            print(f"     evidence[0] {ev0}: {state}")
            print(f"     top-10 for question: {ids}")

        # --- 3. store size ---
        mem_count = None
        for attr in ("count", "stats"):
            fn = getattr(client, attr, None)
            if callable(fn):
                try:
                    mem_count = fn(agent_id=agent)
                except TypeError:
                    try:
                        mem_count = fn()
                    except Exception:
                        pass
                break
        print(f"\n[3] store count api: {mem_count!r} "
              f"(None = client exposes no count; check db directly)")


if __name__ == "__main__":
    main()
