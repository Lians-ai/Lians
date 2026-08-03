"""
Offline LOCOMO ranking lab — replay recall experiments without re-ingesting.

The checkpointed eval runs (``locomo_eval.py --db results/locomo_dbs/conv_N.sqlite``)
leave behind every turn's bge embedding. On SQLite the ANN prefetch always
falls back to a full scan, so the live candidate pool is the entire corpus —
which means top-k selection can be reproduced *exactly* offline from the
stored vectors plus one batched pass of query embeddings. That turns a
30-minute eval run into a ~5-second numpy experiment, so ranking ideas can be
grid-searched before any of them touch ``src/lians``.

Fidelity notes (why the replay is exact, not approximate):
  - candidates = all live_facts rows (SQLite has no pgvector; the ANN
    order_by raises and hybrid_recall scans everything);
  - recency decay is ~2**-38 on this 2023 corpus and importance is the
    uniform default 0.5 — both are additive constants, invariant under both
    sorting and MMR's min-max normalization, so they are omitted;
  - BM25 and MMR are re-implemented bit-for-bit (tokenizer is imported from
    ``lians.ranking``); ``--validate`` checks replayed hit/all flags
    against the real runs' per-question detail.

Usage (from the agentmem repo root):
    python -m benchmarks.locomo_replay --build-cache --convs 0,1,2,3,4,5,6,9
    python -m benchmarks.locomo_replay --validate
    python -m benchmarks.locomo_replay --experiments
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sqlite3
import sys
from pathlib import Path

import numpy as np

os.environ.setdefault("EMBEDDING_PROVIDER", "sentence-transformers")
os.environ.setdefault("MASTER_ENCRYPTION_KEY", "")
os.environ.setdefault("AGENTMEM_ALLOW_UNENCRYPTED", "true")

_REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO))
sys.path.insert(0, str(_REPO / "src"))

from lians.ranking import _bm25_tokens, _BM25_K1, _BM25_B, _BM25_AVG_DOC_LEN  # noqa: E402

_DATA = _REPO / "benchmarks" / "data" / "locomo10.json"
_DBS = _REPO / "results" / "locomo_dbs"
_CACHE = _REPO / "results" / "replay"
_BGE_PREFIX = "Represent this sentence for searching relevant passages: "

W_SEM, W_LEX = 0.50, 0.20
_DIA = re.compile(r"^D(\d+):(\d+)$")


# ── cache build ───────────────────────────────────────────────────────────────

def _load_corpus_rows(db_path: Path) -> list[dict]:
    con = sqlite3.connect(db_path)
    rows = con.execute(
        "select content_encrypted, embedding, metadata from live_facts order by rowid"
    ).fetchall()
    con.close()
    out = []
    for content, emb, meta in rows:
        meta = json.loads(meta) if meta else {}
        out.append({
            "content": (bytes(content).decode("utf-8", "replace") if content else ""),
            "embedding": json.loads(emb) if emb else None,
            "dia_id": str(meta.get("dia_id") or ""),
        })
    return out


def build_cache(convs: list[int]) -> None:
    from sentence_transformers import SentenceTransformer

    _CACHE.mkdir(parents=True, exist_ok=True)
    dataset = json.loads(_DATA.read_text(encoding="utf-8"))
    model = SentenceTransformer("BAAI/bge-large-en-v1.5")

    for n in convs:
        db = _DBS / f"conv_{n}.sqlite"
        rows = _load_corpus_rows(db)
        embs = np.array([r["embedding"] for r in rows], dtype=np.float32)

        qa = [q for q in dataset[n]["qa"] if q.get("evidence")]
        questions = [q["question"] for q in qa]
        q_raw = model.encode(questions, normalize_embeddings=True,
                             batch_size=32, show_progress_bar=False)
        q_pre = model.encode([_BGE_PREFIX + q for q in questions],
                             normalize_embeddings=True, batch_size=32,
                             show_progress_bar=False)

        np.savez_compressed(
            _CACHE / f"conv_{n}.npz",
            doc_embs=embs,
            q_raw=q_raw.astype(np.float32),
            q_pre=q_pre.astype(np.float32),
        )
        (_CACHE / f"conv_{n}.meta.json").write_text(json.dumps({
            "docs": [{"dia_id": r["dia_id"], "content": r["content"]} for r in rows],
            "qa": [{
                "question": q["question"],
                "evidence": [str(e) for e in q["evidence"]],
                "category": int(q.get("category", 0)),
            } for q in qa],
        }), encoding="utf-8")
        print(f"conv_{n}: cached {len(rows)} docs, {len(qa)} questions", flush=True)


# ── replay core ───────────────────────────────────────────────────────────────

class Conv:
    def __init__(self, n: int):
        z = np.load(_CACHE / f"conv_{n}.npz")
        meta = json.loads((_CACHE / f"conv_{n}.meta.json").read_text(encoding="utf-8"))
        self.n = n
        self.doc_embs = z["doc_embs"]           # already L2-normalized by bge
        self.q_raw, self.q_pre = z["q_raw"], z["q_pre"]
        self.dia_ids = [d["dia_id"] for d in meta["docs"]]
        self.contents = [d["content"] for d in meta["docs"]]
        self.qa = meta["qa"]
        # doc-doc similarity for MMR (n≈700 → fine to materialize)
        self.doc_sim = self.doc_embs @ self.doc_embs.T
        # session/turn coordinates for neighbor expansion
        self.coord = {}
        for i, d in enumerate(self.dia_ids):
            m = _DIA.match(d)
            if m:
                self.coord[i] = (int(m.group(1)), int(m.group(2)))
        self.by_coord = {c: i for i, c in self.coord.items()}
        # BM25 doc stats, tokenized once with the engine's own tokenizer
        self.doc_tf: list[dict[str, int]] = []
        self.doc_len: list[int] = []
        for c in self.contents:
            toks = _bm25_tokens(c)
            tf: dict[str, int] = {}
            for t in toks:
                tf[t] = tf.get(t, 0) + 1
            self.doc_tf.append(tf)
            self.doc_len.append(len(toks))

    def bm25(self, query: str) -> np.ndarray:
        q_tokens = set(_bm25_tokens(query))
        out = np.zeros(len(self.doc_tf), dtype=np.float32)
        if not q_tokens:
            return out
        for i, (tf, dl) in enumerate(zip(self.doc_tf, self.doc_len)):
            if not dl:
                continue
            s = 0.0
            denom_len = _BM25_K1 * (1 - _BM25_B + _BM25_B * dl / _BM25_AVG_DOC_LEN)
            for t in q_tokens:
                f = tf.get(t, 0)
                if f:
                    s += (f * (_BM25_K1 + 1)) / (f + denom_len)
            out[i] = s / len(q_tokens)
        return out


def mmr_select(order: np.ndarray, scores: np.ndarray, doc_sim: np.ndarray,
               k: int, lam: float) -> list[int]:
    """Replica of ranking._mmr_select over pre-sorted candidate indices."""
    if lam >= 1.0 or len(order) <= k:
        return list(order[:k])
    lo = scores[order[-1]]
    span = (scores[order[0]] - lo) or 1.0
    selected = [order[0]]
    remaining = list(order[1:])
    max_sim = doc_sim[order[0]][remaining].copy()
    while remaining and len(selected) < k:
        rel = (scores[remaining] - lo) / span
        vals = lam * rel - (1.0 - lam) * max_sim
        j = int(np.argmax(vals))
        chosen = remaining.pop(j)
        selected.append(chosen)
        max_sim = np.delete(max_sim, j)
        if remaining:
            max_sim = np.maximum(max_sim, doc_sim[chosen][remaining])
    return selected


def neighbor_expand(order: np.ndarray, conv: Conv, k: int, window: int) -> list[int]:
    """Interleave each seed with its same-session adjacent turns.

    Dialogue evidence is conversational: a statement and its reply, or a
    question and its answer, sit at turn ±1..±window. Walking the ranked list
    and pulling each hit's neighbors assembles multi-turn evidence that pure
    relevance ranking scatters past rank k.
    """
    selected: list[int] = []
    seen = set()
    for idx in order:
        if len(selected) >= k:
            break
        if idx not in seen:
            seen.add(idx)
            selected.append(int(idx))
        c = conv.coord.get(int(idx))
        if c is None:
            continue
        for d in range(1, window + 1):
            for t in (c[1] + d, c[1] - d):
                nb = conv.by_coord.get((c[0], t))
                if nb is not None and nb not in seen and len(selected) < k:
                    seen.add(nb)
                    selected.append(nb)
    return selected


def _session_cap_select(order: np.ndarray, conv: Conv, k: int, cap: int) -> list[int]:
    """Soft per-session cap: pass 1 takes at most ``cap`` turns per session in
    rank order; pass 2 backfills remaining slots by rank. Diversifies across
    sessions without ever leaving slots empty."""
    selected: list[int] = []
    per_session: dict[int, int] = {}
    deferred: list[int] = []
    for idx in order:
        if len(selected) >= k:
            break
        sess = conv.coord.get(int(idx), (None,))[0]
        if sess is not None and per_session.get(sess, 0) >= cap:
            deferred.append(int(idx))
            continue
        per_session[sess] = per_session.get(sess, 0) + 1
        selected.append(int(idx))
    for idx in deferred:
        if len(selected) >= k:
            break
        selected.append(idx)
    return selected


def _seed_append_neighbors(order: np.ndarray, conv: Conv, k: int, seeds: int) -> list[int]:
    """Top ``seeds`` by rank keep their slots; the remaining k-seeds slots are
    filled with the seeds' same-session adjacent turns (never displacing a
    seed, unlike interleaved expansion)."""
    selected = [int(i) for i in order[:seeds]]
    seen = set(selected)
    for idx in list(selected):
        c = conv.coord.get(idx)
        if c is None:
            continue
        for t in (c[1] + 1, c[1] - 1):
            nb = conv.by_coord.get((c[0], t))
            if nb is not None and nb not in seen and len(selected) < k:
                seen.add(nb)
                selected.append(nb)
    for idx in order[seeds:]:
        if len(selected) >= k:
            break
        if int(idx) not in seen:
            seen.add(int(idx))
            selected.append(int(idx))
    return selected


def run_config(convs: list[Conv], k: int = 10, prefix: bool = True,
               w_sem: float = W_SEM, w_lex: float = W_LEX,
               mmr_lam: float = 1.0, nb_window: int = 0,
               rrf: bool = False, lex_norm: bool = False,
               smooth: float = 0.0, session_cap: int = 0,
               nb_seeds: int = 0) -> dict:
    """Score one ranking configuration; returns headline + per-category rates."""
    stats: dict[int, list[int]] = {}
    for conv in convs:
        Q = conv.q_pre if prefix else conv.q_raw
        sem_all = Q @ conv.doc_embs.T
        for qi, q in enumerate(conv.qa):
            sem = sem_all[qi]
            if smooth:
                # Dialogue-context smoothing: a turn inherits a fraction of its
                # strongest same-session adjacent turn's semantic match, so the
                # low-vocabulary half of an exchange rides on the matching half.
                nb_best = np.zeros_like(sem)
                for i in range(len(sem)):
                    c = conv.coord.get(i)
                    if c is None:
                        continue
                    for t in (c[1] + 1, c[1] - 1):
                        j = conv.by_coord.get((c[0], t))
                        if j is not None and sem[j] > nb_best[i]:
                            nb_best[i] = sem[j]
                sem = sem + smooth * nb_best
            lex = conv.bm25(q["question"]) if (w_lex or rrf) else np.zeros_like(sem)
            if lex_norm and lex.max() > 0:
                lex = lex / lex.max()
            if rrf:
                r_sem = np.empty(len(sem), dtype=np.int32)
                r_sem[np.argsort(-sem, kind="stable")] = np.arange(len(sem))
                r_lex = np.empty(len(lex), dtype=np.int32)
                r_lex[np.argsort(-lex, kind="stable")] = np.arange(len(lex))
                scores = 1.0 / (60 + r_sem) + 1.0 / (60 + r_lex)
            else:
                scores = w_sem * sem + w_lex * lex
            order = np.argsort(-scores, kind="stable")
            if nb_seeds:
                top = _seed_append_neighbors(order, conv, k, nb_seeds)
            elif session_cap:
                top = _session_cap_select(order, conv, k, session_cap)
            elif nb_window:
                top = neighbor_expand(order, conv, k, nb_window)
            elif mmr_lam < 1.0:
                top = mmr_select(order, scores, conv.doc_sim, k, mmr_lam)
            else:
                top = list(order[:k])
            got = {conv.dia_ids[i] for i in top}
            ev = q["evidence"]
            cat = q["category"]
            s = stats.setdefault(cat, [0, 0, 0])
            s[0] += 1
            s[1] += int(any(e in got for e in ev))
            s[2] += int(all(e in got for e in ev))
    head = [s for c, s in stats.items() if c != 5]
    n = sum(s[0] for s in head)
    return {
        "n": n,
        "hit": sum(s[1] for s in head) / n,
        "all": sum(s[2] for s in head) / n,
        "by_cat": {c: (s[0], s[1] / s[0], s[2] / s[0]) for c, s in sorted(stats.items())},
    }


# ── modes ─────────────────────────────────────────────────────────────────────

def validate(nums: list[int]) -> None:
    """Replay must reproduce the real runs' per-question hit flags."""
    checks = [
        ("v2 (prefix, MMR .75)", "results/locomo_v2", dict(prefix=True, mmr_lam=0.75)),
        ("baseline (raw, no MMR)", "results/locomo", dict(prefix=False, mmr_lam=1.0)),
    ]
    for label, res_dir, cfg in checks:
        total = agree_any = agree_all = 0
        for n in nums:
            conv = Conv(n)
            real = json.loads((_REPO / res_dir / f"conv_{n}.json").read_text(encoding="utf-8"))
            real_by_q = {(d["question"], tuple(d["evidence"])): d for d in real["detail"]}
            Q = conv.q_pre if cfg["prefix"] else conv.q_raw
            sem_all = Q @ conv.doc_embs.T
            for qi, q in enumerate(conv.qa):
                d = real_by_q.get((q["question"], tuple(q["evidence"])))
                if d is None:
                    continue
                scores = W_SEM * sem_all[qi] + W_LEX * conv.bm25(q["question"])
                order = np.argsort(-scores, kind="stable")
                top = mmr_select(order, scores, conv.doc_sim, 10, cfg["mmr_lam"])
                got = {conv.dia_ids[i] for i in top}
                total += 1
                agree_any += int(any(e in got for e in q["evidence"]) == d["hit_any"])
                agree_all += int(all(e in got for e in q["evidence"]) == d["hit_all"])
        print(f"{label}: hit_any agreement {agree_any}/{total} "
              f"({agree_any / total:.1%}), hit_all {agree_all}/{total} "
              f"({agree_all / total:.1%})")


