"""
LOCOMO retrieval eval — the real snap-research/locomo dataset, judge-free.

Runs the LOCOMO long-conversation benchmark (10 multi-session conversations,
~1,986 questions) against a memory system and scores **evidence retrieval**:
for each question, did top-k recall surface the dialogue turn(s) the dataset
marks as evidence for the answer?

This is deterministic and judge-free, like ``memory_eval.py``. It is NOT the
LLM-judge QA accuracy that Mem0/Zep publish (that protocol generates an answer
and grades it with a judge model); it isolates the retrieval half, which is the
part a memory layer is responsible for. Both numbers can be reported side by
side once the judged protocol is added.

Scoring:
  - Primary:   evidence_hit@k  — any gold-evidence dia_id appears in top-k
  - Strict:    evidence_all@k  — all gold-evidence dia_ids appear in top-k
  - Secondary: answer_sub@k    — gold answer string appears verbatim in a
                                 retrieved memory (weak for temporal answers,
                                 reported for continuity with memory_eval)

Category 5 (adversarial / unanswerable) tests refusal, a generation property,
so it is excluded from the headline and reported separately.

Usage (from the agentmem repo root)::

    python -m benchmarks.locomo_eval --limit 1        # smoke test, 1 conversation
    python -m benchmarks.locomo_eval                  # full run, 10 conversations
    python -m benchmarks.locomo_eval --k 10 --out results/locomo_lians.json
"""
from __future__ import annotations

import argparse
import json
import re
import statistics
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

_REPO_ROOT = Path(__file__).resolve().parent.parent
# The published-SDK package (LocalLiansClient) lives in sdk/python; the server
# package in src/ shadows the same import name, so the SDK path must win.
sys.path.insert(0, str(_REPO_ROOT / "sdk" / "python"))

_DATA = Path(__file__).resolve().parent / "data" / "locomo10.json"

CATEGORY_NAMES = {
    1: "multi-hop",
    2: "temporal",
    3: "open-domain",
    4: "single-hop",
    5: "adversarial",
}

_DIALOGUE_ID = re.compile(r"D\d+:\d+")


def _normalize_evidence(values: list[Any]) -> list[str]:
    """Expand LoCoMo annotations that pack several dia_ids into one string.

    Four answerable questions use values such as ``"D8:6; D9:17"`` or
    ``"D22:1 D22:2 D9:10 D9:11"``. Treating those as literal IDs creates
    impossible false misses, so normalize them at the dataset boundary.
    """
    out: list[str] = []
    for value in values:
        text = str(value or "")
        ids = _DIALOGUE_ID.findall(text)
        for evidence_id in ids or ([text] if text else []):
            if evidence_id not in out:
                out.append(evidence_id)
    return out

_STOPWORDS = {
    "a", "an", "and", "are", "as", "at", "be", "been", "but", "by", "did",
    "do", "does", "for", "from", "had", "has", "have", "he", "her", "hers",
    "him", "his", "how", "i", "in", "is", "it", "its", "me", "my", "of",
    "on", "or", "our", "she", "so", "that", "the", "their", "them", "they",
    "this", "to", "was", "we", "were", "what", "when", "where", "which",
    "who", "why", "will", "with", "you", "your",
}


def _query_variants(query: str, max_variants: int = 4) -> list[str]:
    original = " ".join(query.strip().split())
    words = re.findall(r"[a-z0-9][a-z0-9'-]*", original.lower())
    keywords = [word for word in words if word not in _STOPWORDS]
    variants = [original]
    if len(keywords) >= 2:
        variants.append(" ".join(keywords))
    if re.search(r"\bwhen\b|\bhow long\b|\bbefore\b|\bafter\b|\brecent|\bfirst\b|\blast\b", original, re.I):
        variants.append(f"{' '.join(keywords)} date time chronology")
    if re.search(r"\b(both|all|activities|events|ways|types|kinds|pieces|items|places|books|artists|instruments)\b", original, re.I):
        variants.append(f"{' '.join(keywords)} complete history examples")
    if re.search(r"\b(would|likely|why|how did|relationship|identity|career|personality|feel|attitude|say|advice|offer|plan)\b", original, re.I):
        variants.append(f"{' '.join(keywords)} background preference reason evidence")
    seen: set[str] = set()
    return [
        variant for variant in variants
        if variant and not (variant.lower() in seen or seen.add(variant.lower()))
    ][:max_variants]


