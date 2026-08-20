"""
Agent-to-agent memory simulator.

Static datasets don't capture how real users interrupt themselves, switch
topics, and circle back days later. This harness drives an LLM "User" agent
with a hidden persona (see ``data/personas.json``) against a memory-augmented
"Assistant" across multiple simulated sessions. The persona's session plans
force the messy behaviors we want to test - mid-task interjections, revisions
of earlier facts, topic switches - while the assistant stores every user turn
in Lians and answers from recall.

Scoring stays deterministic: after the conversation, the persona's ground-truth
probes are run against ``recall`` exactly like lifecycle_eval (answer in top-k,
superseded value excluded, optional as_of). The LLM generates the mess; it
never judges the result.

Requires ``ANTHROPIC_API_KEY``. Run::

    python -m benchmarks.agent_sim
    python -m benchmarks.agent_sim --model claude-haiku-4-5   # cheaper sim
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
_DATA = Path(__file__).resolve().parent / "data" / "personas.json"
_RESULTS = _REPO / "results" / "lifecycle"

DEFAULT_EMBED = "Snowflake/snowflake-arctic-embed-l-v2.0"

USER_SYSTEM = """You are roleplaying a specific person talking to their AI assistant.

Persona: {profile}

Today's session goal: {goal}

Over the course of this session you must naturally work in ALL of these details
(spread them out; deliver interjections abruptly, mid-task, the way real people do):
{must_mention}

Rules:
- Speak only as the user. One conversational message per turn, 1-3 sentences.
- Stay in character; be casual and a little scattered.
- Do not summarize or repeat details you already mentioned this session.
- Never mention that you are roleplaying or following instructions."""

ASSISTANT_SYSTEM = """You are a helpful personal assistant with long-term memory.
Relevant memories about this user (retrieved automatically):
{memories}

Reply in 1-3 sentences. Use the memories when relevant; stay on the user's task."""


def _when(s: str) -> datetime:
    return datetime.fromisoformat(s + "T12:00:00").replace(tzinfo=timezone.utc)


def _text(msg) -> str:
    return next((b.text for b in msg.content if b.type == "text"), "").strip()


def run_session(llm, model: str, mem, agent_id: str, session: dict) -> list[dict]:
    """Drive one simulated session; store each user turn in memory. Returns the transcript."""
    when = _when(session["date"])
    user_history: list[dict] = []       # from the user-agent's point of view
    transcript: list[dict] = []
    must = "\n".join(f"- {m}" for m in session["must_mention"])

    assistant_reply = "Hi! What are we working on today?"
    for _ in range(session["turns"]):
        user_history.append({"role": "user", "content": assistant_reply})
        sim = llm.messages.create(
            model=model,
            max_tokens=2048,
            system=USER_SYSTEM.format(profile=session["_profile"],
                                      goal=session["goal"], must_mention=must),
            messages=user_history,
        )
        utterance = _text(sim)
        if not utterance:
            break
        user_history.append({"role": "assistant", "content": utterance})
        transcript.append({"role": "user", "text": utterance})

        # The memory layer under test: store the user's turn, recall for the reply.
        mem.add(agent_id=agent_id, content=f"User: {utterance}",
                event_time=when, source="conversation")
        recalled = mem.recall(agent_id=agent_id, query=utterance, k=5)
        mem_text = "\n".join(
            f"- {m.get('content')}" for m in recalled.get("memories", [])
        ) or "(none yet)"

        reply = llm.messages.create(
            model=model,
            max_tokens=2048,
            system=ASSISTANT_SYSTEM.format(memories=mem_text),
            messages=[{"role": "user", "content": utterance}],
        )
        assistant_reply = _text(reply) or "Got it."
        transcript.append({"role": "assistant", "text": assistant_reply})

    return transcript


