"""
Scale eval - retention, closure, and latency under long-term load.

Simulates months of heavy use: thousands of interleaved "noise" turns plus a
set of core keyed preferences that are each revised several times. After every
checkpoint it measures the three things a memory backbone must hold at scale:

  1. **Latent recall** - is each core preference's *current* value still in the
     top-k, buried under thousands of unrelated turns, with every superseded
     revision excluded?
  2. **Closure (pruning)** - the lifecycle analogue of decay: every revised
     fact's old versions must have their validity window closed, so the live
     set stays compact. Reported as live/total ratio and revised-fact closure.
  3. **Latency** - median and p95 recall time as the corpus grows.

Deterministic (seeded), judge-free. Run::

    python -m benchmarks.scale_eval                     # ~2k turns, quick
    python -m benchmarks.scale_eval --noise 20000       # long-haul
"""
from __future__ import annotations

import argparse
import json
import os
import random
import statistics
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

_REPO = Path(__file__).resolve().parent.parent
_RESULTS = _REPO / "results" / "lifecycle"

DEFAULT_MODEL = "Snowflake/snowflake-arctic-embed-l-v2.0"
_START = datetime(2025, 1, 1, tzinfo=timezone.utc)

# Core keyed preferences: (field, [v1, v2, v3]) - v3 is current after 2 revisions.
CORE_FACTS = [
    ("diet", ["User diet: strictly vegan.", "User diet: pescatarian now.", "User diet: pescatarian, but allergic to salmon."]),
    ("home_city", ["User lives in Chicago.", "User relocated to Denver.", "User settled in Boulder."]),
    ("employer", ["User works at Acme Corp.", "User moved to Beta Systems.", "User is now at Gamma Labs."]),
    ("contact_channel", ["Reach the user by email.", "Reach the user on Slack.", "Reach the user on Signal only."]),
    ("timezone", ["User timezone: Eastern.", "User timezone: Central.", "User timezone: Pacific."]),
    ("coffee_order", ["User coffee order: oat latte.", "User coffee order: flat white.", "User coffee order: black drip."]),
    ("car", ["User drives a Honda Civic.", "User drives a Subaru Outback.", "User drives a Rivian R2."]),
    ("gym", ["User gym: Planet Fitness downtown.", "User gym: Ironworks on 5th.", "User gym: home garage setup."]),
    ("doctor", ["User primary doctor: Dr. Patel.", "User primary doctor: Dr. Kim.", "User primary doctor: Dr. Alvarez."]),
    ("newspaper", ["User reads the Tribune daily.", "User switched to the Post.", "User reads the FT now."]),
]

PROBES = [
    ("What is the user's diet?", "allergic to salmon", ["strictly vegan"]),
    ("Where does the user live?", "boulder", ["chicago", "relocated to denver"]),
    ("Where does the user work?", "gamma labs", ["acme corp", "beta systems"]),
    ("How should I contact the user?", "signal", ["by email", "on slack"]),
    ("What timezone is the user in?", "pacific", ["eastern", "central"]),
    ("What is the user's coffee order?", "black drip", ["oat latte", "flat white"]),
    ("What car does the user drive?", "rivian", ["honda civic", "subaru"]),
    ("Which gym does the user go to?", "home garage", ["planet fitness", "ironworks"]),
    ("Who is the user's doctor?", "alvarez", ["dr. patel", "dr. kim"]),
    ("What newspaper does the user read?", "ft", ["tribune", "the post"]),
]

_NOISE_TOPICS = [
    "Discussed the {adj} quarterly numbers for {ent} with the team.",
    "Read an article about {adj} developments in {ent} research.",
    "Scheduled a {adj} sync with {ent} for next week.",
    "Debugged a {adj} issue in the {ent} pipeline all afternoon.",
    "Watched a documentary about {ent}, thought it was {adj}.",
    "Drafted {adj} notes on the {ent} proposal.",
    "Compared {adj} vendors for the {ent} migration.",
    "User mentioned the weather in {ent} was {adj} today.",
    "Reviewed the {adj} onboarding docs for {ent}.",
    "Brainstormed {adj} names for the {ent} initiative.",
]
_ENTS = ["Orion", "Larkspur", "Halcyon", "Redwood", "Meridian", "Quartz", "Bluebird",
         "Cascade", "Foxglove", "Summit", "Peregrine", "Juniper", "Basalt", "Harbor"]
_ADJS = ["surprising", "tedious", "promising", "confusing", "detailed", "rough",
         "excellent", "preliminary", "overdue", "ambitious"]


