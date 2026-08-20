"""
Deterministic interjection extraction - sub-turn durable facts.

Conversational turns bury durable personal facts as mid-clause asides:
"...their whole team flying into my studio in Portland and they won't all
know each other", or "remind me I eat fish now" dropped mid-pricing-math.
Stored whole, the turn's embedding dilutes the fact - recall misses it, and
unkeyed supersession can never match a revision to it because turn-vs-turn
cosine stays below the cue threshold (the agent_sim finding, 2026-07-10).

When ``interjection_extraction_enabled`` is on, ``add_memory`` extracts such
clauses and stores each as a *derived* memory alongside the raw turn:

  * rule-based (clause splitting + cue lexicon) - deterministic, reproducible,
    no model call, same posture as auto_metadata;
  * derived rows are provenance-tagged (``metadata._derived`` /
    ``metadata._parent``) and drop structured keys, so a clause can never trip
    keyed supersession against its own parent;
  * the raw turn stays the auditable record - derived rows are a recall and
    supersession surface, closable and time-travelable like any memory.
"""
from __future__ import annotations

import re

_MAX_CLAUSES = 3
_MIN_LEN = 15
_MAX_LEN = 240

# Leading "Speaker: " attribution on conversational content; re-applied to
# extracted clauses so they stay self-attributing.
_SPEAKER_RE = re.compile(r"^([A-Za-z][\w .'&-]{0,24}):\s+(.*)$", re.DOTALL)

# Segment boundaries: sentence enders and spoken-style em/en dashes.  These
# used to be expressed as overlapping ``\s+`` regex alternatives.  Searching
# an untrusted, long whitespace run forced the regex engine to retry the run at
# every character (quadratic time).  The scanners below consume every input
# character at most a constant number of times instead.
_SENTENCE_ENDERS = frozenset(".!?…")
_SPOKEN_DASHES = frozenset("\u2014\u2013")
_COMMA_CONNECTORS = ("so", "since", "because", "but", "although", "though", "anyway")


def _space_end(value: str, start: int) -> int:
    """Return the first index after one contiguous whitespace run."""
    end = start
    while end < len(value) and value[end].isspace():
        end += 1
    return end


def _is_word_char(char: str) -> bool:
    """Match the word-character behavior needed by the former ``\b`` checks."""
    return char == "_" or char.isalnum()


def _word_at(value: str, start: int, word: str) -> bool:
    """Case-insensitive ASCII word match with a trailing word boundary."""
    end = start + len(word)
    return (
        end <= len(value)
        and value[start:end].casefold() == word
        and (end == len(value) or not _is_word_char(value[end]))
    )


def _split_segments(body: str) -> list[str]:
    """Split sentence/dash segments in linear time.

    This preserves the former separator rules: sentence-ending whitespace,
    em/en dashes with whitespace on either side, and ``--`` surrounded by
    whitespace.
    """
    parts: list[str] = []
    start = 0
    index = 0
    while index < len(body):
        char = body[index]
        if char.isspace():
            separator_start = index
            whitespace_end = _space_end(body, index)

            if separator_start > 0 and body[separator_start - 1] in _SENTENCE_ENDERS:
                parts.append(body[start:separator_start])
                start = whitespace_end
                index = whitespace_end
                continue

            if whitespace_end < len(body) and body[whitespace_end] in _SPOKEN_DASHES:
                separator_end = _space_end(body, whitespace_end + 1)
                parts.append(body[start:separator_start])
                start = separator_end
                index = separator_end
                continue

            if (
                body.startswith("--", whitespace_end)
                and whitespace_end + 2 < len(body)
                and body[whitespace_end + 2].isspace()
            ):
                separator_end = _space_end(body, whitespace_end + 2)
                parts.append(body[start:separator_start])
                start = separator_end
                index = separator_end
                continue

            index = whitespace_end
            continue

        if char in _SPOKEN_DASHES and index + 1 < len(body) and body[index + 1].isspace():
            separator_end = _space_end(body, index + 1)
            parts.append(body[start:index])
            start = separator_end
            index = separator_end
            continue

        index += 1

    parts.append(body[start:])
    return parts


def _first_person_at(value: str, start: int) -> bool:
    # ``I\b`` intentionally also covers "I'm": apostrophes are non-word
    # characters, matching the previous lookahead.
    return _word_at(value, start, "i") or _word_at(value, start, "my")


def _comma_connector_end(value: str, comma: int) -> int | None:
    if comma + 1 >= len(value) or not value[comma + 1].isspace():
        return None

    word_start = _space_end(value, comma + 1)
    for connector in _COMMA_CONNECTORS:
        if _word_at(value, word_start, connector):
            return _space_end(value, word_start + len(connector))

    if _word_at(value, word_start, "and"):
        whitespace_start = word_start + len("and")
        if whitespace_start < len(value) and value[whitespace_start].isspace():
            clause_start = _space_end(value, whitespace_start)
            if _first_person_at(value, clause_start):
                return clause_start
    return None