def score(mem, agent_id: str, probes: list[dict], k: int = 5,
          transcript_text: str = "") -> list[dict]:
    rows = []
    for q in probes:
        # Sim-fidelity guard: the User-LLM sometimes never works a must_mention
        # detail into the conversation. A probe whose answer was never uttered
        # is unanswerable by ANY memory system - report it separately instead
        # of scoring it against the engine.
        if transcript_text and q["answer"].lower() not in transcript_text:
            rows.append({"query": q["query"], "as_of": q.get("as_of"),
                         "not_uttered": True, "ok": None})
            continue
        kwargs: dict[str, Any] = {"k": q.get("k", k)}
        if q.get("as_of"):
            kwargs["as_of"] = _when(q["as_of"])
        res = mem.recall(agent_id=agent_id, query=q["query"], **kwargs)
        texts = [(m.get("content") or "").lower() for m in res.get("memories", [])]
        found = any(q["answer"].lower() in t for t in texts)
        stale_ok = not (q.get("stale") and any(q["stale"].lower() in t for t in texts))
        rows.append({"query": q["query"], "as_of": q.get("as_of"),
                     "found": found, "stale_excluded": stale_ok,
                     "ok": found and stale_ok,
                     "top_k": [t[:140] for t in texts]})
    return rows


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", default="claude-opus-4-8", help="model for both sim agents")
    ap.add_argument("--personas", default=str(_DATA))
    ap.add_argument("--embed-model", default=DEFAULT_EMBED)
    ap.add_argument("--k", type=int, default=5)
    args = ap.parse_args()

    os.environ["EMBEDDING_PROVIDER"] = "sentence-transformers"
    os.environ["SENTENCE_TRANSFORMER_MODEL"] = args.embed_model
    os.environ["DOMAIN_ADAPTER"] = "passthrough"  # raw conversational text

    try:
        import anthropic
        llm = anthropic.Anthropic()
        llm.models.retrieve(args.model)  # fail fast if creds are missing
    except Exception as e:
        sys.exit(f"agent_sim needs Anthropic API credentials (ANTHROPIC_API_KEY "
                 f"or `ant auth login`): {e}")

    sys.path.insert(0, str(_REPO / "sdk" / "python"))
    sys.path.insert(0, str(_REPO))
    from lians import LocalLiansClient  # noqa: E402

    dataset = json.loads(Path(args.personas).read_text(encoding="utf-8"))
    report: dict[str, Any] = {"model": args.model, "personas": []}

    with LocalLiansClient() as mem:
        for persona in dataset["personas"]:
            agent_id = f"sim-{persona['id']}"
            transcripts = []
            for sess in persona["sessions"]:
                sess["_profile"] = persona["profile"]
                print(f"[{persona['id']}] session {sess['date']}: {sess['goal']}")
                transcripts.append({
                    "date": sess["date"],
                    "transcript": run_session(llm, args.model, mem, agent_id, sess),
                })

            all_text = "\n".join(
                t["text"].lower() for s in transcripts for t in s["transcript"]
                if t["role"] == "user"
            )
            rows = score(mem, agent_id, persona["probes"], k=args.k,
                         transcript_text=all_text)
            scored = [r for r in rows if r["ok"] is not None]
            ok = sum(r["ok"] for r in scored)
            skipped = len(rows) - len(scored)
            note = f" ({skipped} not uttered by sim, excluded)" if skipped else ""
            print(f"[{persona['id']}] probes: {ok}/{len(scored)}{note}")
            for r in rows:
                if r["ok"] is None:
                    mark = "SKIP"
                else:
                    mark = "OK  " if r["ok"] else ("MISS" if not r["found"] else "STALE")
                asof = f" (as_of {r['as_of']})" if r["as_of"] else ""
                print(f"  {mark} {r['query']}{asof}")
            report["personas"].append({
                "id": persona["id"], "correct": ok, "total": len(scored),
                "not_uttered": skipped, "probes": rows, "sessions": transcripts,
            })

    _RESULTS.mkdir(parents=True, exist_ok=True)
    out = _RESULTS / "agent_sim.json"
    out.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(f"\nfull report -> {out}")


if __name__ == "__main__":
    main()
