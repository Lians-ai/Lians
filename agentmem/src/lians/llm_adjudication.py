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
- CONFIRMS    : NEW expresses the same underlying value as OLD (paraphrase, rounding, unit variant).
- ADDS        : NEW is a related but distinct attribute — both facts remain valid.
- CONTRADICTS_SAME_TIME : conflicting values with no clear temporal ordering.

Rules:
1. A paraphrase or restatement of the same number → CONFIRMS, never SUPERSEDES.
2. A different numeric value (beyond rounding) → SUPERSEDES.
3. When uncertain, prefer SUPERSEDES in finance — missing a real update is worse than a false confirm.
4. Rationale must be one sentence max.

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


_EXTRACT_PROMPT = """\
You extract relationship triplets from text to build a knowledge graph for
regulated industries (finance, legal, healthcare). Extract every relationship
EXPLICITLY stated in the text as (source_entity, relation_type, destination_entity).

Rules:
- Entities are proper nouns (people, companies, funds, products, matters).
  Use the shortest unambiguous surface form; drop trailing punctuation.
- relation_type is lowercase snake_case (e.g. works_at, owns, controls, acquired,
  subsidiary_of, has_cfo, advises, represents, adverse_to, referred, director_of).
- Only extract what is explicitly stated. Do NOT infer or hallucinate.
- Return an empty list if there are no clear relationships.

TEXT:
{text}

Return ONLY valid JSON, no markdown fences:
{{"triplets":[{{"src":"...","rel":"...","dst":"..."}}]}}"""


async def extract_triplets(text: str) -> list[tuple[str, str, str]]:
    """
    LLM relationship extraction for the graph builder (Graphiti-style, opt-in).

    Returns ``(src, rel_type, dst)`` triplets. Best-effort: any error (missing
    key, bad JSON, network) returns an empty list so ``graph_extract`` can fall
    back to the deterministic extractor without a hard dependency on the model.
    """
    settings = get_settings()
    try:
        import anthropic
        client = anthropic.AsyncAnthropic(
            api_key=settings.anthropic_api_key or None,  # None → ANTHROPIC_API_KEY env var
        )
        message = await client.messages.create(
            model=settings.llm_adjudication_model,
            max_tokens=1024,
            messages=[{"role": "user", "content": _EXTRACT_PROMPT.format(text=text)}],
        )
        raw = message.content[0].text.strip()
        parsed = json.loads(raw)
    except Exception:
        return []

    out: list[tuple[str, str, str]] = []
    seen: set[tuple[str, str, str]] = set()
    for t in parsed.get("triplets", []):
        try:
            src = str(t["src"]).strip()
            rel = str(t["rel"]).strip().lower().replace(" ", "_")
            dst = str(t["dst"]).strip()
        except (KeyError, TypeError, AttributeError):
            continue
        triplet = (src, rel, dst)
        if src and rel and dst and src != dst and triplet not in seen:
            seen.add(triplet)
            out.append(triplet)
    return out
