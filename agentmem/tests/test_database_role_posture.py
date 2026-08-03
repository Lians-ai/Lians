"""Fail-closed contracts for the shared PostgreSQL runtime-role posture."""

from __future__ import annotations

import inspect
from types import SimpleNamespace

import pytest
from lians import audit_chain, main
from lians.database_role_posture import database_role_posture_status


class _NonPostgresSession:
    def get_bind(self):
        return SimpleNamespace(dialect=SimpleNamespace(name="sqlite"))


@pytest.mark.asyncio
async def test_non_postgres_role_posture_is_explicitly_unenforced() -> None:
    status = await database_role_posture_status(_NonPostgresSession())  # type: ignore[arg-type]
    assert {key: status[key] for key in (
        "backend", "enforced", "role", "checks", "attributes"
    )} == {
        "backend": "sqlite",
        "enforced": False,
        "role": None,
        "checks": {"postgresql_backend": False},
        "attributes": {},
    }
    assert status["tenant_isolation"] == {
        "backend": "sqlite",
        "enforced": False,
        "checks": {"postgresql_backend": False},
        "relations": [],
        "violations": ["postgresql_backend_required"],
        "exceptions": {},
    }


def test_role_posture_checks_ownership_and_assumable_owner_roles() -> None:
    source = inspect.getsource(database_role_posture_status)
    for required in (
        "rolsuper",
        "rolbypassrls",
        "rolcreatedb",
        "rolcreaterole",
        "rolreplication",
        "pg_has_role",
        "runtime_owns_application_object",
        "capability_owns_application_object",
        "runtime_can_assume_application_owner",
        "capability_can_assume_application_owner",
    ):
        assert required in source
    assert "session_user AS session_role" in source
    assert "rolname = 'lians_runtime'" in source


def test_startup_audit_and_continuous_health_share_role_posture() -> None:
    assert "database_role_posture_status" in inspect.getsource(
        audit_chain.audit_append_boundary_status
    )
    health_source = inspect.getsource(main.health)
    assert "database_role_posture_status" in health_source
    assert '"error: unsafe_posture"' in health_source
