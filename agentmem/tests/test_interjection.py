"""
Interjection extraction - sub-turn durable facts (the agent_sim finding).

Two layers under test:
  1. The deterministic clause extractor (pure) - pulls buried durable facts
     ("remind me I eat fish now" mid-task) out of long conversational turns,
     and stays silent on single-clause turns, task chatter, and keyed facts.
  2. The ranking helpers - parent/clause collapse (one fact can't fill two
     result slots) and the time-aware stale-clause demotion.
"""

from __future__ import annotations

import re
from datetime import UTC, datetime

import pytest
from src.lians.interjection import _split_clauses, _split_segments, extract_interjections
from src.lians.ranking import STALE_CLAUSE_PENALTY, _collapse_derived, _stale_clause_penalty

# Test-only references over bounded literals.  Production parsing must use the
# linear scanners; these preserve an executable record of their former regex
# semantics without applying the expressions to untrusted input.
_EM_DASH = chr(0x2014)
_EN_DASH = chr(0x2013)
_REFERENCE_SEGMENT_SPLIT = re.compile(
    r"(?<=[.!?…])\s+|\s+["
    + _EM_DASH
    + _EN_DASH
    + r"]\s*|\s*["
    + _EM_DASH
    + _EN_DASH
    + r"]\s+|\s+--\s+"
)
_REFERENCE_CLAUSE_SPLIT = re.compile(
    r",\s+(?:so|since|because|but|although|though|anyway)\b\s*|\s+because\s+"
    r"|,?\s+and\s+(?=I\b|I'm\b|my\b)",
    re.IGNORECASE,
)


# ── Layer 1: pure deterministic extractor ────────────────────────────────────


def test_buried_location_fact_extracted():
    turn = (
        "User: Ooh that's good. Okay so now activities " + _EM_DASH + " I'm thinking we open "
        "with something to get people talking, since I've got their whole team "
        "flying into my studio in Portland and they won't all know each other."
    )
    out = extract_interjections(turn)
    assert len(out) == 1
    assert "my studio in Portland" in out[0]
    assert out[0].startswith("User: ")


def test_aside_marker_trims_to_the_request():
    turn = (
        "User: Um, discovery is probably like 4 days, UX design maybe 8? Oh and "
        "I should tell you " + _EM_DASH + " the client wants to do a lunch meeting, remind me "
        "I eat fish now, I'm not vegetarian anymore."
    )
    out = extract_interjections(turn)
    assert any(c.endswith("remind me I eat fish now, I'm not vegetarian anymore.") for c in out)


def test_trailing_aside_falls_back_to_whole_fact_clause():
    turn = (
        "User: Oh wait, actually can we do pricing at the same time because my "
        "brain wants to know the numbers " + _EM_DASH + " my day rate is $900 by the way."
    )
    out = extract_interjections(turn)
    assert out == ["User: my day rate is $900 by the way."]


def test_revision_clause_carries_its_cue():
    turn = (
        "User: Oh, by the way, my day rate went up to $1100 now that Loomis "
        "renewed, so I should factor that into how long I'm spending prepping this."
    )
    out = extract_interjections(turn)
    assert len(out) == 1
    assert "$1100" in out[0] and "went up" in out[0]


def test_short_fact_clause_falls_back_to_segment():
    # "my day rate is $900" alone is under the length floor; the segment rescue
    # must keep the fact (second agent_sim run's phrasing).
    turn = (
        "User: Yes, phases is perfect. Oh "
        + _EM_DASH
        + " my day rate is $900, so we can build off that for the estimates."
    )
    out = extract_interjections(turn)
    assert len(out) == 1
    assert "$900" in out[0]


def test_negotiated_revision_extracted():
    turn = (
        "User: Let me find it real quick... okay it's basically like intro, then a "
        "design system overview, then a break, then prototyping demo, then wrap-up. "
        "Oh and by the way I finally negotiated my day rate up to $1100 after the "
        "Loomis renewal, so that felt good."
    )
    out = extract_interjections(turn)
    assert any("$1100" in c for c in out)


def test_reminder_to_myself_and_adverbed_verb():
    # Third agent_sim run's phrasing: aside marker "reminder to myself" and the
    # adverb between subject and verb ("I actually eat").
    turn = (
        "User: Yeah let's do it. Oh wait" + _EM_DASH + "reminder to myself, I need to tell the "
        "caterer I actually eat fish now, so I'm pescatarian, not full veggie. "
        "Anyway, deliverables!"
    )
    out = extract_interjections(turn)
    assert any("eat fish now" in c or "pescatarian" in c for c in out)