def experiments(nums: list[int]) -> None:
    convs = [Conv(n) for n in nums]

    def show(name: str, r: dict) -> None:
        cats = {1: "mh", 2: "tmp", 3: "od", 4: "sh", 5: "adv"}
        per = "  ".join(f"{cats[c]} {h:.0%}/{a:.0%}" for c, (_, h, a) in r["by_cat"].items())
        print(f"{name:<44} hit@10 {r['hit']:.1%}  all@10 {r['all']:.1%}   [{per}]")

    print(f"— replaying {len(nums)} convs, n={run_config(convs)['n']} headline questions —\n")

    show("baseline replica (raw, .5/.2, no MMR)",
         run_config(convs, prefix=False, mmr_lam=1.0))
    show("v2 replica (prefix, .5/.2, MMR .75)",
         run_config(convs, prefix=True, mmr_lam=0.75))

    print("\n· one-factor sweeps ·")
    show("prefix only, no MMR", run_config(convs, prefix=True, mmr_lam=1.0))
    for lam in (0.9, 0.8, 0.5):
        show(f"prefix, MMR lam={lam}", run_config(convs, prefix=True, mmr_lam=lam))
    show("pure semantic (w_lex=0), prefix", run_config(convs, prefix=True, w_lex=0.0))
    for wl in (0.1, 0.3, 0.5):
        show(f"prefix, w_lex={wl}", run_config(convs, prefix=True, w_lex=wl))
    show("RRF fusion (sem+bm25), prefix", run_config(convs, prefix=True, rrf=True))

    print("\n· neighbor expansion (session-adjacent turns) ·")
    for pre in (True, False):
        for w in (1, 2, 3):
            show(f"{'prefix' if pre else 'raw'}, neighbors w={w}",
                 run_config(convs, prefix=pre, nb_window=w))

    print("\n· retrieval ceiling (evidence within top-N by blended score) ·")
    for kk in (10, 20, 50):
        r = run_config(convs, k=kk, prefix=True)
        print(f"  top-{kk}: hit {r['hit']:.1%}  all {r['all']:.1%}")