def _whitespace_connector_end(value: str, whitespace_start: int) -> int | None:
    word_start = _space_end(value, whitespace_start)
    for connector in ("because", "and"):
        if not _word_at(value, word_start, connector):
            continue
        trailing_start = word_start + len(connector)
        if trailing_start >= len(value) or not value[trailing_start].isspace():
            continue
        clause_start = _space_end(value, trailing_start)
        if connector == "because" or _first_person_at(value, clause_start):
            return clause_start
    return None


def _split_clauses(segment: str) -> list[str]:
    """Split conservative clause connectors in a single linear scan."""
    parts: list[str] = []
    start = 0
    index = 0
    while index < len(segment):
        char = segment[index]
        if char == ",":
            separator_end = _comma_connector_end(segment, index)
            if separator_end is not None:
                parts.append(segment[start:index])
                start = separator_end
                index = separator_end
                continue
        elif char.isspace():
            separator_end = _whitespace_connector_end(segment, index)
            if separator_end is not None:
                parts.append(segment[start:index])
                start = separator_end
                index = separator_end
                continue
            index = _space_end(segment, index)
            continue
        index += 1

    parts.append(segment[start:])
    return parts

# Aside markers: the clause is an explicit "store this" interjection - trim to
# the marker so the stored fact starts at the request, not the task chatter.
_ASIDE_CUES = re.compile(
    r"\b(?:remind me|reminder (?:to|for) (?:myself|me)|note to self|"
    r"don'?t forget|for the record|remember that|"
    r"I should (?:tell|mention|say)|I need to tell|by the way)\b",
    re.IGNORECASE,
)

# Durable personal-fact patterns: first-person state that outlives the task.
_FACT_CUES = re.compile(
    r"\bmy\s+(?:\w+\s+){0,3}?(?:is|are|was|were|went|now|changed|moved|renewed|"
    r"increased|decreased)\b"
    r"|\bmy\s+\w+(?:\s+\w+)?\s+in\s+[A-Z][a-z]"
    r"|\bI(?:'m| am)\s+(?:allergic|vegetarian|vegan|pescatarian|gluten|lactose|"
    r"based|located)\b"
    r"|\bI\s+(?:now\s+|just\s+|\w+ly\s+)?(?:eat|live|work|drive|prefer|use|go by)\b"
    r"|\bI(?:'m| am)\s+(?:at|with)\s+[A-Z][\w']"
    # habitual adverb + verb is a durable fact by construction
    # ("I usually do a day rate, which is $900")
    r"|\bI\s+(?:usually|typically|normally|always)\s+\w+",
)


def _clauses(body: str) -> list[tuple[str, str]]:
    """(clause, enclosing_segment) pairs. A cue clause that connector-splitting
    left too short ("my day rate is $900" before ", so we can build off that")
    falls back to its segment, so the fact is never dropped on a length gate."""
    out: list[tuple[str, str]] = []
    for segment in _split_segments(body):
        segment = (segment or "").strip(" ,;")
        if not segment:
            continue
        for clause in _split_clauses(segment):
            clause = (clause or "").strip(" ,;")
            if clause:
                out.append((clause, segment))
    return out


def extract_interjections(content: str, max_clauses: int = _MAX_CLAUSES) -> list[str]:
    """Return durable-fact clauses buried in a conversational turn.

    Empty when the content is short/single-clause (the whole turn already IS
    the fact - extraction would just duplicate it) or when no cue fires.
    """
    if not content:
        return []

    speaker: str | None = None
    body = content.strip()
    m = _SPEAKER_RE.match(body)
    if m:
        speaker, body = m.group(1), m.group(2).strip()

    clauses = _clauses(body)
    if len(clauses) < 2:
        return []

    found: list[str] = []
    seen: set[str] = set()
    for clause, segment in clauses:
        # An aside marker trims the clause to the request itself ("remind me I
        # eat fish now") - but a trailing marker ("my day rate is $900 by the
        # way") leaves nothing after it, so fall back to the fact-cue whole
        # clause in that case.
        aside = _ASIDE_CUES.search(clause)
        if aside:
            tail = clause[aside.start():].strip(" ,;")
            if len(tail) >= _MIN_LEN:
                clause = tail
            elif not _FACT_CUES.search(clause):
                continue
        elif not _FACT_CUES.search(clause):
            continue
        if len(clause) < _MIN_LEN:
            clause = segment  # cue fired but the split left a stub - keep its segment
        if not (_MIN_LEN <= len(clause) <= _MAX_LEN):
            continue
        if len(clause) >= 0.8 * len(body):
            continue  # not buried - the turn is essentially this clause already
        key = clause.lower()
        if key in seen:
            continue
        seen.add(key)
        found.append(f"{speaker}: {clause}" if speaker else clause)
        if len(found) >= max_clauses:
            break
    return found
