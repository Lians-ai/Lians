"""
LOCOMO token-efficiency analysis — answer-context cost per question.

For each of the 1,986 LOCOMO questions, compares the tokens an answerer must
read under Lians retrieval (top-k memories, as dumped for the judged run) vs
stuffing the full conversation into context. Judge-free, pure accounting over
the artifacts of the judged run — no model calls, no ingest, no embeddings.

Token counts are exact (tiktoken o200k_base, the GPT-4o/gpt-5 tokenizer);
memory lines are rendered "[created_at] text" to mirror the harness's answer
prompt, conversations as "speaker: text" turns with session date headers.

Run (from agentmem root, after a locomo dump exists)::

    python -m benchmarks.locomo_tokens \
        --pred ../memory-benchmarks/results/locomo/predicted_lians_arctic
"""
from __future__ import annotations

import argparse
import json
import statistics
from pathlib import Path

import tiktoken

_REPO = Path(__file__).resolve().parent.parent
_DATASET = _REPO / "benchmarks" / "data" / "locomo10.json"

CUTOFFS = [10, 20, 50, 100, 200]


def conv_tokens(enc, conv: dict) -> int:
    """Full-context baseline: every session's turns with date headers."""
    parts: list[str] = []
    for key, sess in conv.items():
        if not key.startswith("session_") or key.endswith("_date_time"):
            continue
        date = conv.get(f"{key}_date_time", "")
        parts.append(f"SESSION ({date}):")
        for turn in sess:
            parts.append(f"{turn.get('speaker', '')}: {turn.get('text', '')}")
    return len(enc.encode("\n".join(parts)))


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--pred", required=True, help="predicted_* directory of per-question JSONs")
    ap.add_argument("--out", default=None, help="optional JSON report path")
    args = ap.parse_args()

    enc = tiktoken.get_encoding("o200k_base")
    dataset = json.loads(_DATASET.read_text(encoding="utf-8"))
    full_by_conv = {i: conv_tokens(enc, item["conversation"]) for i, item in enumerate(dataset)}

    per_cutoff: dict[int, list[int]] = {k: [] for k in CUTOFFS}
    full_ctx: list[int] = []
    n = 0
    for f in sorted(Path(args.pred).glob("conv*_q*.json")):
        q = json.loads(f.read_text(encoding="utf-8"))
        results = q["retrieval"]["search_results"]
        lines = [f"[{m.get('created_at', '')}] {m.get('memory', '')}" for m in results]
        tok = [len(enc.encode(line)) + 1 for line in lines]  # +1 newline join
        for k in CUTOFFS:
            per_cutoff[k].append(sum(tok[:k]))
        full_ctx.append(full_by_conv[q["conversation_idx"]])
        n += 1

    print(f"questions: {n}\n")
    fmean = statistics.mean(full_ctx)
    print(f"{'context':<22}{'mean tok':>10}{'median':>10}{'p95':>10}{'vs full':>10}")
    rows = {}
    for k in CUTOFFS:
        v = per_cutoff[k]
        mean = statistics.mean(v)
        med = statistics.median(v)
        p95 = sorted(v)[int(0.95 * len(v)) - 1]
        rows[f"top_{k}"] = {"mean": round(mean), "median": round(med), "p95": p95,
                            "pct_of_full": round(100 * mean / fmean, 2)}
        print(f"{'Lians top-' + str(k):<22}{mean:>10.0f}{med:>10.0f}{p95:>10}{mean / fmean:>9.1%}")
    med_f = statistics.median(full_ctx)
    p95_f = sorted(full_ctx)[int(0.95 * len(full_ctx)) - 1]
    print(f"{'full conversation':<22}{fmean:>10.0f}{med_f:>10.0f}{p95_f:>10}{'100.0%':>10}")
    rows["full_conversation"] = {"mean": round(fmean), "median": round(med_f), "p95": p95_f,
                                 "pct_of_full": 100.0}

    if args.out:
        Path(args.out).write_text(json.dumps({"questions": n, "tokenizer": "o200k_base",
                                              "contexts": rows}, indent=2), encoding="utf-8")
        print(f"\nwrote {args.out}")


if __name__ == "__main__":
    main()