def experiments2(nums: list[int]) -> None:
    convs = [Conv(n) for n in nums]

    def show(name: str, r: dict) -> None:
        cats = {1: "mh", 2: "tmp", 3: "od", 4: "sh", 5: "adv"}
        per = "  ".join(f"{cats[c]} {h:.0%}/{a:.0%}" for c, (_, h, a) in r["by_cat"].items())
        print(f"{name:<48} hit@10 {r['hit']:.1%}  all@10 {r['all']:.1%}   [{per}]")

    print("· lexical dose, normalized BM25 ·")
    for wl in (0.05, 0.1, 0.15, 0.2, 0.3):
        show(f"prefix, lex_norm, w_lex={wl}",
             run_config(convs, w_lex=wl, lex_norm=True))
    print("\n· fine w_lex grid, raw BM25 ·")
    for wl in (0.02, 0.05, 0.08, 0.12):
        show(f"prefix, w_lex={wl}", run_config(convs, w_lex=wl))

    print("\n· dialogue-context smoothing (base: prefix, w_lex=0.1) ·")
    for a in (0.15, 0.3, 0.5):
        show(f"smooth a={a}", run_config(convs, w_lex=0.1, smooth=a))

    print("\n· session cap (base: prefix, w_lex=0.1) ·")
    for cap in (3, 4, 5):
        show(f"session_cap={cap}", run_config(convs, w_lex=0.1, session_cap=cap))

    print("\n· seed+append neighbors (base: prefix, w_lex=0.1) ·")
    for s in (6, 7, 8):
        show(f"nb_seeds={s}", run_config(convs, w_lex=0.1, nb_seeds=s))

    print("\n· combos ·")
    show("smooth .3 + lex_norm .1", run_config(convs, w_lex=0.1, lex_norm=True, smooth=0.3))
    show("smooth .3 + cap 4", run_config(convs, w_lex=0.1, smooth=0.3, session_cap=4))
    show("smooth .3 + seeds 7", run_config(convs, w_lex=0.1, smooth=0.3, nb_seeds=7))
    show("smooth .5 + lex_norm .1", run_config(convs, w_lex=0.1, lex_norm=True, smooth=0.5))
    show("lex_norm .1 + cap 4", run_config(convs, w_lex=0.1, lex_norm=True, session_cap=4))

    print("\n· ceiling with best blend so far ·")
    for kk in (10, 20, 50):
        r = run_config(convs, k=kk, w_lex=0.1, smooth=0.3)
        print(f"  top-{kk} (smooth .3): hit {r['hit']:.1%}  all {r['all']:.1%}")


