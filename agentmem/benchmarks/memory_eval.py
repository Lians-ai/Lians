"""
Memory evaluation harness — the LoCoMo / LongMemEval protocol, judge-free.

The standard long-term-memory benchmarks (LoCoMo, LongMemEval) feed a model a
multi-session conversation, then ask questions whose answers depend on
remembering — and correctly *updating* — facts across sessions. They score the
model's generated answer with an LLM judge.

This harness measures the part a memory layer is actually responsible for:
**evidence retrieval** — does recall surface the memory that contains the answer?
That's a deterministic, judge-free proxy (``answer_recall@k``) that isolates the
memory system from the downstream LLM, and it directly exercises the property
Lians is built for: a *superseded* fact must NOT be retrieved, and its current
replacement must be.

Run on the bundled sample (no external data, no API keys)::

    python -m benchmarks.memory_eval

Plug in the real datasets by converting them to the same schema (see
``data/sample_memory_eval.json``) and passing ``--dataset path.json``.
"""
from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

_DATA = Path(__file__).resolve().parent / "data" / "sample_memory_eval.json"


def _parse_date(s: str) -> datetime:
    return datetime.fromisoformat(s).replace(tzinfo=timezone.utc) if "T" in s \
        else datetime.fromisoformat(s + "T12:00:00").replace(tzinfo=timezone.utc)


def load_dataset(path: str | Path = _DATA) -> dict[str, Any]:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def run_eval(client, dataset: dict[str, Any], k: int = 5) -> dict[str, Any]:
    """
    Ingest each sample's sessions as memories (event-timed), then score every
    question by whether the gold ``answer`` appears in the top-``k`` recall.

    ``client`` is any Lians client exposing ``add`` and ``recall`` (e.g.
    LocalLiansClient). Returns a report dict with the overall ``answer_recall_at_k``,
    a per-category breakdown, and the per-question detail (so you can see which
    supersession cases passed).
    """
    total = 0
    correct = 0
    by_cat: dict[str, list[int]] = {}
    detail: list[dict[str, Any]] = []

    for i, sample in enumerate(dataset["samples"]):
        agent = f"eval-{i}"
        for sess in sample["sessions"]:
            when = _parse_date(sess["date"])
            for turn in sess["turns"]:
                client.add(
                    agent_id=agent,
                    content=f"{turn['speaker']}: {turn['text']}",
                    event_time=when,
                    metadata=turn.get("metadata") or None,
                )

        for q in sample["questions"]:
            res = client.recall(agent_id=agent, query=q["question"], k=k)
            memories = res.get("memories", []) if isinstance(res, dict) else []
            ans = q["answer"].lower()
            found = any(ans in (m.get("content") or "").lower() for m in memories)

            # For supersession questions, also confirm the STALE value is gone.
            stale_ok = True
            if "stale" in q:
                stale = q["stale"].lower()
                stale_ok = not any(stale in (m.get("content") or "").lower() for m in memories)

            ok = found and stale_ok
            total += 1
            correct += int(ok)
            cat = q.get("category", "general")
            by_cat.setdefault(cat, [0, 0])
            by_cat[cat][0] += int(ok)
            by_cat[cat][1] += 1
            detail.append({"question": q["question"], "ok": ok, "found": found,
                           "stale_excluded": stale_ok, "category": cat})

    return {
        "answer_recall_at_k": correct / total if total else 0.0,
        "k": k,
        "total": total,
        "correct": correct,
        "by_category": {c: v[0] / v[1] for c, v in by_cat.items()},
        "detail": detail,
    }


def main() -> None:
    ap = argparse.ArgumentParser(description="Lians memory evaluation harness")
    ap.add_argument("--dataset", default=str(_DATA))
    ap.add_argument("--k", type=int, default=5)
    args = ap.parse_args()

    # Local SQLite client — zero setup, no API keys.
    import sys
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "sdk" / "python"))
    from lians import LocalLiansClient

    dataset = load_dataset(args.dataset)
    with LocalLiansClient() as client:
        report = run_eval(client, dataset, k=args.k)

    print(f"answer_recall@{report['k']}: {report['answer_recall_at_k']:.0%} "
          f"({report['correct']}/{report['total']})")
    for cat, score in report["by_category"].items():
        print(f"  {cat:20s} {score:.0%}")


if __name__ == "__main__":
    main()