def _fused_recall(client, agent_id: str, query: str, k: int,
                  query_expansion: bool,
                  include_context: bool = False,
                  strategy: str = "standard",
                  max_query_variants: int = 4) -> dict[str, Any]:
    if strategy == "adaptive":
        return client.recall(
            agent_id=agent_id, query=query, k=k,
            include_context=include_context, strategy="adaptive",
            max_query_variants=max_query_variants,
        )
    variants = _query_variants(query) if query_expansion else [query]
    fused: dict[str, dict[str, Any]] = {}
    as_of = None
    total_candidates = 0
    for query_index, variant in enumerate(variants):
        result = client.recall(
            agent_id=agent_id, query=variant, k=k,
            include_context=include_context,
        )
        as_of = as_of or result.get("as_of")
        total_candidates = max(total_candidates, int(result.get("total_candidates") or 0))
        for rank, memory in enumerate(result.get("memories") or []):
            key = str(memory.get("id") or memory.get("metadata", {}).get("dia_id") or "")
            if not key:
                continue
            current = fused.setdefault(key, {
                "memory": memory,
                "rrf": 0.0,
                "best_score": 0.0,
                "queries": [],
            })
            current["rrf"] += (1.2 if query_index == 0 else 1.0) / (61 + rank)
            current["best_score"] = max(current["best_score"], float(memory.get("score") or 0))
            current["queries"].append(variant)
    if not fused:
        return {"memories": [], "as_of": as_of, "total_candidates": total_candidates}
    ceiling = max(item["rrf"] for item in fused.values())
    memories = []
    for item in fused.values():
        memory = dict(item["memory"])
        memory["score"] = 0.8 * item["rrf"] / ceiling + 0.2 * item["best_score"]
        metadata = dict(memory.get("metadata") or {})
        metadata["_retrieval_queries"] = item["queries"]
        memory["metadata"] = metadata
        memories.append(memory)
    memories.sort(key=lambda memory: float(memory.get("score") or 0), reverse=True)
    return {
        "memories": memories[:k],
        "as_of": as_of,
        "total_candidates": total_candidates,
        "query_variants": variants,
    }

_DATE_FORMATS = (
    "%I:%M %p on %d %B, %Y",   # "1:56 pm on 8 May, 2023"
    "%H:%M on %d %B, %Y",
    "%d %B, %Y",
)


def _parse_session_time(raw: str) -> datetime:
    s = raw.strip()
    for fmt in _DATE_FORMATS:
        try:
            return datetime.strptime(s, fmt).replace(tzinfo=timezone.utc)
        except ValueError:
            continue
    raise ValueError(f"unparseable LOCOMO session date: {raw!r}")


def _turn_content(turn: dict[str, Any]) -> str:
    text = (turn.get("text") or "").strip()
    caption = (turn.get("blip_caption") or "").strip()
    body = f"{turn['speaker']}: {text}" if text else f"{turn['speaker']}:"
    if caption:
        body += f" [shared a photo: {caption}]"
    return body


def iter_sessions(conv: dict[str, Any]):
    """Yield (session_index, datetime, turns) in chronological order."""
    idxs = sorted(
        int(k.split("_")[1])
        for k, v in conv.items()
        if k.startswith("session_") and isinstance(v, list)
    )
    for i in idxs:
        when = _parse_session_time(conv[f"session_{i}_date_time"])
        yield i, when, conv[f"session_{i}"]


def ingest_conversation(client, agent_id: str, conv: dict[str, Any]) -> int:
    """Add every turn as an event-timed memory; returns turn count.

    One ``add_batch`` per session — contents are embedded in a single model
    pass, which is the difference between ~25 min and ~2 min per conversation
    on a CPU-only local model."""
    n = 0
    for _, when, turns in iter_sessions(conv):
        client.add_batch(agent_id, [
            {
                "content": _turn_content(turn),
                # preserve in-session order without inventing timestamps
                "event_time": when + timedelta(seconds=j),
                "metadata": {"dia_id": turn.get("dia_id", "")},
            }
            for j, turn in enumerate(turns)
        ])
        n += len(turns)
    return n


def _retrieved_ids(res: Any) -> list[str]:
    memories = res.get("memories", []) if isinstance(res, dict) else []
    out = []
    for m in memories:
        meta = m.get("metadata") or {}
        out.append(str(meta.get("dia_id") or ""))
    return out


def _retrieved_texts(res: Any) -> list[str]:
    memories = res.get("memories", []) if isinstance(res, dict) else []
    return [(m.get("content") or "").lower() for m in memories]


