"""
Demo: research agent tracking earnings revisions with AgentMem.

Shows two modes:
  - Local mode  (LocalLianClient): zero setup, in-memory SQLite, no server
  - HTTP mode   (LianClient):      sync client -> real Lian server

Run local mode immediately::

    cd agentmem
    python examples/research_agent_demo.py

Run against a live server::

    uvicorn src.lian.main:app --reload
    python examples/research_agent_demo.py --mode http --api-key your-key
"""
import sys
import argparse
from pathlib import Path
from datetime import datetime, timezone

# Allow running from any directory without installing the SDK
_root = Path(__file__).resolve().parent.parent  # agentmem/
sys.path.insert(0, str(_root / "sdk" / "python"))
sys.path.insert(0, str(_root))  # makes src.lian importable in dev mode


def run_demo(mem) -> None:
    agent = "research-agent-1"

    print("--- Adding earnings guidance sequence ---")
    mem.add(
        agent_id=agent,
        content="NVDA Q3 FY2026 guidance: $32B",
        event_time=datetime(2026, 2, 1, tzinfo=timezone.utc),
        source="earnings_call",
        metadata={"ticker": "NVDA", "metric": "guidance", "quarter": "Q3FY26"},
    )
    print("  Added: Q3 guidance $32B (Feb 1)")

    mem.add(
        agent_id=agent,
        content="NVDA raises Q3 FY2026 guidance to $36B (analyst day)",
        event_time=datetime(2026, 5, 10, tzinfo=timezone.utc),
        source="analyst_day",
        metadata={"ticker": "NVDA", "metric": "guidance", "quarter": "Q3FY26"},
    )
    print("  Added: Q3 guidance raised to $36B (May 10) -- should supersede Feb entry")

    print("\n--- Present-time recall (expect: $36B first) ---")
    result = mem.recall(agent_id=agent, query="NVDA Q3 guidance", k=5)
    for m in result["memories"]:
        ts = (m.get("event_time") or "")[:10]
        print(f"  [{ts}] {m['content']}  valid_to={m.get('valid_to')}")

    print("\n--- Point-in-time recall as of 2026-03-01 (expect: $32B only) ---")
    past = mem.recall(
        agent_id=agent,
        query="NVDA Q3 guidance",
        k=5,
        as_of=datetime(2026, 3, 1, tzinfo=timezone.utc),
    )
    for m in past["memories"]:
        ts = (m.get("event_time") or "")[:10]
        print(f"  [{ts}] {m['content']}")

    print("\n--- Audit reconstruction as of 2099-01-01 ---")
    audit = mem.reconstruct(
        agent_id=agent,
        as_of=datetime(2099, 1, 1, tzinfo=timezone.utc),
        query="NVDA guidance",
    )
    print(f"  Memories visible: {len(audit['memories'])}")
    print(f"  Event log entries: {len(audit['event_trail'])}")
    for e in audit["event_trail"][:6]:
        print(f"    {e['op']:10s} {str(e.get('memory_id', ''))[:8]}")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--mode", choices=["local", "http"], default="local")
    parser.add_argument("--base-url", default="http://localhost:8000")
    parser.add_argument("--api-key", default="")
    args = parser.parse_args()

    if args.mode == "local":
        from lian import LocalLianClient
        print("=== Local mode (no server needed) ===\n")
        with LocalLianClient() as mem:
            run_demo(mem)
    else:
        from lian import LianClient
        print(f"=== HTTP mode -> {args.base_url} ===\n")
        with LianClient(base_url=args.base_url, api_key=args.api_key) as mem:
            run_demo(mem)


if __name__ == "__main__":
    main()
