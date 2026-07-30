import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from scripts.migrate_via_fly_proxy import _proxied_url


def test_proxied_url_preserves_credentials_database_and_query():
    result = _proxied_url(
        "postgresql+asyncpg://migration:encoded%2Fsecret@db.internal:5432/lians?ssl=require",
        "127.0.0.1",
        15433,
    )
    assert result == (
        "postgresql+asyncpg://migration:encoded%2Fsecret@127.0.0.1:15433/"
        "lians?ssl=require"
    )


def test_proxied_url_rejects_non_postgres():
    try:
        _proxied_url("sqlite:///tmp.db", "127.0.0.1", 15433)
    except ValueError as exc:
        assert "PostgreSQL" in str(exc)
    else:
        raise AssertionError("non-PostgreSQL migration URLs must be rejected")
