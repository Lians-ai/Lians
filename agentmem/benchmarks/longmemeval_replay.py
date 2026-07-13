"""
LongMemEval offline retrieval lab — judge-free iteration on cached embeddings.

Metric: evidence-session coverage@k — do the sessions listed in
``answer_session_ids`` appear among the sessions of the top-k retrieved
messages? Computable offline from results/longmemeval_cache/*.npz, so
retrieval variants iterate in seconds instead of $40 judged runs.

Abstention questions (*_abs) have no answer sessions and are skipped here —
their score is a generation property.

Usage (from agentmem root):
    python -m benchmarks.longmemeval_replay --analyze
    python -m benchmarks.longmemeval_replay --variants
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import numpy as np

_REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO))

from benchmarks.longmemeval_lians import (  # noqa: E402
    _DATASET, _CACHE, _parse_session_date, _bm25_scores,
    W_SEM, W_LEX, SMOOTH, T_BONUS,
)
from benchmarks.locomo_replay import _bm25_tokens  # noqa: E402
from src.lians.ranking import query_time_windows  # noqa: E402


class Q:
    """One question's cached corpus + rebuilt session mapping."""

    def __init__(self, question: dict):
        self.qid = question["question_id"]
        self.qtype = question["question_type"]
        self.question = question["question"]
        self.answer_sessions = set(question.get("answer_session_ids", []))
        contents, sess_ids, ts = [], [], []
        from datetime import timedelta
        for sess, date_str, sid in zip(question["haystack_sessions"],
                                       question["haystack_dates"],
                                       question["haystack_session_ids"]):
            when = _parse_session_date(date_str)
            for j, msg in enumerate(sess):
                text = (msg.get("content") or "").strip()
                if not text:
                    continue
                contents.append(f"{msg.get('role', 'user')}: {text}")
                sess_ids.append(sid)
                ts.append((when + timedelta(seconds=j)).timestamp())
        self.contents = contents
        self.sess_ids = sess_ids
        self.ts = np.array(ts, dtype=np.float64)
        z = np.load(_CACHE / f"{self.qid}.npz")
        self.doc_embs, self.q_emb = z["doc_embs"], z["q_emb"]
        assert len(self.doc_embs) == len(contents), \
            f"{self.qid}: cache/{len(self.doc_embs)} vs rebuilt/{len(contents)}"

    def base_scores(self, smooth: float = SMOOTH, w_lex: float = W_LEX,
                    t_bonus: float = T_BONUS) -> np.ndarray:
        sem = self.doc_embs @ self.q_emb.astype(np.float32)
        if smooth:
            order_t = np.argsort(self.ts, kind="stable")
            nb = np.zeros_like(sem)
            for pos, i in enumerate(order_t):
                for nb_pos in (pos - 1, pos + 1):
                    if 0 <= nb_pos < len(order_t):
                        j = order_t[nb_pos]
                        if abs(self.ts[i] - self.ts[j]) <= 3600 and sem[j] > nb[i]:
                            nb[i] = sem[j]
            sem = sem + smooth * nb
        lex = np.zeros_like(sem)
        if w_lex:
            doc_tf, doc_len = [], []
            for c in self.contents:
                toks = _bm25_tokens(c)
                tf: dict[str, int] = {}
                for t in toks:
                    tf[t] = tf.get(t, 0) + 1
                doc_tf.append(tf)
                doc_len.append(len(toks))
            lex = _bm25_scores(self.question, doc_tf, doc_len)
        scores = W_SEM * sem + w_lex * lex
        if t_bonus:
            wins = query_time_windows(self.question)
            if wins:
                scores = scores + np.array(
                    [t_bonus if any(lo <= t <= hi for lo, hi in wins) else 0.0
                     for t in self.ts], dtype=np.float32)
        return scores

    def coverage(self, top_idx, ks=(10, 20, 50, 200)):
        out = {}
        for k in ks:
            got = {self.sess_ids[i] for i in top_idx[:k]}
            out[k] = (bool(self.answer_sessions & got),
                      self.answer_sessions <= got)
        return out


def session_pull(q: Q, scores: np.ndarray, seeds: int, k: int = 200) -> list[int]:
    """Whole-session expansion: top ``seeds`` messages pull their entire
    sessions (in seed order); remaining slots fill by message rank."""
    order = np.argsort(-scores, kind="stable")
    by_sess: dict[str, list[int]] = {}
    for i, s in enumerate(q.sess_ids):
        by_sess.setdefault(s, []).append(i)
    selected, seen = [], set()
    for idx in order[:seeds]:
        for j in by_sess[q.sess_ids[int(idx)]]:
            if j not in seen and len(selected) < k:
                seen.add(j)
                selected.append(j)
    for idx in order:
        if len(selected) >= k:
            break
        if int(idx) not in seen:
            seen.add(int(idx))
            selected.append(int(idx))
    return selected


def run(dataset, variant, ks=(10, 20, 50, 200)):
    stats: dict[str, dict[int, list[int]]] = {}
    for question in dataset:
        if question["question_id"].endswith("_abs"):
            continue
        q = Q(question)
        top = variant(q)
        cov = q.coverage(top, ks)
        s = stats.setdefault(q.qtype, {k: [0, 0, 0] for k in ks})
        for k in ks:
            s[k][0] += 1
            s[k][1] += int(cov[k][0])
            s[k][2] += int(cov[k][1])
    return stats


def show(name, stats, ks=(10, 50, 200)):
    tot = {k: [0, 0, 0] for k in ks}
    for s in stats.values():
        for k in ks:
            for x in range(3):
                tot[k][x] += s[k][x]
    line = "  ".join(
        f"any@{k}={tot[k][1] / tot[k][0]:.1%} all@{k}={tot[k][2] / tot[k][0]:.1%}"
        for k in ks)
    print(f"{name:<38} {line}")
    return tot


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--analyze", action="store_true")
    ap.add_argument("--variants", action="store_true")
    args = ap.parse_args()
    dataset = json.loads(_DATASET.read_text(encoding="utf-8"))

    if args.analyze:
        stats = run(dataset, lambda q: np.argsort(-q.base_scores(), kind="stable"))
        print("current config — evidence-session coverage by type (any/all @50):")
        for t, s in sorted(stats.items()):
            print(f"  {t:<28} n={s[50][0]:<4} any@10={s[10][1]/s[10][0]:.1%} "
                  f"any@50={s[50][1]/s[50][0]:.1%} all@50={s[50][2]/s[50][0]:.1%} "
                  f"all@200={s[200][2]/s[200][0]:.1%}")
        show("OVERALL", stats)

    if args.variants:
        base = lambda q: np.argsort(-q.base_scores(), kind="stable")  # noqa: E731
        show("base (smooth .3, lex .05, tb .1)", run(dataset, base))
        show("no smoothing", run(dataset, lambda q: np.argsort(-q.base_scores(smooth=0.0), kind="stable")))
        show("smooth .5", run(dataset, lambda q: np.argsort(-q.base_scores(smooth=0.5), kind="stable")))
        show("pure semantic", run(dataset, lambda q: np.argsort(-q.base_scores(w_lex=0.0), kind="stable")))
        show("lex .1", run(dataset, lambda q: np.argsort(-q.base_scores(w_lex=0.1), kind="stable")))
        for seeds in (10, 20, 40):
            show(f"session_pull seeds={seeds}",
                 run(dataset, lambda q, s=seeds: session_pull(q, q.base_scores(), s)))


if __name__ == "__main__":
    main()
