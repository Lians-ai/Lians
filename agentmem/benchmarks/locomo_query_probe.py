"""Probe deterministic query expansion on a fixed list of diagnostic questions.

Gold evidence is used only after retrieval to report ranks. It is never passed
to query generation, recall, or fusion.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT / "sdk" / "python"))

from benchmarks.locomo_eval import _fused_recall  # noqa: E402
from lians import LocalLiansClient  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--db", required=True)
    parser.add_argument("--diagnostic", required=True)
    parser.add_argument("--agent", required=True)
    parser.add_argument("--k", type=int, default=200)
    parser.add_argument("--out", required=True)
    args = parser.parse_args()

    diagnostic = json.loads(Path(args.diagnostic).read_text(encoding="utf-8"))
    misses = [row for row in diagnostic["frozen"]["detail"] if not row["hit"]]
    output = []
    with LocalLiansClient(
        embedding_provider="sentence-transformers",
        db_path=args.db,
    ) as client:
        for row in misses:
            result = _fused_recall(
                client, args.agent, row["question"], args.k, True,
                include_context=True,
            )
            ids = [
                str(memory.get("metadata", {}).get("dia_id") or "")
                for memory in result.get("memories") or []
            ]
            ranks = [
                ids.index(str(evidence)) + 1 if str(evidence) in ids else None
                for evidence in row["evidence"]
            ]
            output.append({
                "question": row["question"],
                "category": row["category"],
                "evidence": row["evidence"],
                "expanded_ranks": ranks,
                "query_variants": result.get("query_variants") or [],
            })
    report = {"questions": len(output), "results": output}
    Path(args.out).write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
