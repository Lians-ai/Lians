"""distill=True ingest: derived facts stored beside raw provenance.

The extractor is stubbed — no LLM call, no API key, deterministic."""
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "sdk" / "python"))

from lians import LocalLiansClient  # noqa: E402

MESSAGES = [
    {"role": "user", "content": "I moved my emergency fund from Chase to Fidelity."},
    {"role": "assistant", "content": "Noted — Fidelity is your emergency fund home now."},
]

STUB_FACTS = [
    "User moved their emergency fund from Chase to Fidelity (10 May, 2026).",
    "User's emergency fund is now held at Fidelity (10 May, 2026).",
]


def test_distill_stores_facts_and_raw_provenance(monkeypatch):
    import src.lians.enrichment as enrichment

    async def fake_distill(transcript, session_date, **kwargs):
        assert "Chase to Fidelity" in transcript          # full transcript passed
        assert "assistant:" in transcript                 # both roles included
        return list(STUB_FACTS)

    monkeypatch.setattr(enrichment, "distill_batch", fake_distill)

    with LocalLiansClient(embedding_provider="local") as mem:
        out = mem.add_from_messages(
            agent_id="d", messages=MESSAGES, distill=True,
            event_time=datetime(2026, 5, 10, tzinfo=timezone.utc),
        )
        # 2 raw messages (user+assistant by default under distill) + 2 facts
        assert out["added"] == 4
        contents = {m["content"] for m in out["memories"]}
        assert MESSAGES[0]["content"] in contents         # raw provenance kept
        assert STUB_FACTS[0] in contents                  # fact stored
        derived = [m for m in out["memories"] if m["metadata"].get("derived")]
        assert len(derived) == 2
        assert all(m["metadata"].get("distilled") for m in derived)
        assert all("distilled" in (m["source"] or "") for m in derived)


def test_distill_off_keeps_legacy_behavior():
    with LocalLiansClient(embedding_provider="local") as mem:
        out = mem.add_from_messages(agent_id="d", messages=MESSAGES)
        # default (no distill): assistant messages only, no derived facts
        assert out["added"] == 1
        assert out["memories"][0]["metadata"]["role"] == "assistant"
