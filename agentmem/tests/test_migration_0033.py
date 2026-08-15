"""Contract tests for signed device removal and future-key rotation evidence."""

from __future__ import annotations

import importlib.util
from pathlib import Path

MIGRATION = (
    Path(__file__).resolve().parents[1]
    / "alembic"
    / "versions"
    / "0033_sync_device_key_rotation.py"
)


def test_key_rotation_migration_extends_sync_head_and_forces_tenant_rls():
    source = MIGRATION.read_text(encoding="utf-8")
    assert 'down_revision = "0032_device_enrollment_exchange"' in source
    assert '"sync_key_rotations"' in source
    assert 'sa.Column("document", sa.JSON(), nullable=False)' in source
    assert 'sa.Column("signature", sa.JSON(), nullable=False)' in source
    assert "ALTER TABLE sync_key_rotations FORCE ROW LEVEL SECURITY" in source
    assert "CREATE POLICY rls_sync_key_rotations_namespace" in source
    assert "current_setting('app.current_namespace', true)" in source


def test_key_rotation_migration_file_is_importable():
    spec = importlib.util.spec_from_file_location("migration_0033", MIGRATION)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    assert module.revision == "0033_sync_device_key_rotation"
