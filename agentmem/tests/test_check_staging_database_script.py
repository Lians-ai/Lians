from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

SCRIPT_PATH = Path(__file__).parents[2] / "scripts" / "check_staging_database.py"
SPEC = importlib.util.spec_from_file_location("check_staging_database", SCRIPT_PATH)
assert SPEC is not None
assert SPEC.loader is not None
MODULE = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = MODULE
SPEC.loader.exec_module(MODULE)


def test_parse_connection_settings_for_fly_proxy() -> None:
    settings = MODULE.parse_connection_settings(
        "postgresql+asyncpg://lians:p%40ss%3Aword@private.flycast:5432/lians"
        "?sslmode=disable",
        host_override="127.0.0.1",
        port_override=15432,
    )

    assert settings.host == "127.0.0.1"
    assert settings.port == 15432
    assert settings.user == "lians"
    assert settings.password == "p@ss:word"
    assert settings.database == "lians"
    assert settings.ssl is False
    assert "p@ss:word" not in repr(settings)


@pytest.mark.parametrize(
    "database_url",
    [
        "https://lians:secret@example.com/lians",
        "postgresql://lians@example.com/lians",
        "postgresql://lians:secret@example.com",
    ],
)
def test_parse_connection_settings_rejects_invalid_urls(database_url: str) -> None:
    with pytest.raises(ValueError):
        MODULE.parse_connection_settings(database_url)


def test_expected_revision_defaults_to_current_alembic_head(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(sys, "argv", [str(SCRIPT_PATH)])

    assert MODULE.parse_args().expected_revision == "0033_sync_device_key_rotation"
