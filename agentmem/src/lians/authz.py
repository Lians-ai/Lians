"""Shared authorization primitives for API keys and federated identities."""
from __future__ import annotations

from collections.abc import Iterable
from uuid import UUID

ROLE_SCOPES: dict[str, frozenset[str]] = {
    "owner": frozenset({"read", "write", "admin"}),
    "analyst": frozenset({"read", "write"}),
    "compliance": frozenset({"read", "admin"}),
    "readonly": frozenset({"read"}),
}

PRINCIPAL_REF_VERSION = "v1"


def oidc_principal_ref(provider_id: UUID | str, binding_id: UUID | str) -> str:
    """Canonical identity for one issuer-qualified, administrator-owned binding."""
    return (
        f"lians:principal:{PRINCIPAL_REF_VERSION}:oidc:"
        f"{UUID(str(provider_id))}:{UUID(str(binding_id))}"
    )


def api_key_principal_ref(credential_id: UUID | str) -> str:
    """Canonical identity for one API/workload credential, never its display label."""
    return f"lians:principal:{PRINCIPAL_REF_VERSION}:api-key:{UUID(str(credential_id))}"


def effective_scopes(role: str | None, explicit_scopes: Iterable[str] | None) -> list[str]:
    """Return a deterministic union of a named role and explicit grants."""
    scopes = set(explicit_scopes or ())
    if role:
        scopes.update(ROLE_SCOPES.get(role, ()))
    return sorted(scopes)
