"""
Stage 3 LLM adjudication for the supersession engine.

Called when Stage 2's rule-based classifier returns SUPERSEDES but we want
to verify whether the content genuinely changed or is a paraphrase of the
same fact (which should be CONFIRMS, not SUPERSEDES).

Key properties:
- Disabled by default (config.supersession_llm_stage = False)
- In-memory cache keyed by (hash(old), hash(new)) — same pair never
  adjudicated twice within a process lifetime
- Falls back to ("SUPERSEDES", 0.7, "llm_error: ...") on any failure so
  the write path is never blocked by an LLM outage
- Uses claude-haiku for cost discipline; Stage 3 should be rare
"""
from __future__ import annotations

import hashlib
import json
from typing import Any

from .config import get_settings


# In-process cache: (short_hash_old, short_hash_new) -> (relation, confidence, rationale)
_CACHE: dict[tuple[str, str], tuple[str, float, str]] = {}


def _pair_key(old: str, new: str) -> tuple[str, str]:
    h = lambda s: hashlib.sha256(s.encode()).hexdigest()[:16]
    return (h(old), h(new))


_PROMPT = """\
You are a financial-data fact classifier. Two facts about the same entity and attribute are given below.

OLD: {old}
NEW: {new}
Metadata: {meta}

Classify the relationship. Choose exactly one:
- SUPERSEDES  : NEW has a genuinely different value — the old fact is now stale.
- REFINES     : NEW keeps the old value but narrows it (adds scope, segment, or precision).
- CONFIRMS    : NEW expresses the same underlying value as OLD (paraphrase, rounding, unit variant).
- ADDS        : NEW is a related but distinct attribute — both facts remain valid.
- CONTRADICTS_SAME_TIME : conflicting values with no clear temporal ordering.

Rules:
1. A paraphrase or restatement of the same number → CONFIRMS, never SUPERSEDES.
2. A different numeric value (beyond rounding) → SUPERSEDES.
3. Same value with added qualification or scope → REFINES, not CONFIRMS.
4. When uncertain, prefer SUPERSEDES in finance — missing a real update is worse than a false confirm.
5. Rationale must be one sentence max.

Return ONLY valid JSON, no markdown fences:
{{"relation":"...","confidence":0.0,"rationale":"..."}}"""


async def llm_adjudicate(
    old_content: str,
    new_content: str,
    meta: dict[str, Any],
) -> tuple[str, float, str]:
    """
    Returns (relation, confidence, rationale).
    Cache hit: returns immediately. Cache miss: calls LLM.
    Any exception: returns safe fallback without raising.
    """
    key = _pair_key(old_content, new_content)
    if key in _CACHE:
        return _CACHE[key]

    settings = get_settings()
    prompt = _PROMPT.format(
        old=old_content,
        new=new_content,
        meta=json.dumps(meta, separators=(",", ":")),
    )

    try:
        import anthropic
        client = anthropic.AsyncAnthropic(
            api_key=settings.anthropic_api_key or None,  # None → reads ANTHROPIC_API_KEY env var
        )
        message = await client.messages.create(
            model=settings.llm_adjudication_model,
            max_tokens=128,
            messages=[{"role": "user", "content": prompt}],
        )
        raw = message.content[0].text.strip()
        parsed = json.loads(raw)
        relation = str(parsed["relation"])
        confidence = float(parsed["confidence"])
        rationale = str(parsed.get("rationale", ""))
    except Exception as exc:
        relation = "SUPERSEDES"
        confidence = 0.70
        rationale = f"llm_error: {type(exc).__name__}"

    result: tuple[str, float, str] = (relation, confidence, rationale)
    _CACHE[key] = result
    return result


# ── LLM relationship extraction (opt-in graph builder) ────────────────────────
#
# Backs graph_extract.extract_llm (GRAPH_EXTRACT_LLM / use_llm=true). Turns free
# text into (src, rel_type, dst) triplets. Steered toward the same snake_case
# vocabulary the deterministic extractor emits so LLM- and rule-derived edges are
# interchangeable for path/neighbor queries. Unlike adjudication, this RAISES on
# failure so the caller (extract_llm) cleanly falls back to the rule-based
# extractor instead of silently returning an empty graph.

# In-process cache: text hash -> triplets (same text never re-extracted per process).
_TRIPLET_CACHE: dict[str, list[tuple[str, str, str]]] = {}

# The relation vocabulary the rule-based extractor produces; the model is asked to
# prefer these but may emit other snake_case verbs for relationships they don't cover.
_KNOWN_RELS = [
    "works_at", "owns", "controls", "subsidiary_of", "represents",
    "adverse_to", "referred", "advises", "director_of",
]

_EXTRACT_PROMPT = """\
Extract the explicit relationships stated in the text as directed triplets.

Text: {text}

Rules:
1. One triplet per stated relationship: [subject, relation, object].
2. subject and object are proper-noun entities exactly as written (people, firms, funds, issuers).
3. relation is a lowercase snake_case verb phrase. Prefer these when they fit: {rels}.
4. Only extract relationships the text actually asserts — never infer or invent.
5. If the text asserts no relationships, return [].

Return ONLY a JSON array of [subject, relation, object] arrays, no markdown fences. Example:
[["Alice","works_at","Acme"],["Fund A","owns","Issuer X"]]"""


async def extract_triplets(text: str) -> list[tuple[str, str, str]]:
    """
    Extract ``(src, rel_type, dst)`` triplets from free text via the LLM.

    Raises on any API/parse failure so the caller can fall back to the
    deterministic extractor.  A successful call that finds nothing returns [].
    """
    key = hashlib.sha256(text.encode()).hexdigest()[:32]
    if key in _TRIPLET_CACHE:
        return _TRIPLET_CACHE[key]

    settings = get_settings()
    prompt = _EXTRACT_PROMPT.format(text=text, rels=", ".join(_KNOWN_RELS))

    import anthropic
    client = anthropic.AsyncAnthropic(
        api_key=settings.anthropic_api_key or None,  # None → reads ANTHROPIC_API_KEY env var
    )
    message = await client.messages.create(
        model=settings.llm_adjudication_model,
        max_tokens=512,
        messages=[{"role": "user", "content": prompt}],
    )
    raw = message.content[0].text.strip()
    # Tolerate a stray ```json fence even though we ask for none.
    if raw.startswith("```"):
        raw = raw.strip("`")
        raw = raw[raw.find("[") : raw.rfind("]") + 1]
    parsed = json.loads(raw)

    triplets: list[tuple[str, str, str]] = []
    seen: set[tuple[str, str, str]] = set()
    for item in parsed:
        if not isinstance(item, (list, tuple)) or len(item) != 3:
            continue
        src, rel, dst = (str(x).strip() for x in item)
        rel = rel.lower().replace(" ", "_").replace("-", "_")
        triplet = (src, rel, dst)
        if src and rel and dst and src != dst and triplet not in seen:
            seen.add(triplet)
            triplets.append(triplet)

    _TRIPLET_CACHE[key] = triplets
    return triplets
