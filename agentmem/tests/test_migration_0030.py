"""Contract tests for hosted MCP tenant-table RLS hardening."""

from __future__ import annotations

import importlib.util
from pathlib import Path


def _migration_source() -> str:
    path = (
        Path(__file__).resolve().parents[1]
        / "alembic"
        / "versions"
        / "0030_force_hosted_mcp_rls.py"
    )
    return path.read_text(encoding="utf-8")


def test_hosted_rls_migration_follows_the_current_head_and_forces_core_tables():
    source = _migration_source()

    assert 'down_revision = "0029_fix_experience_namespace_rls"' in source
    assert 'op.get_bind().dialect.name != "postgresql"' in source
    for table in (
        "event_log",
        "subject_keys",
        "namespace_policies",
        "agent_barrier_groups",
    ):
        assert f'"{table}"' in source
    for table in ("idempotency_keys", "conflict_flags"):
        assert f'"{table}"' in source
    assert "ALTER TABLE {table} FORCE ROW LEVEL SECURITY" in source
    assert "CREATE POLICY rls_{table}_namespace" in source
    assert "app.current_namespace" in source


def test_migration_file_is_importable():
    path = (
        Path(__file__).resolve().parents[1]
        / "alembic"
        / "versions"
        / "0030_force_hosted_mcp_rls.py"
    )
    spec = importlib.util.spec_from_file_location("migration_0030", path)
    assert spec is not None and spec.loader is not None
