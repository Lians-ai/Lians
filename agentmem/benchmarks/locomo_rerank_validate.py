"""
Full-LOCOMO offline validation of the cross-encoder reranker stage.

Local CPU only (models already cached) - no API spend. Per-conversation
checkpointing in results/replay/rerank_validation.json so interrupted runs
resume free.

Usage (from agentmem root):
    python -m benchmarks.locomo_rerank_validate
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np

_REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO))

from benchmarks.locomo_replay import Conv, _CACHE  # noqa: E402

SLUG = "Snowflake__snowflake-arctic-embed-l-v2.0"
CKPT = _CACHE / "rerank_validation.json"
PREFETCH = 30


def main() -> None:
    from sentence_transformers import CrossEncoder

    state = json.loads(CKPT.read_text()) if CKPT.exists() else {}
    ce = CrossEncoder("BAAI/bge-reranker-v2-m3", max_length=384)

    for n in range(10):
        if str(n) in state:
            continue
        conv = Conv(n)
        z = np.load(_CACHE / f"alt_{SLUG}_conv_{n}.npz")
        conv.doc_embs, conv.q_pre = z["doc_embs"], z["q_pre"]

        stats: dict[int, list[int]] = {}
        for qi, q in enumerate(conv.qa):
            sem = conv.doc_embs @ conv.q_pre[qi].astype(np.float32)
            nb = np.zeros_like(sem)
            for i in range(len(sem)):
                c = conv.coord.get(i)
                if c is None:
                    continue
                for t in (c[1] + 1, c[1] - 1):
                    j = conv.by_coord.get((c[0], t))
                    if j is not None and sem[j] > nb[i]:
                        nb[i] = sem[j]
            scores = 0.5 * (sem + 0.3 * nb) + 0.05 * conv.bm25(q["question"])
            cand = list(np.argsort(-scores, kind="stable")[:PREFETCH])
            ce_scores = np.asarray(ce.predict(
                [(q["question"], conv.contents[i]) for i in cand],
                show_progress_bar=False))
            top = [cand[i] for i in np.argsort(-ce_scores, kind="stable")[:10]]
            got = {conv.dia_ids[i] for i in top}
            ev, cat = q["evidence"], q["category"]
            s = stats.setdefault(cat, [0, 0, 0])
            s[0] += 1
            s[1] += int(any(e in got for e in ev))
            s[2] += int(all(e in got for e in ev))

        state[str(n)] = {str(c): v for c, v in stats.items()}
        CKPT.write_text(json.dumps(state, indent=1))
        head = [v for c, v in stats.items() if c != 5]
        hn = sum(v[0] for v in head)
        print(f"conv_{n}: hit@10 {sum(v[1] for v in head)/hn:.1%} "
              f"all@10 {sum(v[2] for v in head)/hn:.1%}", flush=True)

    agg: dict[int, list[int]] = {}
    for conv_stats in state.values():
        for c, v in conv_stats.items():
            s = agg.setdefault(int(c), [0, 0, 0])
            for i in range(3):
                s[i] += v[i]
    head = [v for c, v in agg.items() if c != 5]
    hn = sum(v[0] for v in head)
    print(f"\nALL 10 with reranker (prefetch {PREFETCH}): "
          f"hit@10 {sum(v[1] for v in head)/hn:.1%}  "
          f"all@10 {sum(v[2] for v in head)/hn:.1%}   "
          f"(blend-only baseline: 82.4% / 68.5%)")


if __name__ == "__main__":
    main()
