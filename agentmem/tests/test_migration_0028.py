"""Regression tests for Decision Envelope migration safety."""

from __future__ import annotations

import importlib.util
from pathlib import Path
from types import SimpleNamespace


class _EmptyRows:
    def mappings(self) -> list[dict[str, object]]:
        return []


class _RecordingBind:
    def __init__(self, dialect_name: str) -> None:
        self.dialect = SimpleNamespace(name=dialect_name)
        self.statements: list[str] = []

    def execute(
        self,
        statement: object,
        params: dict[str, object] | None = None,
    ) -> _EmptyRows:
        del params
        self.statements.append(str(statement))
        return _EmptyRows()


def _load_migration_module():
    migration_path = (
        Path(__file__).parents[1]
        / "alembic"
        / "versions"
        / "0028_decision_envelopes.py"
    )
    spec = importlib.util.spec_from_file_location(
        "migration_0028_decision_envelopes",
        migration_path,
    )
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_backfill_enters_transaction_local_rls_admin_context(monkeypatch) -> None:
    migration = _load_migration_module()
    bind = _RecordingBind("postgresql")
    monkeypatch.setattr(migration.op, "get_bind", lambda: bind)

    migration._backfill_existing_decisions()

    assert "set_config('app.current_namespace', '__admin__', true)" in bind.statements[0]
    assert "set_config('agentmem.barrier_group', '', true)" in bind.statements[1]
    assert "FROM decision_records" in bind.statements[2]


def test_backfill_does_not_emit_postgres_settings_on_other_dialects(
    monkeypatch,
) -> None:
    migration = _load_migration_module()
    bind = _RecordingBind("sqlite")
    monkeypatch.setattr(migration.op, "get_bind", lambda: bind)

    migration._backfill_existing_decisions()

    assert len(bind.statements) == 1
    assert "FROM decision_records" in bind.statements[0]
