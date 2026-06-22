"""
Shared type constants — imported by both core modules and adapter modules.

Keeping these here breaks the circular import that would occur if adapters
imported from supersession.py and supersession.py imported from adapters.
"""

# Finance adapter: metadata keys that identify a structured financial fact.
# These keys trigger the keyed supersession fast path and live_facts indexing.
_FINANCE_STRUCTURED_KEYS: frozenset[str] = frozenset({
    "ticker", "metric", "entity", "instrument", "cusip", "isin", "field",
})

# Passthrough adapter: no structured keys (pure semantic supersession only).
_PASSTHROUGH_STRUCTURED_KEYS: frozenset[str] = frozenset()
