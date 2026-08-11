"""Contract tests for workspace and connector tenant storage."""
from pathlib import Path


def _source() -> str:
    return (
        Path(__file__).resolve().parents[1]
        / "alembic"
        / "versions"
        / "0031_workspaces_connectors.py"
    ).read_text(encoding="utf-8")


def test_workspace_connector_migration_follows_head_and_enforces_rls():
    source = _source()
    assert 'down_revision = "0030_force_hosted_mcp_rls"' in source
    assert '"workspaces"' in source
    assert '"connectors"' in source
    assert "uq_connector_namespace_name" in source
    assert "ENABLE ROW LEVEL SECURITY" in source
    assert "FORCE ROW LEVEL SECURITY" in source
    assert "app.current_namespace" in source
