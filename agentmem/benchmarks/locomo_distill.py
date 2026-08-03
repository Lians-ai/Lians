"""
Distill LOCOMO sessions into derived fact memories and ingest them.

Phase 1 (--distill):  one LLM call per session (~350 total) extracts atomic
dated facts; saved to results/locomo_facts/conv_N.json so the LLM cost is
paid once.

Phase 2 (--ingest):   copies each arctic checkpoint DB to
results/locomo_dbs_arctic_enriched/ and add_batch-es the facts as derived
memories (metadata.derived=true, event_time = session time + turn count + 60s
so facts sort after their session's raw turns and never interleave the
smoothing adjacency of raw dialogue).

Usage (from agentmem root; needs OPENAI_API_KEY for --distill):
    python -m benchmarks.locomo_distill --distill
    python -m benchmarks.locomo_distill --ingest
"""
from __future__ import annotations

import argparse
import asyncio
import json
import shutil
import sys
from datetime import timedelta
from pathlib import Path

_REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO))
sys.path.insert(0, str(_REPO / "sdk" / "python"))

from benchmarks.locomo_eval import iter_sessions, _turn_content  # noqa: E402

_DATA = _REPO / "benchmarks" / "data" / "locomo10.json"
_FACTS = _REPO / "results" / "locomo_facts"
_SRC_DBS = _REPO / "results" / "locomo_dbs_arctic"
_DST_DBS = _REPO / "results" / "locomo_dbs_arctic_enriched"

CONCURRENCY = 8


async def distill_all(dataset: list[dict]) -> None:
    # This benchmark intentionally exercises the source checkout through the
    # public SDK's local-engine compatibility bridge. The SDK and deployable
    # server distributions are isolated and both own the top-level name.
    from src.lians.enrichment import distill_batch

    _FACTS.mkdir(parents=True, exist_ok=True)
    sem = asyncio.Semaphore(CONCURRENCY)

    for n, entry in enumerate(dataset):
        out = _FACTS / f"conv_{n}.json"
        if out.exists():
            print(f"conv_{n}: facts already distilled, skipping", flush=True)
            continue
        sessions = list(iter_sessions(entry["conversation"]))

        async def one(idx, when, turns):
            transcript = "\n".join(_turn_content(t) for t in turns)
            date_str = when.strftime("%d %B, %Y")
            async with sem:
                for attempt in range(3):
                    try:
                        facts = await distill_batch(transcript, date_str)
                        return {"session": idx, "date": when.isoformat(),
                                "n_turns": len(turns), "facts": facts}
                    except Exception as exc:
                        if attempt == 2:
                            print(f"  conv_{n} session {idx} FAILED: {exc}", flush=True)
                            return {"session": idx, "date": when.isoformat(),
                                    "n_turns": len(turns), "facts": [],
                                    "error": str(exc)}
                        await asyncio.sleep(5 * (attempt + 1))

        results = await asyncio.gather(*[one(i, w, t) for i, w, t in sessions])
        out.write_text(json.dumps(results, indent=1), encoding="utf-8")
        nf = sum(len(r["facts"]) for r in results)
        print(f"conv_{n}: {len(sessions)} sessions -> {nf} facts", flush=True)


def ingest_all(dataset: list[dict]) -> None:
    from lians import LocalLiansClient

    _DST_DBS.mkdir(parents=True, exist_ok=True)
    for n in range(len(dataset)):
        src = _SRC_DBS / f"conv_{n}.sqlite"
        dst = _DST_DBS / f"conv_{n}.sqlite"
        facts_file = _FACTS / f"conv_{n}.json"
        if not facts_file.exists():
            print(f"conv_{n}: no facts file, skipping")
            continue
        expected = sum(len(s["facts"]) for s in
                       json.loads(facts_file.read_text(encoding="utf-8")))
        if dst.exists():
            import sqlite3
            have = sqlite3.connect(dst).execute(
                "select count(*) from memories where json_extract(metadata,'$.derived')"
            ).fetchone()[0]
            if have >= expected:
                print(f"conv_{n}: already enriched ({have} facts), skipping", flush=True)
                continue
        shutil.copyfile(src, dst)
        sessions = json.loads(facts_file.read_text(encoding="utf-8"))
        agent = f"locomo-{dataset[n]['sample_id']}"
        with LocalLiansClient(embedding_provider="sentence-transformers",
                              db_path=str(dst)) as client:
            total = 0
            for s in sessions:
                if not s["facts"]:
                    continue
                from datetime import datetime
                when = datetime.fromisoformat(s["date"])
                base = when + timedelta(seconds=s["n_turns"] + 60)
                client.add_batch(agent, [
                    {
                        "content": fact,
                        "event_time": base + timedelta(seconds=j),
                        "metadata": {"derived": True, "dia_id": ""},
                    }
                    for j, fact in enumerate(s["facts"])
                ])
                total += len(s["facts"])
        print(f"conv_{n}: ingested {total} derived facts -> {dst.name}", flush=True)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--distill", action="store_true")
    ap.add_argument("--ingest", action="store_true")
    args = ap.parse_args()
    dataset = json.loads(_DATA.read_text(encoding="utf-8"))
    if args.distill:
        asyncio.run(distill_all(dataset))
    if args.ingest:
        ingest_all(dataset)


if __name__ == "__main__":
    main()
