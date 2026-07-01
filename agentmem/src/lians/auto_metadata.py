"""
Auto-metadata extraction — auto-supersession parity (mem0 / Zep style).

mem0 and Zep let a caller send plain text and quietly infer the structured facts
needed to decide what that text supersedes.  Lians offers the same convenience
but keeps its regulated-determinism posture:

  * the default extractor is **rule-based** (deterministic, reproducible, no
    model, no network) and lives in the **domain adapter** — the core never
    learns what a ticker or a metric is;
  * caller-supplied structured keys are **authoritative** and never overridden;
  * every auto-derived key is **provenance-tagged** under ``metadata._auto_meta``
    so an examiner can tell machine-inferred keys from human-supplied ones;
  * the whole path is **fail-open** — an extractor error never blocks a write;
  * it is **opt-in** (``config.auto_metadata_enabled``), so existing deployments
    keep caller-only keying and identical behavior.

When enabled and the caller sent *no* structured keys, ``enrich_metadata`` fills
them from the content so ``run_supersession``'s deterministic keyed fast path can
fire on a plain-text write.
"""
from __future__ import annotations

import json
import logging
from typing import Any, Optional

logger = logging.getLogger("agentmem.auto_metadata")


def _structured_present(meta: dict[str, Any], adapter) -> bool:
    """True if the caller already supplied at least one structured key with a value."""
    sk = adapter.structured_keys
    return any(k in sk and meta.get(k) not in (None, "") for k in meta)


async def enrich_metadata(
    content: str,
    meta: dict[str, Any],
    *,
    adapter,
    settings,
) -> tuple[dict[str, Any], Optional[dict[str, Any]]]:
    """
    Return ``(metadata, provenance)``.

    ``provenance`` is None when nothing was auto-derived (feature disabled, caller
    already keyed the write, or the extractor found nothing) — in that case
    ``metadata`` is returned unchanged.  Otherwise ``metadata`` is a new dict with
    the derived structured keys merged in and a ``_auto_meta`` provenance block.
    """
    meta = dict(meta or {})

    if not getattr(settings, "auto_metadata_enabled", False):
        return meta, None

    # Respect caller-supplied structured keys — never override authoritative tagging.
    if _structured_present(meta, adapter):
        return meta, None

    derived: dict[str, Any] = {}
    method = "rule"

    extractor = getattr(adapter, "extract_structured_keys", None)
    if callable(extractor):
        try:
            derived = extractor(content) or {}
        except Exception as exc:  # fail-open
            logger.warning("rule-based auto-metadata extraction failed: %s", exc)
            derived = {}

    if not derived and getattr(settings, "auto_metadata_llm", False):
        derived = await _llm_extract(content, adapter, settings)
        method = "llm"

    # Keep only real structured keys, normalized through the adapter, non-empty.
    sk = adapter.structured_keys
    clean: dict[str, str] = {}
    for key, value in derived.items():
        if key not in sk or value in (None, ""):
            continue
        try:
            clean[key] = adapter.normalize(key, str(value))
        except Exception:
            clean[key] = str(value).strip()

    if not clean:
        return meta, None

    enriched = {**meta, **clean}
    provenance: dict[str, Any] = {"keys": sorted(clean), "method": method}
    if method == "llm":
        provenance["model"] = getattr(settings, "auto_metadata_model", "")
    enriched["_auto_meta"] = provenance
    return enriched, provenance


_LLM_PROMPT = """\
You extract structured fact identifiers from a short piece of text.

Allowed keys (return only these; omit any you cannot fill with confidence):
{keys}

Text: {content}

Return ONLY a compact JSON object mapping key -> value, no markdown fences.
If nothing is confidently identifiable, return {{}}."""


async def _llm_extract(content: str, adapter, settings) -> dict[str, Any]:
    """
    Optional LLM fallback, used only when the deterministic extractor found
    nothing and ``auto_metadata_llm`` is enabled.  Best-effort: any failure
    returns {} so the write path is never blocked.
    """
    keys = sorted(adapter.structured_keys)
    if not keys:
        return {}
    prompt = _LLM_PROMPT.format(keys=", ".join(keys), content=content)
    try:
        import anthropic

        client = anthropic.AsyncAnthropic(
            api_key=settings.anthropic_api_key or None,  # None → ANTHROPIC_API_KEY env var
        )
        message = await client.messages.create(
            model=settings.auto_metadata_model,
            max_tokens=200,
            messages=[{"role": "user", "content": prompt}],
        )
        raw = message.content[0].text.strip()
        data = json.loads(raw)
        if isinstance(data, dict):
            allowed = set(keys)
            return {
                k: v for k, v in data.items()
                if k in allowed and isinstance(v, (str, int, float))
            }
    except Exception as exc:  # fail-open
        logger.warning("LLM auto-metadata extraction failed: %s", exc)
    return {}
