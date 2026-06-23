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
