"""Deterministic query planning for adaptive memory recall.

The planner broadens only questions whose wording signals that one embedding is
unlikely to cover the whole information need. It never sees benchmark labels,
answers, or evidence IDs, and it does not call an LLM.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

_TOKEN = re.compile(r"[a-z0-9][a-z0-9'-]*", re.I)
_STOP = frozenset(
    "a an and are as at be been but by did do does for from had has have he her "
    "hers him his how i in is it its me my of on or our she so that the their "
    "them they this to was we were what when where which who whom why will with "
    "you your".split()
)
_TEMPORAL = re.compile(
    r"\b(when|before|after|first|last|recent|previous|formerly|used to|how long|"
    r"date|time|year|month|week|day)\b", re.I
)
_AGGREGATE = re.compile(
    r"\b(all|both|activities|events|ways|examples|kinds|types|places|books|"
    r"jobs|hobbies|interests|changes|experiences|things)\b", re.I
)
_RELATIONAL = re.compile(
    r"\b(why|would|might|likely|relationship|together|influence|role|feel|"
    r"attitude|personality|nickname|advice|support|cope|reason)\b", re.I
)


@dataclass(frozen=True)
class QueryPlan:
    variants: tuple[str, ...]
    scopes: tuple[str, ...]
    complex: bool


def _keywords(query: str) -> str:
    words = [word.lower() for word in _TOKEN.findall(query)]
    return " ".join(word for word in words if word not in _STOP)


def plan_query(query: str, max_variants: int = 4) -> QueryPlan:
    """Return a bounded, stable set of semantic search facets.

    The original question is always first and carries the strongest fusion
    weight. Facets describe retrieval intent rather than guessing an answer.
    """
    original = " ".join(str(query or "").split())
    if not original:
        return QueryPlan(("",), ("episodic",), False)

    core = _keywords(original)
    variants = [original]
    scopes = ["episodic"]

    if _TEMPORAL.search(original):
        variants.append(f"{core} chronology date sequence prior later")
        scopes.append("temporal")
    if _AGGREGATE.search(original):
        variants.append(f"{core} complete history examples")
        scopes.append("history")
    if _RELATIONAL.search(original):
        variants.append(f"{core} relationships background reasons preferences")
        scopes.append("relational")

    seen: set[str] = set()
    unique: list[tuple[str, str]] = []
    for variant, scope in zip(variants, scopes):
        normalized = " ".join(variant.split()).lower()
        if normalized and normalized not in seen:
            seen.add(normalized)
            unique.append((variant, scope))

    bounded = unique[:max(1, max_variants)]
    return QueryPlan(
        tuple(item[0] for item in bounded),
        tuple(item[1] for item in bounded),
        len(bounded) > 1,
    )