def build_timeline(noise: int, rng: random.Random) -> list[dict]:
    """Interleave noise turns and core-fact revisions across a simulated year."""
    events: list[dict] = []
    span_min = 365 * 24 * 60
    for i in range(noise):
        t = _START + timedelta(minutes=rng.randrange(span_min))
        events.append({
            "content": rng.choice(_NOISE_TOPICS).format(ent=rng.choice(_ENTS), adj=rng.choice(_ADJS)),
            "event_time": t, "metadata": None,
        })
    for field, versions in CORE_FACTS:
        # revisions spaced through the year so noise lands between them
        anchors = sorted(rng.sample(range(span_min), len(versions)))
        for v, offset in zip(versions, anchors):
            events.append({
                "content": v,
                "event_time": _START + timedelta(minutes=offset),
                "metadata": {"entity": "user", "field": field},
            })
    # Ingest in event order (realistic accumulation)
    events.sort(key=lambda e: e["event_time"])
    return events


def measure(client, agent: str, k: int) -> dict[str, Any]:
    lat: list[float] = []
    correct = 0
    stale_hits = 0
    detail = []
    for query, answer, stales in PROBES:
        t0 = time.perf_counter()
        res = client.recall(agent_id=agent, query=query, k=k)
        lat.append((time.perf_counter() - t0) * 1000)
        texts = [(m.get("content") or "").lower() for m in res.get("memories", [])]
        found = any(answer in t for t in texts)
        leaked = [s for s in stales if any(s in t for t in texts)]
        correct += int(found and not leaked)
        stale_hits += len(leaked)
        detail.append({"query": query, "found": found, "leaked": leaked})
    return {
        "core_recall_at_k": correct / len(PROBES),
        "stale_in_topk": stale_hits,
        "latency_ms_p50": round(statistics.median(lat), 1),
        "latency_ms_p95": round(sorted(lat)[max(0, int(len(lat) * 0.95) - 1)], 1),
        "detail": detail,
    }


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--noise", type=int, default=2000)
    ap.add_argument("--checkpoints", default="", help="comma-separated turn counts; default = quartiles")
    ap.add_argument("--model", default=DEFAULT_MODEL)
    ap.add_argument("--k", type=int, default=5)
    ap.add_argument("--batch", type=int, default=200)
    ap.add_argument("--seed", type=int, default=7)
    args = ap.parse_args()

    os.environ["EMBEDDING_PROVIDER"] = "sentence-transformers"
    os.environ["SENTENCE_TRANSFORMER_MODEL"] = args.model
    # No Redis in local mode - each cache attempt costs ~2s in connect timeouts.
    os.environ.setdefault("RECALL_CACHE_ENABLED", "false")

    sys.path.insert(0, str(_REPO / "sdk" / "python"))
    sys.path.insert(0, str(_REPO))
    from lians import LocalLiansClient  # noqa: E402

    rng = random.Random(args.seed)
    events = build_timeline(args.noise, rng)
    total = len(events)
    if args.checkpoints:
        marks = sorted(int(x) for x in args.checkpoints.split(","))
    else:
        marks = sorted({total // 4, total // 2, (3 * total) // 4, total})

    agent = "scale-user"
    now = datetime.now(timezone.utc)
    checkpoints: list[dict] = []
    ingest_ms: list[float] = []

    with LocalLiansClient() as client:
        done = 0
        for start in range(0, total, args.batch):
            chunk = events[start:start + args.batch]
            t0 = time.perf_counter()
            client.add_batch(agent, [
                {"content": e["content"],
                 "event_time": e["event_time"].isoformat(),
                 "source": "conversation",
                 "metadata": e["metadata"] or {}}
                for e in chunk
            ])
            ingest_ms.append((time.perf_counter() - t0) * 1000 / len(chunk))
            done += len(chunk)
            while marks and done >= marks[0]:
                mark = marks.pop(0)
                m = measure(client, agent, args.k)
                live = client.snapshot(agent_id=agent, as_of=now, limit=10 * total)["total"]
                m.update({"turns": done, "live": live, "total": done,
                          "live_ratio": round(live / done, 4)})
                checkpoints.append(m)
                print(f"@{done:>7} turns  core_recall@{args.k}={m['core_recall_at_k']:.0%}  "
                      f"stale_in_topk={m['stale_in_topk']}  live/total={m['live_ratio']:.3f}  "
                      f"recall p50={m['latency_ms_p50']}ms p95={m['latency_ms_p95']}ms")

        # Closure: every core fact should have exactly 1 live version (of 3)
        expected_closed = sum(len(v) - 1 for _, v in CORE_FACTS)
        final_live = checkpoints[-1]["live"] if checkpoints else None
        closed = total - final_live if final_live is not None else None

    report = {
        "noise": args.noise, "total_turns": total, "k": args.k,
        "model": args.model, "seed": args.seed,
        "ingest_ms_per_memory_p50": round(statistics.median(ingest_ms), 1),
        "expected_closures": expected_closed,
        "observed_closures": closed,
        "checkpoints": checkpoints,
    }
    print(f"\nclosure: {closed} closed / {expected_closed} expected "
          f"(superseded core revisions)   ingest p50 = {report['ingest_ms_per_memory_p50']}ms/memory")

    _RESULTS.mkdir(parents=True, exist_ok=True)
    out = _RESULTS / f"scale_{args.noise}.json"
    out.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(f"full report -> {out}")


if __name__ == "__main__":
    main()
