"""
Relationship extraction — turn unstructured text into graph edges.

Graphiti (Zep) auto-builds its knowledge graph by having an LLM extract entities
and relationships from every message. Lians offers the same convenience, but
keeps the regulated-determinism posture: the **default extractor is rule-based**
(deterministic, reproducible, no model, no network), so the edges it writes are
auditable and stable. An optional LLM extractor can be enabled for messier text,
but it is opt-in and never the only path.

Each extractor returns a list of ``(src_entity, rel_type, dst_entity)`` triplets;
callers feed them to ``graph_service.relate`` so the edges inherit the bitemporal
+ audit-chain + barrier machinery.
"""
from __future__ import annotations

import re
from typing import Optional

# Entity = one or more Capitalized tokens (proper nouns), allowing &, ., -.
_ENT = r"([A-Z][A-Za-z0-9&.\-]*(?:\s+[A-Z][A-Za-z0-9&.\-]*)*)"

# (regex, rel_type). Verbs are matched literally (lowercase); entities must be
# capitalized. Ordered most-specific first. Tuned for the regulated verticals:
# employment, ownership/control (finance), representation/conflict (legal),
# referral (healthcare).
_PATTERNS: list[tuple[re.Pattern, str]] = [
    (re.compile(rf"{_ENT}\s+works\s+(?:at|for)\s+{_ENT}"), "works_at"),
    (re.compile(rf"{_ENT}\s+is\s+employed\s+by\s+{_ENT}"), "works_at"),
    (re.compile(rf"{_ENT}\s+(?:wholly\s+)?owns\s+{_ENT}"), "owns"),
    (re.compile(rf"{_ENT}\s+holds\s+a\s+stake\s+in\s+{_ENT}"), "owns"),
    (re.compile(rf"{_ENT}\s+controls\s+{_ENT}"), "controls"),
    (re.compile(rf"{_ENT}\s+is\s+a\s+subsidiary\s+of\s+{_ENT}"), "subsidiary_of"),
    (re.compile(rf"{_ENT}\s+represents?\s+{_ENT}"), "represents"),
    (re.compile(rf"{_ENT}\s+represented\s+{_ENT}"), "represents"),
    (re.compile(rf"{_ENT}\s+(?:is\s+adverse\s+to|versus|vs\.?)\s+{_ENT}"), "adverse_to"),
    (re.compile(rf"{_ENT}\s+referred\s+{_ENT}"), "referred"),
    (re.compile(rf"{_ENT}\s+advises?\s+{_ENT}"), "advises"),
    (re.compile(rf"{_ENT}\s+is\s+a\s+director\s+of\s+{_ENT}"), "director_of"),
]


def _clean_entity(s: str) -> str:
    # Collapse whitespace and drop trailing sentence punctuation that the greedy
    # capitalized-token match may have swept up (e.g. "Acme." -> "Acme").
    return re.sub(r"[.,;:]+$", "", " ".join(s.split()))


def extract_rule_based(text: str) -> list[tuple[str, str, str]]:
    """Deterministic pattern extraction. Returns unique ``(src, rel, dst)`` triplets."""
    seen: set[tuple[str, str, str]] = set()
    out: list[tuple[str, str, str]] = []
    # Work sentence-by-sentence so a multi-token entity can't span a sentence break.
    for sentence in re.split(r"(?<=[.!?])\s+", text):
        for pattern, rel in _PATTERNS:
            for m in pattern.finditer(sentence):
                src = _clean_entity(m.group(1))
                dst = _clean_entity(m.group(2))
                triplet = (src, rel, dst)
                if src and dst and src != dst and triplet not in seen:
                    seen.add(triplet)
                    out.append(triplet)
    return out


async def extract_llm(text: str) -> list[tuple[str, str, str]]:
    """
    Optional LLM extraction (opt-in). Best-effort: if the model or its key is
    unavailable, falls back to the deterministic extractor so a write path never
    hard-depends on an external service.
    """
    try:
        from .config import get_settings
        settings = get_settings()
        if not getattr(settings, "graph_extract_llm", False):
            return extract_rule_based(text)
        # The LLM path reuses the adjudication client; kept intentionally small.
        from .llm_adjudication import extract_triplets  # type: ignore
        triplets = await extract_triplets(text)
        return [(s, r, d) for (s, r, d) in triplets if s and d and s != d]
    except Exception:
        return extract_rule_based(text)


async def extract_relationships(text: str, *, use_llm: bool = False) -> list[tuple[str, str, str]]:
    """Extract ``(src, rel, dst)`` triplets — rule-based by default, LLM if asked + configured."""
    if use_llm:
        return await extract_llm(text)
    return extract_rule_based(text)
