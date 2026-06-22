"""
Finance domain adapter — wraps the entity normalizer for the DomainAdapter protocol.

This module is the only place in the codebase where finance-specific concepts
(tickers, ISINs, CUSIPs, equity name aliases) are permitted to exist.

The core engine imports only from adapters.get_adapter() — never directly from
here or from entity_normalizer.  That boundary is what lets the same core engine
serve healthcare, legal, or any other regulated vertical without modification.
"""
from __future__ import annotations

from typing import TYPE_CHECKING

from ..._types import _FINANCE_STRUCTURED_KEYS
from ...entity_normalizer import cached_normalize

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

# Maps each top-level structured key to all metadata field names that can carry it.
_KEY_ALIASES: dict[str, list[str]] = {
    "ticker": ["ticker", "entity", "isin", "cusip"],
    "metric": ["metric", "field"],
}


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
