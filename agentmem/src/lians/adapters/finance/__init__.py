"""
Finance domain adapter — wraps the entity normalizer for the DomainAdapter protocol.

This module is the only place in the codebase where finance-specific concepts
(tickers, ISINs, CUSIPs, equity name aliases) are permitted to exist.

The core engine imports only from adapters.get_adapter() — never directly from
here or from entity_normalizer.  That boundary is what lets the same core engine
serve healthcare, legal, or any other regulated vertical without modification.
"""
from __future__ import annotations

import re
from functools import lru_cache
from typing import TYPE_CHECKING, Optional

from ..._types import _FINANCE_STRUCTURED_KEYS
from ...entity_normalizer import cached_normalize, known_aliases

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

# Maps each top-level structured key to all metadata field names that can carry it.
_KEY_ALIASES: dict[str, list[str]] = {
    "ticker": ["ticker", "entity", "isin", "cusip"],
    "metric": ["metric", "field"],
}


# ── Deterministic auto-metadata extraction ──────────────────────────────────────
#
# Turns free-text like "AAPL price target raised to $250" into the structured
# keys {"ticker": "AAPL", "metric": "price_target"} so the core's keyed
# supersession fast path can fire on plain-text writes.  Pure, rule-based, and
# reproducible — no model, no network — mirroring graph_extract's posture.

# $CASHTAG — an explicit ticker signal (1-5 letters, no leading digit).
_CASHTAG_RE = re.compile(r"\$([A-Za-z]{1,5})\b")
# A bare all-caps token that could be a ticker; only accepted if the normalizer
# already knows it (guards against "EPS", "CEO", "USD", "Q3", …).
_UPPER_TOKEN_RE = re.compile(r"\b([A-Z]{1,5})\b")

# Metric keyword → canonical metric.  Ordered most-specific first so that, e.g.,
# "earnings per share" resolves to eps rather than a broader earnings match.
_METRIC_PATTERNS: list[tuple[re.Pattern, str]] = [
    (re.compile(r"\b(?:earnings per share|eps)\b", re.I), "eps"),
    (re.compile(r"\b(?:price target|target price|pt)\b", re.I), "price_target"),
    (re.compile(r"\b(?:free cash flow|fcf)\b", re.I), "fcf"),
    (re.compile(r"\b(?:gross|operating|net)\s+margin\b", re.I), "margin"),
    (re.compile(r"\bmargin\b", re.I), "margin"),
    (re.compile(r"\b(?:revenue|sales|top[\s-]?line)\b", re.I), "revenue"),
    (re.compile(r"\b(?:net income|profit)\b", re.I), "net_income"),
    (re.compile(r"\b(?:guidance|outlook|forecast)\b", re.I), "guidance"),
    (re.compile(r"\bdividend\b", re.I), "dividend"),
    (re.compile(
        r"\b(?:rating|recommendation|upgrade[ds]?|downgrade[ds]?|"
        r"overweight|underweight|reiterate[ds]?)\b", re.I), "rating"),
]


@lru_cache(maxsize=1)
def _alias_pattern() -> Optional[re.Pattern]:
    """One combined, word-boundary regex over all known name aliases (len ≥ 2),
    longest-first so 'advanced micro devices' wins over 'amd'.  Cached; the alias
    set is fixed at process start."""
    aliases = sorted((a for a in known_aliases() if len(a) >= 2), key=len, reverse=True)
    if not aliases:
        return None
    alt = "|".join(re.escape(a) for a in aliases)
    return re.compile(rf"\b({alt})\b", re.I)


def _detect_ticker(content: str) -> Optional[str]:
    # 1. Cashtag — explicit, accept even if the symbol is unknown.
    m = _CASHTAG_RE.search(content)
    if m:
        return cached_normalize("ticker", m.group(1))
    # 2. Known company name / alias appearing in the text.
    pat = _alias_pattern()
    if pat:
        m = pat.search(content)
        if m:
            return cached_normalize("ticker", m.group(1))
    # 3. Bare all-caps token, but only if the normalizer already knows it.
    known = known_aliases()
    for m in _UPPER_TOKEN_RE.finditer(content):
        tok = m.group(1)
        if tok.lower() in known:
            return cached_normalize("ticker", tok)
    return None


def _detect_metric(content: str) -> Optional[str]:
    for pattern, metric in _METRIC_PATTERNS:
        if pattern.search(content):
            return metric
    return None


def extract_finance_keys(content: str) -> dict[str, str]:
    """Best-effort structured keys from free text: {ticker?, metric?}.

    May return {}, a partial set, or a full {ticker, metric} — the core decides
    what to do with it.  A full set unlocks deterministic keyed supersession.
    """
    out: dict[str, str] = {}
    ticker = _detect_ticker(content)
    if ticker:
        out["ticker"] = ticker
    metric = _detect_metric(content)
    if metric:
        out["metric"] = metric
    return out


class FinanceAdapter:
    """
    Finance domain adapter: ticker/ISIN/CUSIP normalization + financial structured keys.

    Structured keys are the metadata fields that identify a financial fact:
      ticker / entity / isin / cusip — what instrument
      metric / field                 — what attribute (eps, price_target, revenue, …)
      instrument                     — instrument type (equity, bond, option, …)

    normalize() maps any of: company name, ISIN, CUSIP, or ticker alias → canonical ticker.
    key_aliases() tells the core which metadata fields are synonymous for a given key.
    fact_history() is the finance-specific entry point for the structured-fact time series.
    """

    @property
    def structured_keys(self) -> frozenset[str]:
        return _FINANCE_STRUCTURED_KEYS

    def normalize(self, key: str, value: str) -> str:
        return cached_normalize(key, value)

    def key_aliases(self, key: str) -> list[str]:
        return _KEY_ALIASES.get(key, [key])

    def extract_structured_keys(self, content: str) -> dict[str, str]:
        """Derive {ticker?, metric?} from free text for auto-metadata ingestion.

        Deterministic and reproducible; returns {} when nothing is recognized.
        """
        return extract_finance_keys(content)

    async def fact_history(
        self,
        db: "AsyncSession",
        namespace: str,
        agent_id: str,
        ticker: str,
        metric: str,
        limit: int = 100,
    ):
        """
        Return all versions of a ticker+metric fact, ordered by event_time ascending.

        Translates finance-specific ticker/metric params into the domain-agnostic
        key_values dict, normalizes via entity_normalizer, then delegates to the
        core get_structured_fact_history().
        """
        from ...memory_service import get_structured_fact_history

        key_values = {
            "ticker": cached_normalize("ticker", ticker),
            "metric": metric.strip(),
        }
        return await get_structured_fact_history(db, namespace, agent_id, key_values, self, limit)
