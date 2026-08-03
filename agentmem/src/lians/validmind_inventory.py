"""Deterministic identifiers shared by the ValidMind API and SQLite triggers."""

from __future__ import annotations

import hashlib


def validmind_external_id(
    kind: str,
    source_id: str,
    scope_id: str | None = None,
) -> str:
    """Return the stable opaque identifier for one integration resource.

    Model identities became barrier-scoped in the 0.5 contract. Agent identities
    remain namespace-wide and therefore deliberately omit a scope component.
    """
    if kind == "model":
        if not scope_id:
            raise ValueError("model external IDs require an opaque scope_id")
        material = f"model:{scope_id}:{source_id}"
    elif kind == "agent":
        material = f"agent:{source_id}"
    else:
        raise ValueError(f"unsupported ValidMind resource kind: {kind}")
    digest = hashlib.sha256(material.encode()).hexdigest()[:20]
    return f"lians-{kind}-{digest}"


def validmind_legacy_model_id(source_id: str) -> str:
    """Return the namespace-wide model ID used before the scoped 0.5 contract."""
    digest = hashlib.sha256(f"model:{source_id}".encode()).hexdigest()[:20]
    return f"lians-model-{digest}"
