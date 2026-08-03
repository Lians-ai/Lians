"""Backfill and contract the database-owned audit append boundary.

Revision ID: 0039a_audit_append_contract
Revises: 0039_audit_append_boundary

The expand revision adds only the nullable ordering column. This online data
revision advances linear namespace chains in independently committed bounded
frontier pages, repairs an interrupted concurrent unique-index build, briefly
fences the final write tail, and installs the append contract atomically with
the Alembic stamp.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path
from types import ModuleType

from alembic import context, op

revision = "0039a_audit_append_contract"
down_revision = "0039_audit_append_boundary"
branch_labels = None
depends_on = None


def _expand_module() -> ModuleType:
    path = Path(__file__).with_name("0039_audit_append_boundary.py")
    spec = importlib.util.spec_from_file_location("lians_migration_0039_expand", path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Cannot load audit contract implementation from {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def upgrade() -> None:
    if op.get_bind().dialect.name == "postgresql" and context.is_offline_mode():
        raise RuntimeError(
            "0039a_audit_append_contract requires an online PostgreSQL "
            "connection so bounded position pages and the concurrent index "
            "commit and resume safely. Generate reviewed offline expand DDL "
            "through 0039_audit_append_boundary, then run 0039a online."
        )
    _expand_module()._contract_upgrade()


def downgrade() -> None:
    _expand_module()._contract_downgrade()