def test_conjunction_buried_fact_splits_off():
    turn = (
        "User: Ugh, hold on " + _EM_DASH + " remind me to reschedule the client lunch, they "
        "picked a steakhouse and I'm pescatarian now, so I need to find "
        "somewhere with fish."
    )
    out = extract_interjections(turn)
    assert any(c == "User: I'm pescatarian now" for c in out), out


def test_single_clause_turn_not_duplicated():
    # The whole turn already IS the fact - extraction would just copy it.
    assert extract_interjections("User: I moved to Boulder.") == []
    assert extract_interjections("NVDA guidance raised to $40B") == []


def test_task_chatter_ignored():
    turn = (
        "User: Perfect, let's build in 2 rounds per phase and then it's like... "
        "$900 a day after that for extra rounds, right? Oh god I also need to "
        "reschedule my dentist thing but that's a later-me problem " + _EM_DASH + " anyway "
        "yeah, put the revision clause in."
    )
    assert extract_interjections(turn) == []


def test_maximum_size_unmatched_whitespace_run_is_ignored():
    """A maximum-size write with no separator remains a single clause."""
    prefix = "User: keep planning"
    suffix = "then finish the task"
    turn = prefix + (" " * (100_000 - len(prefix) - len(suffix))) + suffix

    assert len(turn) == 100_000
    assert extract_interjections(turn) == []


def test_maximum_size_whitespace_run_preserves_connector_semantics():
    prefix = "User: keep planning"
    suffix = "because my studio in Portland is ready."
    turn = prefix + (" " * (100_000 - len(prefix) - len(suffix))) + suffix

    assert len(turn) == 100_000
    assert extract_interjections(turn) == ["User: my studio in Portland is ready."]


@pytest.mark.parametrize(
    "value",
    [
        "first.   second",
        "first?\tsecond",
        "first…\nsecond",
        "first   " + _EM_DASH + "second",
        "first" + _EM_DASH + "   second",
        "first   –   second",
        "first   --   second",
        "first" + _EM_DASH + "second",
        "first-- second",
    ],
)
def test_segment_scanner_matches_bounded_reference_semantics(value):
    assert _split_segments(value) == _REFERENCE_SEGMENT_SPLIT.split(value)


@pytest.mark.parametrize(
    "value",
    [
        "first, so second",
        "first, since second",
        "first, because second",
        "first, but second",
        "first, although second",
        "first, though second",
        "first, anyway second",
        "first because second",
        "first and I moved",
        "first, and I'm ready",
        "first and my studio moved",
        "first and they moved",
        "first becauseful second",
        "first, somehow second",
    ],
)
def test_clause_scanner_matches_bounded_reference_semantics(value):
    assert _split_clauses(value) == _REFERENCE_CLAUSE_SPLIT.split(value)


# ── Layer 2: ranking helpers ─────────────────────────────────────────────────


class _Row:
    def __init__(self, id, meta=None):
        self.id = id
        self.metadata_ = meta or {}


def test_collapse_drops_clause_only_when_parent_already_kept():
    parent = _Row("p1")
    clause = _Row("c1", {"_derived": "interjection", "_parent": "p1"})
    other = _Row("x1")
    # Parent first: the clause is a substring of it - redundant, dropped.
    scored = [(parent, 0.9, "p"), (clause, 0.8, "c"), (other, 0.7, "o")]
    assert [e[0].id for e in _collapse_derived(scored)] == ["p1", "x1"]
    # Clause first: the parent may hold facts the clause lost - NEVER evicted.
    scored = [(clause, 0.9, "c"), (parent, 0.8, "p"), (other, 0.7, "o")]
    assert [e[0].id for e in _collapse_derived(scored)] == ["c1", "p1", "x1"]


def test_collapse_noop_without_derived_rows():
    scored = [(_Row("a"), 0.9, None), (_Row("b"), 0.8, None)]
    assert _collapse_derived(scored) == scored


def test_stale_penalty_is_time_aware():
    closure = datetime(2026, 6, 9, tzinfo=UTC)
    meta = {"_stale_clauses": [closure.isoformat()]}
    before = datetime(2026, 5, 25, tzinfo=UTC)
    after = datetime(2026, 7, 1, tzinfo=UTC)
    assert _stale_clause_penalty(meta, before) == 0.0
    assert _stale_clause_penalty(meta, after) == STALE_CLAUSE_PENALTY
    assert _stale_clause_penalty({}, after) == 0.0
    # capped at two closures
    meta3 = {"_stale_clauses": [closure.isoformat()] * 3}
    assert _stale_clause_penalty(meta3, after) == 2 * STALE_CLAUSE_PENALTY
