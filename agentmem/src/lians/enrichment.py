"""
Opt-in LLM fact distillation at ingest.

Raw event streams (dialogue turns, tickets, logs) split durable knowledge
across many low-signal entries: "what activities has X done?" has six answers
scattered over six sessions. Distillation extracts atomic, dated, attributed
fact statements per batch of entries and stores them as *derived* memories
alongside the raw ones — recall then surfaces dense facts and raw evidence
together.

This is deliberately opt-in: it puts an LLM in the ingest path, which costs
money, adds latency, and is non-deterministic. The default ingest path stays
LLM-free. Derived memories are tagged ``metadata.derived = true`` and point
back to their source batch, so they are auditable and can be filtered or
regenerated at any time.
"""
from __future__ import annotations

import os
from typing import List, Optional

DISTILL_MODEL = os.getenv("LIANS_DISTILL_MODEL", "gpt-5-mini")

DISTILL_SYSTEM = (
    "You extract long-term memory facts from conversation transcripts. "
    "Output one fact per line. No numbering, no bullets, no commentary."
)

DISTILL_PROMPT = """Extract every distinct, durable fact from this conversation session (dated {session_date}).

Rules:
- One fact per line, each fully self-contained (usable without the transcript).
- Always use people's names, never pronouns ("Melanie went camping", not "she went camping").
- Attribute every action, statement, preference, and plan to the correct speaker.
- Include the date context: append "({session_date})" to facts tied to this session's events; keep any other dates mentioned in the text.
- Capture: events and activities, preferences and opinions, relationships, possessions, plans and intentions, life changes, numbers and counts, names of specific things (books, movies, places, pets, businesses).
- If someone shared a photo, record what it showed as a fact about them.
- Keep each fact short (one sentence). Split compound statements into separate facts.

Transcript:
{transcript}
"""


def build_distill_prompt(transcript: str, session_date: str) -> str:
    return DISTILL_PROMPT.format(transcript=transcript, session_date=session_date)


def parse_facts(raw: str) -> List[str]:
    """One fact per non-empty line; strips accidental list markers."""
    facts = []
    for line in raw.splitlines():
        line = line.strip().lstrip("-*•").strip()
        line = line.lstrip("0123456789.)").strip() if line[:1].isdigit() else line
        if len(line) > 10:
            facts.append(line)
    return facts


async def distill_batch(
    transcript: str,
    session_date: str,
    *,
    model: Optional[str] = None,
    api_key: Optional[str] = None,
) -> List[str]:
    """Extract fact strings from one transcript batch via the OpenAI API."""
    import openai

    client = openai.AsyncOpenAI(api_key=api_key or os.getenv("OPENAI_API_KEY"))
    resp = await client.chat.completions.create(
        model=model or DISTILL_MODEL,
        messages=[
            {"role": "system", "content": DISTILL_SYSTEM},
            {"role": "user", "content": build_distill_prompt(transcript, session_date)},
        ],
    )
    return parse_facts(resp.choices[0].message.content or "")