def run_locomo(client, dataset: list[dict[str, Any]], k: int = 10,
               limit: int | None = None, reuse_db: bool = False,
               dump_candidates: bool = False,
               query_expansion: bool = False,
               include_context: bool = False,
               strategy: str = "standard",
               max_query_variants: int = 4) -> dict[str, Any]:
    stats: dict[int, dict[str, int]] = {}
    detail: list[dict[str, Any]] = []
    samples = dataset[:limit] if limit else dataset
    latencies_ms: list[float] = []
    token_estimates: list[int] = []

    for sample in samples:
        agent = f"locomo-{sample['sample_id']}"
        t0 = time.time()
        already = False
        if reuse_db:
            probe = client.recall(agent_id=agent, query="hello", k=1)
            already = bool(probe.get("memories")) if isinstance(probe, dict) else False
        if already:
            print(f"  reusing ingested db for {agent}", flush=True)
        else:
            n_turns = ingest_conversation(client, agent, sample["conversation"])
            print(f"  ingested {n_turns} turns for {agent} "
                  f"({time.time() - t0:.1f}s)", flush=True)

        for q in sample["qa"]:
            evidence = _normalize_evidence(q.get("evidence") or [])
            if not evidence:
                continue
            cat = int(q.get("category", 0))
            recall_t0 = time.perf_counter()
            res = _fused_recall(
                client, agent, q["question"], k, query_expansion,
                include_context=include_context,
                strategy=strategy,
                max_query_variants=max_query_variants,
            )
            wall_latency_ms = (time.perf_counter() - recall_t0) * 1000
            latencies_ms.append(float(res.get("latency_ms") or wall_latency_ms))
            returned_tokens = res.get("token_estimate")
            if not returned_tokens:
                returned_tokens = sum(
                    max(1, len(str(memory.get("content") or "")) // 4)
                    for memory in (res.get("memories") or [])
                )
            token_estimates.append(int(returned_tokens))
            got_ids = _retrieved_ids(res)
            texts = _retrieved_texts(res)

            hit_any = any(e in got_ids for e in evidence)
            hit_all = all(e in got_ids for e in evidence)
            ans = q.get("answer")
            ans_sub = (
                any(str(ans).lower() in t for t in texts)
                if ans is not None else False
            )

            s = stats.setdefault(cat, {"n": 0, "any": 0, "all": 0, "sub": 0})
            s["n"] += 1
            s["any"] += int(hit_any)
            s["all"] += int(hit_all)
            s["sub"] += int(ans_sub)
            row = {
                "sample": sample["sample_id"], "category": cat,
                "question": q["question"], "evidence": evidence,
                "hit_any": hit_any, "hit_all": hit_all, "answer_sub": ans_sub,
                "latency_ms": round(latencies_ms[-1], 3),
                "token_estimate": token_estimates[-1],
            }
            if dump_candidates:
                row["candidates"] = [
                    {
                        "id": str(memory.get("id") or ""),
                        "content": memory.get("content") or "",
                        "score": memory.get("score"),
                        "event_time": memory.get("event_time"),
                        "metadata": memory.get("metadata") or {},
                        "context_before": memory.get("context_before"),
                        "context_after": memory.get("context_after"),
                        "context_before_2": memory.get("context_before_2"),
                        "context_after_2": memory.get("context_after_2"),
                        "context_before_id": memory.get("context_before_id"),
                        "context_after_id": memory.get("context_after_id"),
                        "context_before_2_id": memory.get("context_before_2_id"),
                        "context_after_2_id": memory.get("context_after_2_id"),
                        "context_before_metadata": memory.get("context_before_metadata") or {},
                        "context_after_metadata": memory.get("context_after_metadata") or {},
                        "context_before_2_metadata": memory.get("context_before_2_metadata") or {},
                        "context_after_2_metadata": memory.get("context_after_2_metadata") or {},
                    }
                    for memory in (res.get("memories", []) if isinstance(res, dict) else [])
                ]
            detail.append(row)

    headline_cats = [c for c in stats if c != 5]
    hn = sum(stats[c]["n"] for c in headline_cats)
    hany = sum(stats[c]["any"] for c in headline_cats)
    hall = sum(stats[c]["all"] for c in headline_cats)

    return {
        "benchmark": "LOCOMO (snap-research locomo10)",
        "protocol": "judge-free evidence retrieval",
        "k": k,
        "strategy": strategy,
        "max_query_variants": max_query_variants,
        "conversations": len(samples),
        "questions_scored": sum(s["n"] for s in stats.values()),
        "headline": {
            "categories": "1-4 (adversarial excluded)",
            "n": hn,
            "evidence_hit_at_k": round(hany / hn, 4) if hn else 0.0,
            "evidence_all_at_k": round(hall / hn, 4) if hn else 0.0,
        },
        "efficiency": {
            "latency_p50_ms": round(statistics.median(latencies_ms), 3) if latencies_ms else 0.0,
            "latency_p95_ms": round(
                sorted(latencies_ms)[min(len(latencies_ms) - 1, int(len(latencies_ms) * 0.95))], 3
            ) if latencies_ms else 0.0,
            "latency_mean_ms": round(statistics.fmean(latencies_ms), 3) if latencies_ms else 0.0,
            "mean_returned_tokens": round(statistics.fmean(token_estimates), 2) if token_estimates else 0.0,
        },
        "by_category": {
            CATEGORY_NAMES.get(c, str(c)): {
                "n": s["n"],
                "evidence_hit_at_k": round(s["any"] / s["n"], 4),
                "evidence_all_at_k": round(s["all"] / s["n"], 4),
                "answer_sub_at_k": round(s["sub"] / s["n"], 4),
            }
            for c, s in sorted(stats.items())
        },
        "detail": detail,
    }


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--dataset", default=str(_DATA))
    ap.add_argument("--k", type=int, default=10)
    ap.add_argument("--limit", type=int, default=None,
                    help="only run the first N conversations (smoke test)")
    ap.add_argument("--conv", type=int, default=None,
                    help="run only conversation index N (for checkpointed runs)")
    ap.add_argument("--reuse-db", action="store_true",
                    help="skip ingest when the agent already has memories in --db")
    ap.add_argument("--db", default=None,
                    help="sqlite path for LocalLiansClient (default: temp)")
    ap.add_argument("--embeddings", default="sentence-transformers",
                    choices=["sentence-transformers", "local", "openai", "voyage"],
                    help="embedding provider; 'local' is the token-hash TEST stub "
                         "and must not be used for publishable numbers")
    ap.add_argument("--out", default=None, help="write full JSON report here")
    ap.add_argument("--dump-candidates", action="store_true",
                    help="include retrieved IDs/content/scores for leakage-free offline reranking")
    ap.add_argument("--query-expansion", action="store_true",
                    help="retrieve deterministic query variants and fuse them with weighted RRF")
    ap.add_argument("--include-context", action="store_true",
                    help="promote attributed adjacent turns into the candidate pool")
    ap.add_argument("--strategy", choices=["standard", "adaptive"], default="standard",
                    help="engine recall strategy (adaptive uses bounded internal query planning)")
    ap.add_argument("--max-query-variants", type=int, default=4, choices=range(1, 5),
                    help="maximum adaptive retrieval facets")
    args = ap.parse_args()

    dataset = json.loads(Path(args.dataset).read_text(encoding="utf-8"))
    if args.conv is not None:
        dataset = [dataset[args.conv]]
    from lians import LocalLiansClient  # noqa: deferred so --help stays fast

    kwargs: dict[str, Any] = {"embedding_provider": args.embeddings}
    if args.db:
        kwargs["db_path"] = args.db
    with LocalLiansClient(**kwargs) as client:
        report = run_locomo(client, dataset, k=args.k, limit=args.limit,
                            reuse_db=args.reuse_db,
                            dump_candidates=args.dump_candidates,
                            query_expansion=args.query_expansion,
                            include_context=args.include_context,
                            strategy=args.strategy,
                            max_query_variants=args.max_query_variants)

    print()
    print(f"LOCOMO · {report['conversations']} conversations · "
          f"{report['questions_scored']} questions · k={report['k']}")
    h = report["headline"]
    print(f"HEADLINE (cats 1-4, n={h['n']}): "
          f"evidence_hit@{report['k']} = {h['evidence_hit_at_k']:.1%}   "
          f"evidence_all@{report['k']} = {h['evidence_all_at_k']:.1%}")
    e = report["efficiency"]
    print(f"EFFICIENCY ({report['strategy']}): p50={e['latency_p50_ms']:.1f}ms "
          f"p95={e['latency_p95_ms']:.1f}ms mean_tokens={e['mean_returned_tokens']:.0f}")
    for name, s in report["by_category"].items():
        print(f"  {name:<12} n={s['n']:<4} hit@k={s['evidence_hit_at_k']:.1%} "
              f"all@k={s['evidence_all_at_k']:.1%} "
              f"answer_sub={s['answer_sub_at_k']:.1%}")

    if args.out:
        out = Path(args.out)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(report, indent=2), encoding="utf-8")
        print(f"\nfull report -> {out}")


if __name__ == "__main__":
    main()
