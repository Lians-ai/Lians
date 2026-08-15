"""Contract tests for opaque zero-knowledge sync persistence."""

from __future__ import annotations

import importlib.util
from pathlib import Path


def _source() -> str:
    path = (
        Path(__file__).resolve().parents[1]
        / "alembic"
        / "versions"
        / "0031_zero_knowledge_sync.py"
    )
    return path.read_text(encoding="utf-8")


def test_sync_migration_extends_current_head_and_forces_tenant_rls():
    source = _source()

    assert 'down_revision = "0030_force_hosted_mcp_rls"' in source
    for table in ("sync_workspaces", "sync_devices", "sync_revisions"):
        assert f'"{table}"' in source
    assert "ALTER TABLE {table} FORCE ROW LEVEL SECURITY" in source
    assert "CREATE POLICY rls_{table}_namespace" in source
    assert "app.current_namespace" in source
    assert 'ondelete="CASCADE"' in source
    assert 'name="uq_sync_revision_object_hash"' in source


def test_sync_migration_file_is_importable():
    path = (
        Path(__file__).resolve().parents[1]
        / "alembic"
        / "versions"
        / "0031_zero_knowledge_sync.py"
    )
    spec = importlib.util.spec_from_file_location("migration_0031", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    assert module.revision == "0031_zero_knowledge_sync"