def experiments3(nums: list[int]) -> None:
    convs = [Conv(n) for n in nums]

    def show(name: str, r: dict) -> None:
        cats = {1: "mh", 2: "tmp", 3: "od", 4: "sh", 5: "adv"}
        per = "  ".join(f"{cats[c]} {h:.0%}/{a:.0%}" for c, (_, h, a) in r["by_cat"].items())
        print(f"{name:<48} hit@10 {r['hit']:.1%}  all@10 {r['all']:.1%}   [{per}]")

    print("· smoothing x lexical dose ·")
    for wl in (0.02, 0.05):
        for a in (0.3, 0.5, 0.7):
            show(f"w_lex={wl}, smooth={a}", run_config(convs, w_lex=wl, smooth=a))
    print("\n· best-blend ceilings ·")
    for wl, a in ((0.05, 0.5), (0.05, 0.3)):
        for kk in (20, 50, 100):
            r = run_config(convs, k=kk, w_lex=wl, smooth=a)
            print(f"  w_lex={wl} smooth={a} top-{kk}: hit {r['hit']:.1%}  all {r['all']:.1%}")


def rerank_probe(nums: list[int], prefetch: int = 50,
                 model_name: str = "BAAI/bge-reranker-base") -> None:
    """Cross-encoder rerank of the top-``prefetch`` blended candidates.

    Sizing probe for an optional second-stage reranker: run on 1-2 convs first
    (CPU cost ~50 pairs/question) before committing to all eight.
    """
    from sentence_transformers import CrossEncoder

    ce = CrossEncoder(model_name, max_length=384)
    for n in nums:
        conv = Conv(n)
        sem_all = conv.q_pre @ conv.doc_embs.T
        # query embedding = avg(prefixed, raw) — current best variant
        q_avg = conv.q_pre + conv.q_raw
        q_avg = q_avg / np.linalg.norm(q_avg, axis=1, keepdims=True)
        sem_all = q_avg @ conv.doc_embs.T
        stats: dict[str, dict[int, list[int]]] = {"ce": {}, "fused": {}}
        for qi, q in enumerate(conv.qa):
            sem = sem_all[qi]
            nb_best = np.zeros_like(sem)
            for i in range(len(sem)):
                c = conv.coord.get(i)
                if c is None:
                    continue
                for t in (c[1] + 1, c[1] - 1):
                    j = conv.by_coord.get((c[0], t))
                    if j is not None and sem[j] > nb_best[i]:
                        nb_best[i] = sem[j]
            scores = 0.5 * (sem + 0.3 * nb_best) + 0.05 * conv.bm25(q["question"])
            cand = list(np.argsort(-scores, kind="stable")[:prefetch])
            ce_scores = np.asarray(ce.predict(
                [(q["question"], conv.contents[i]) for i in cand],
                show_progress_bar=False))
            ce_rank = np.empty(len(cand), dtype=np.int32)
            ce_rank[np.argsort(-ce_scores, kind="stable")] = np.arange(len(cand))
            # RRF of CE rank with blend rank (cand is already blend-ordered)
            fused = 1.0 / (20 + ce_rank) + 1.0 / (20 + np.arange(len(cand)))
            tops = {
                "ce": [cand[i] for i in np.argsort(-ce_scores, kind="stable")[:10]],
                "fused": [cand[i] for i in np.argsort(-fused, kind="stable")[:10]],
            }
            for key, top in tops.items():
                got = {conv.dia_ids[i] for i in top}
                ev, cat = q["evidence"], q["category"]
                s = stats[key].setdefault(cat, [0, 0, 0])
                s[0] += 1
                s[1] += int(any(e in got for e in ev))
                s[2] += int(all(e in got for e in ev))
        for key, st in stats.items():
            head = [s for c, s in st.items() if c != 5]
            hn = sum(s[0] for s in head)
            print(f"conv_{n} {key}({model_name}, prefetch={prefetch}): "
                  f"hit@10 {sum(s[1] for s in head) / hn:.1%}  "
                  f"all@10 {sum(s[2] for s in head) / hn:.1%}", flush=True)


