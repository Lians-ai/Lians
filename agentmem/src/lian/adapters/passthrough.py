"""
Passthrough adapter — domain-agnostic default for non-financial verticals.

Use DOMAIN_ADAPTER=passthrough when deploying AgentMem for a domain where
you haven't yet written a custom adapter.  structured_keys defaults to an
empty set (no keyed supersession fast path), and normalize() returns the
value unchanged.

Healthcare teams, law firms, and government deployments can start here and
incrementally add their own structured_keys and normalize() logic.
"""
from __future__ import annotations

from .._types import _PASSTHROUGH_STRUCTURED_KEYS


class PassthroughAdapter:
    """No-op adapter: no entity normalization, no hardcoded structured keys."""

    @property
    def structured_keys(self) -> frozenset[str]:
        return _PASSTHROUGH_STRUCTURED_KEYS

    def normalize(self, key: str, value: str) -> str:
        return value.strip()

    def key_aliases(self, key: str) -> list[str]:
        return [key]
