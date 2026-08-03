"""
Lifecycle eval — state reconstruction, temporal disambiguation, interjections.

Where memory_eval scores ranked recall on QA datasets, this harness tests the
agent's ability to maintain a *dynamic model* of a user or domain:

  1. **State reconstruction** — messy, interleaved preference updates over
     weeks ("I'm vegan" → "I eat fish now" → "allergic to salmon"). We then
     reconstruct a clean profile from ``snapshot(now)`` and score, per field:
     is the current value live, and is every superseded value gone (a leaked
     stale value = a zombie fact the agent would act on).
  2. **Temporal disambiguation / time-travel** — the same profile is
     reconstructed ``as_of`` past checkpoints: the belief of that era must be
     live and later values must not exist yet. Includes an out-of-order
     ingestion case (backdated fact arrives last and must not supersede).
  3. **Interjections** — a background fact dropped mid-task must be stored and
     recallable on its own, while task-focused queries stay unpolluted by it.

All scoring is deterministic (case-insensitive substring, like memory_eval) —
no LLM judge. Two modes isolate the layers:

  ``--mode keyed``  metadata keys supplied → tests the lifecycle engine alone
  ``--mode raw``    text only (passthrough adapter) → end-to-end: semantic
                    supersession must detect revisions with no keys at all

Run::

    python -m benchmarks.lifecycle_eval --mode keyed
    python -m benchmarks.lifecycle_eval --mode raw
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

_REPO = Path(__file__).resolve().parent.parent
_DATA = Path(__file__).resolve().parent / "data" / "lifecycle_scenarios.json"
_RESULTS = _REPO / "results" / "lifecycle"

DEFAULT_MODEL = "Snowflake/snowflake-arctic-embed-l-v2.0"


def _when(s: str) -> datetime:
    return datetime.fromisoformat(s + "T12:00:00").replace(tzinfo=timezone.utc)


def _contents(items: list[dict]) -> list[str]:
    return [(m.get("content") or "").lower() for m in items]


def _any(texts: list[str], needle: str) -> bool:
    return any(needle.lower() in t for t in texts)


def ingest(client, agent: str, events: list[dict], mode: str) -> None:
    for ev in events:
        meta = ev.get("keys") if mode == "keyed" else None
        client.add(
            agent_id=agent,
            content=ev["text"],
            event_time=_when(ev["date"]),
            source="conversation",
            metadata=meta or None,
        )


def score_profile(texts: list[str], profile: dict[str, dict]) -> list[dict]:
    """Score one reconstructed state (live snapshot contents) against the
    expected field→value profile. A field passes only if its current value is
    live AND no stale/absent value leaked."""
    rows = []
    for field, spec in profile.items():
        found = _any(texts, spec["current"])
        leaked = [s for s in spec.get("stale", []) + spec.get("absent", []) if _any(texts, s)]
        rows.append({
            "field": field,
            "found_current": found,
            "leaked": leaked,
            "ok": found and not leaked,
        })
    return rows


def run_eval(client, dataset: dict[str, Any], mode: str, k: int = 5) -> dict[str, Any]:
    now = datetime.now(timezone.utc)
    scenarios = []

    for sc in dataset["scenarios"]:
        agent = f"lc-{mode}-{sc['id']}"
        ingest(client, agent, sc["events"], mode)

        # 1. Profile reconstruction from the current knowledge state
        snap = client.snapshot(agent_id=agent, as_of=now)
        live = _contents(snap.get("items", []))
        profile_rows = score_profile(live, sc.get("profile", {}))

        # 2. Time-travel checkpoints
        checkpoint_rows = []
        for cp in sc.get("checkpoints", []):
            cp_snap = client.snapshot(agent_id=agent, as_of=_when(cp["as_of"]))
            cp_texts = _contents(cp_snap.get("items", []))
            for row in score_profile(cp_texts, cp["profile"]):
                checkpoint_rows.append({"as_of": cp["as_of"], **row})

        # 3. Ranked-recall probes (with optional as_of and pollution checks)
        probe_rows = []
        for q in sc.get("probes", []):
            kwargs: dict[str, Any] = {"k": q.get("k", k)}
            if q.get("as_of"):
                kwargs["as_of"] = _when(q["as_of"])
            res = client.recall(agent_id=agent, query=q["query"], **kwargs)
            texts = _contents(res.get("memories", []))
            found = _any(texts, q["answer"])
            stale_ok = not (q.get("stale") and _any(texts, q["stale"]))
            clean_ok = not (q.get("must_not_contain") and _any(texts, q["must_not_contain"]))
            probe_rows.append({
                "query": q["query"], "as_of": q.get("as_of"),
                "found": found, "stale_excluded": stale_ok, "unpolluted": clean_ok,
                "ok": found and stale_ok and clean_ok,
            })

        scenarios.append({
            "id": sc["id"], "category": sc["category"],
            "profile": profile_rows, "checkpoints": checkpoint_rows, "probes": probe_rows,
        })

    def _agg(rows: list[dict]) -> dict[str, Any]:
        n = len(rows)
        return {"total": n, "correct": sum(r["ok"] for r in rows),
                "accuracy": (sum(r["ok"] for r in rows) / n) if n else None}

    all_profile = [r for s in scenarios for r in s["profile"]]
    all_cp = [r for s in scenarios for r in s["checkpoints"]]
    all_probe = [r for s in scenarios for r in s["probes"]]
    by_cat: dict[str, list[dict]] = {}
    for s in scenarios:
        by_cat.setdefault(s["category"], []).extend(s["profile"] + s["checkpoints"] + s["probes"])

    return {
        "mode": mode,
        "k": k,
        "profile_reconstruction": _agg(all_profile),
        "zombie_fields": sum(1 for r in all_profile if r["leaked"]),
        "time_travel": _agg(all_cp),
        "probes": _agg(all_probe),
        "by_category": {c: _agg(rows) for c, rows in sorted(by_cat.items())},
        "overall": _agg(all_profile + all_cp + all_probe),
        "scenarios": scenarios,
    }


def print_report(report: dict[str, Any]) -> None:
    def pct(a):
        return "  n/a" if a["accuracy"] is None else f"{a['accuracy']:.1%} ({a['correct']}/{a['total']})"

    print(f"\n=== lifecycle_eval  mode={report['mode']}  k={report['k']} ===")
    print(f"profile reconstruction : {pct(report['profile_reconstruction'])}   "
          f"zombie fields: {report['zombie_fields']}")
    print(f"time-travel checkpoints: {pct(report['time_travel'])}")
    print(f"recall probes          : {pct(report['probes'])}")
    print("by category:")
    for cat, agg in report["by_category"].items():
        print(f"  {cat:<24} {pct(agg)}")
    print(f"OVERALL                : {pct(report['overall'])}")

    fails = []
    for s in report["scenarios"]:
        for r in s["profile"]:
            if not r["ok"]:
                why = f"leaked {r['leaked']}" if r["leaked"] else "current value not live"
                fails.append(f"  [{s['id']}] profile.{r['field']}: {why}")
        for r in s["checkpoints"]:
            if not r["ok"]:
                why = f"leaked {r['leaked']}" if r["leaked"] else "era value not in snapshot"
                fails.append(f"  [{s['id']}] as_of {r['as_of']} {r['field']}: {why}")
        for r in s["probes"]:
            if not r["ok"]:
                why = ("answer missing" if not r["found"] else
                       "stale in top-k" if not r["stale_excluded"] else "polluted top-k")
                fails.append(f"  [{s['id']}] probe '{r['query'][:48]}': {why}")
    if fails:
        print(f"\nfailures ({len(fails)}):")
        print("\n".join(fails))


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--mode", choices=["keyed", "raw"], default="keyed")
    ap.add_argument("--dataset", default=str(_DATA))
    ap.add_argument("--model", default=DEFAULT_MODEL)
    ap.add_argument("--k", type=int, default=5)
    ap.add_argument("--out", default=None, help="write full JSON report here")
    args = ap.parse_args()

    # Environment must be pinned before lians imports read settings.
    os.environ["EMBEDDING_PROVIDER"] = "sentence-transformers"
    os.environ["SENTENCE_TRANSFORMER_MODEL"] = args.model
    if args.mode == "raw":
        # No structured keys anywhere: force the pure-semantic supersession path.
        os.environ["DOMAIN_ADAPTER"] = "passthrough"

    sys.path.insert(0, str(_REPO / "sdk" / "python"))
    sys.path.insert(0, str(_REPO))
    from lians import LocalLiansClient  # noqa: E402

    dataset = json.loads(Path(args.dataset).read_text(encoding="utf-8"))
    with LocalLiansClient() as client:  # in-memory DB, fresh every run
        report = run_eval(client, dataset, mode=args.mode, k=args.k)

    print_report(report)

    out = Path(args.out) if args.out else _RESULTS / f"lifecycle_{args.mode}.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(f"\nfull report -> {out}")


if __name__ == "__main__":
    main()
