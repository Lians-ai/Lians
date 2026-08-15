"""Contract tests for the zero-knowledge device enrollment exchange."""

from __future__ import annotations

import importlib.util
from pathlib import Path


def _path() -> Path:
    return (
        Path(__file__).resolve().parents[1]
        / "alembic"
        / "versions"
        / "0032_device_enrollment_exchange.py"
    )


def test_enrollment_migration_extends_sync_head_and_forces_tenant_rls():
    source = _path().read_text(encoding="utf-8")

    assert 'down_revision = "0031_zero_knowledge_sync"' in source
    assert '"sync_enrollments"' in source
    assert 'ondelete="CASCADE"' in source
    assert "ALTER TABLE sync_enrollments FORCE ROW LEVEL SECURITY" in source
    assert "CREATE POLICY rls_sync_enrollments_namespace" in source
    assert "app.current_namespace" in source
    for field in (
        "request_id",
        "namespace",
        "verification_code",
        "request",
        "approval",
        "expires_at",
    ):
        assert f'"{field}"' in source


def test_enrollment_migration_file_is_importable():
    spec = importlib.util.spec_from_file_location("migration_0032", _path())
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    assert module.revision == "0032_device_enrollment_exchange"
