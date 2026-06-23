"""
Domain adapter protocol — the boundary between the correctness/compliance core
and domain-specific logic (financial entity normalization, legal matter IDs,
healthcare patient identifiers, etc.).

The core knows about: time, revision, supersession, audit, erasure.
The core does NOT know about: tickers, ISINs, CUSIPs, or any finance concept.

To add a new vertical (healthcare, legal, gov):
  1. Create adapters/<vertical>/__init__.py implementing DomainAdapter.
  2. Set DOMAIN_ADAPTER=<vertical> in the environment.
  3. The core picks it up at startup — no core changes needed.

This is the architectural decision that lets the same engine serve a hospital
reconstructing "what did the triage agent know at 3am" or a law firm answering
"what did the agent know before the privilege cutoff" — same primitives, new adapter.
"""
from __future__ import annotations

from typing import Protocol, runtime_checkable


@runtime_checkable
class DomainAdapter(Protocol):
    """
    Interface for domain-specific entity normalization and structured-key definitions.

    Implementations live in adapters/<vertical>/ and are registered by name.
    The core calls only these two methods — nothing domain-specific leaks in.
    """

    @property
    def structured_keys(self) -> frozenset[str]:
        """
        Set of metadata keys treated as structured fact identifiers.

        For finance: {"ticker", "metric", "entity", "isin", "cusip", "instrument", "field"}
        For healthcare: might be {"patient_id", "condition", "medication"}
        For legal: might be {"matter_id", "jurisdiction", "claim_type"}

        Memories with any of these keys participate in the keyed supersession fast path
        and the live_facts index.
        """
        ...

    def normalize(self, key: str, value: str) -> str:
        """
        Normalize an entity value for a given metadata key.

        Finance example: normalize("ticker", "Apple Inc.") → "AAPL"
        Finance example: normalize("ticker", "US0378331005") → "AAPL"  (ISIN)
        Default: return value.strip()
        """
        ...

    def key_aliases(self, key: str) -> list[str]:
        """
        Return all metadata field names that are aliases for a structured key.

        Used by the core engine to match memories without knowing domain vocabulary.

        Finance example: key_aliases("ticker") → ["ticker", "entity", "isin", "cusip"]
        Finance example: key_aliases("metric") → ["metric", "field"]
        Default: return [key]  (no aliasing)
        """
        ...


# ── Adapter registry ──────────────────────────────────────────────────────────

_registry: dict[str, DomainAdapter] = {}


def register_adapter(name: str, adapter: DomainAdapter) -> None:
    _registry[name] = adapter


def get_adapter() -> DomainAdapter:
    """Return the active domain adapter (configured by DOMAIN_ADAPTER env var)."""
    from ..config import get_settings
    name = get_settings().domain_adapter
    if name in _registry:
        return _registry[name]
    # Lazy load built-in adapters
    if name == "finance":
        from .finance import FinanceAdapter
        adapter = FinanceAdapter()
        _registry[name] = adapter
        return adapter
    if name == "passthrough":
        from .passthrough import PassthroughAdapter
        adapter = PassthroughAdapter()
        _registry[name] = adapter
        return adapter
    if name == "healthcare":
        from .healthcare import HealthcareAdapter
        adapter = HealthcareAdapter()
        _registry[name] = adapter
        return adapter
    if name == "legal":
        from .legal import LegalAdapter
        adapter = LegalAdapter()
        _registry[name] = adapter
        return adapter
    raise ValueError(
        f"Unknown DOMAIN_ADAPTER '{name}'. "
        "Built-in values: 'finance', 'passthrough', 'healthcare', 'legal'. "
        "Register a custom adapter with adapters.register_adapter()."
    )