_ALT_QUERY_PROMPTS = {
    # model → instruction prepended to *queries* (documents always raw),
    # per each model's card.
    "mixedbread-ai/mxbai-embed-large-v1":
        "Represent this sentence for searching relevant passages: ",
    "Snowflake/snowflake-arctic-embed-l-v2.0": "query: ",
    "BAAI/bge-m3": "",
    "BAAI/bge-large-en-v1.5": _BGE_PREFIX,
}


def alt_model(nums: list[int], model_name: str) -> None:
    """Score a candidate embedding model: re-embed docs+queries offline, then
    run the tuned blend (w_lex=.05, smooth=.3). Caches per-model npz so a
    repeat run is instant."""
    from sentence_transformers import SentenceTransformer

    slug = model_name.replace("/", "__")
    prompt = _ALT_QUERY_PROMPTS.get(model_name, "")
    model = None
    convs = []
    for n in nums:
        cache = _CACHE / f"alt_{slug}_conv_{n}.npz"
        conv = Conv(n)
        if cache.exists():
            z = np.load(cache)
            conv.doc_embs, q_pre = z["doc_embs"], z["q_pre"]
        else:
            if model is None:
                model = SentenceTransformer(model_name, trust_remote_code=True)
            doc_embs = model.encode(conv.contents, normalize_embeddings=True,
                                    batch_size=32, show_progress_bar=False)
            q_pre = model.encode([prompt + q["question"] for q in conv.qa],
                                 normalize_embeddings=True, batch_size=32,
                                 show_progress_bar=False)
            np.savez_compressed(cache, doc_embs=doc_embs.astype(np.float32),
                                q_pre=q_pre.astype(np.float32))
            conv.doc_embs = doc_embs.astype(np.float32)
            q_pre = q_pre.astype(np.float32)
            print(f"  conv_{n}: embedded {len(conv.contents)} docs, "
                  f"{len(conv.qa)} queries", flush=True)
        conv.q_pre = q_pre
        conv.doc_sim = conv.doc_embs @ conv.doc_embs.T
        convs.append(conv)

    r = run_config(convs, w_lex=0.05, smooth=0.3)
    cats = {1: "mh", 2: "tmp", 3: "od", 4: "sh", 5: "adv"}
    per = "  ".join(f"{cats[c]} {h:.0%}/{a:.0%}" for c, (_, h, a) in r["by_cat"].items())
    print(f"{model_name} (blend .05/.3): hit@10 {r['hit']:.1%}  "
          f"all@10 {r['all']:.1%}   [{per}]")
    for kk in (20, 50):
        rr = run_config(convs, k=kk, w_lex=0.05, smooth=0.3)
        print(f"  top-{kk} ceiling: hit {rr['hit']:.1%}  all {rr['all']:.1%}")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--convs", default="0,1,2,3,4,5,6,9")
    ap.add_argument("--build-cache", action="store_true")
    ap.add_argument("--validate", action="store_true")
    ap.add_argument("--experiments", action="store_true")
    ap.add_argument("--experiments2", action="store_true")
    ap.add_argument("--experiments3", action="store_true")
    ap.add_argument("--rerank-probe", action="store_true")
    ap.add_argument("--prefetch", type=int, default=50)
    ap.add_argument("--rerank-model", default="BAAI/bge-reranker-base")
    ap.add_argument("--alt-model", default=None,
                    help="candidate embedding model to score offline")
    args = ap.parse_args()
    nums = [int(x) for x in args.convs.split(",")]

    if args.build_cache:
        build_cache(nums)
    if args.validate:
        validate(nums)
    if args.experiments:
        experiments(nums)
    if args.experiments2:
        experiments2(nums)
    if args.experiments3:
        experiments3(nums)
    if args.rerank_probe:
        rerank_probe(nums, prefetch=args.prefetch, model_name=args.rerank_model)
    if args.alt_model:
        alt_model(nums, args.alt_model)


if __name__ == "__main__":
    main()
