"""Focused contracts for the persistent master-key database write fence.

The PostgreSQL cases are opt-in through ``TEST_DATABASE_URL`` because trigger
and table-lock behavior cannot be represented faithfully by SQLite.
"""
from __future__ import annotations

import os
import uuid
from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path

import pytest
import pytest_asyncio
import lians.key_rotation as rotation
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from lians.kms import MasterKeyring, MasterKeyVersion


def _ring() -> MasterKeyring:
    return MasterKeyring(
        current=MasterKeyVersion("mk-new", b"n" * 32),
        previous=MasterKeyVersion("mk-old", b"o" * 32),
        provider="env",
    )


def _subject_wrapper(key_id: str) -> bytes:
    encoded = key_id.encode("ascii")
    return b"lians-dek:v2\x00" + bytes((len(encoded),)) + encoded + (b"x" * 60)


def _sealed(key_id: str) -> str:
    # Forty base64url characters is the minimum canonical nonce/tag payload
    # accepted at the database structural boundary.
    return f"lians-sealed:v2:{key_id}:" + ("A" * 40)


def test_fence_registry_covers_every_master_derived_storage_target() -> None:
    migration_path = (
        Path(__file__).parents[1]
        / "alembic"
        / "versions"
        / "0037_master_key_write_fence.py"
    )
    spec = spec_from_file_location("migration_0037_master_key_write_fence", migration_path)
    assert spec is not None and spec.loader is not None
    migration = module_from_spec(spec)
    spec.loader.exec_module(migration)
    base_fences = set(migration.FENCE_TRIGGERS)
    runtime_fences = set(rotation.FENCE_TRIGGERS)
    assert base_fences < runtime_fences
    assert runtime_fences - base_fences == {
        (
            "subject_erasure_jobs",
            "trg_subject_erasure_jobs_master_key_fence",
            "lians_master_key_fence_sealed",
            ("subject_locator_encrypted", "nullable"),
        )
    }

    trigger_tables = {table for table, _, _, _ in rotation.FENCE_TRIGGERS}
    assert trigger_tables == set(rotation.MASTER_KEY_VALUE_TABLES)
    assert len(trigger_tables) == len(rotation.FENCE_TRIGGERS) == 9

    sealed_trigger_fields = {
        (table, arguments[0], arguments[1])
        for table, _, function, arguments in rotation.FENCE_TRIGGERS
        if function == "lians_master_key_fence_sealed"
    }
    expected_sealed_fields = {
        (
            spec.model.__tablename__,
            spec.attribute,
            "nullable"
            if spec.name.startswith(
                (
                    "subject_erasure_jobs.",
                    "gate_approval_attestations.",
                    "decision_review_events.",
                )
            )
            else "required",
        )
        for spec in rotation.SEALED_FIELDS
    }
    assert sealed_trigger_fields == expected_sealed_fields
    assert (
        "control_closure_attestations",
        "trg_control_closure_attestations_master_key_fence",
        "lians_master_key_fence_closure",
        (),
    ) in rotation.FENCE_TRIGGERS
    assert (
        "subject_keys",
        "trg_subject_keys_master_key_fence",
        "lians_master_key_fence_subject",
        (),
    ) in rotation.FENCE_TRIGGERS


def test_fence_pair_matching_is_role_sensitive(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(rotation, "get_master_keyring", _ring)
    prepared = rotation.WriteFenceStatus(
        phase="prepared",
        current_key_id="mk-new",
        previous_key_id="mk-old",
        generation=4,
    )
    reversed_pair = rotation.WriteFenceStatus(
        phase="prepared",
        current_key_id="mk-old",
        previous_key_id="mk-new",
        generation=4,
    )
    narrowed = rotation.WriteFenceStatus(
        phase="narrowed",
        current_key_id="mk-new",
        generation=4,
    )

    assert rotation._fence_matches_configured_pair(prepared, phase="prepared")
    assert not rotation._fence_matches_configured_pair(
        reversed_pair, phase="prepared"
    )
    assert rotation._fence_matches_configured_pair(narrowed, phase="narrowed")
    assert narrowed.as_dict() == {
        "active": True,
        "phase": "narrowed",
        "current_key_id": "mk-new",
        "previous_key_id": None,
        "generation": 4,
    }


TEST_DB_URL = os.environ.get("TEST_DATABASE_URL", "")
PG_AVAILABLE = bool(TEST_DB_URL and "postgresql" in TEST_DB_URL)
requires_postgres = pytest.mark.skipif(
    not PG_AVAILABLE,
    reason="TEST_DATABASE_URL is not a PostgreSQL URL",
)


@pytest_asyncio.fixture
async def fence_db():
    engine = create_async_engine(TEST_DB_URL, pool_pre_ping=True)
    async with engine.connect() as connection:
        transaction = await connection.begin()
        session = AsyncSession(bind=connection, expire_on_commit=False)
        await session.execute(
            text("SELECT set_config('app.current_namespace', '__admin__', true)")
        )
        await session.execute(
            text("SELECT set_config('agentmem.barrier_group', '', true)")
        )
        await session.execute(text("DELETE FROM master_key_write_fence_state"))
        try:
            yield session
        finally:
            await session.close()
            await transaction.rollback()
    await engine.dispose()


async def _expect_rejected(
    session: AsyncSession, statement: str, parameters: dict[str, object]
) -> None:
    with pytest.raises(DBAPIError):
        async with session.begin_nested():
            await session.execute(text(statement), parameters)


@requires_postgres
async def test_prepared_and_narrowed_fence_reject_unbounded_sealed_ids(
    fence_db: AsyncSession,
) -> None:
    await fence_db.execute(
        text(
            "INSERT INTO master_key_write_fence_state ("
            "singleton_id, phase, current_key_id, previous_key_id, generation, "
            "prepared_at, narrowed_at) VALUES ("
            "1, 'prepared', 'mk-new', 'mk-old', 1, now(), NULL)"
        )
    )
    schema = str(
        (await fence_db.execute(text("SELECT current_schema()"))).scalar_one()
    )
    assert schema.replace("_", "").isalnum()
    function = f'{schema}.lians_master_key_fence_check_sealed'

    for key_id in ("mk-new", "mk-old"):
        allowed = (
            await fence_db.execute(
                text(f"SELECT {function}(:value, false)"),
                {"value": _sealed(key_id)},
            )
        ).scalar_one()
        assert allowed is True
    for value in (_sealed("mk-unknown"), "lians-sealed:v1:AAAA", "plaintext"):
        allowed = (
            await fence_db.execute(
                text(f"SELECT {function}(:value, false)"),
                {"value": value},
            )
        ).scalar_one()
        assert allowed is False
    nullable = (
        await fence_db.execute(
            text(f"SELECT {function}(NULL, true)")
        )
    ).scalar_one()
    required = (
        await fence_db.execute(
            text(f"SELECT {function}(NULL, false)")
        )
    ).scalar_one()
    assert nullable is True
    assert required is False

    await fence_db.execute(
        text(
            "UPDATE master_key_write_fence_state SET phase = 'narrowed', "
            "previous_key_id = NULL, narrowed_at = now() WHERE singleton_id = 1"
        )
    )
    old_allowed = (
        await fence_db.execute(
            text(f"SELECT {function}(:value, false)"),
            {"value": _sealed("mk-old")},
        )
    ).scalar_one()
    new_allowed = (
        await fence_db.execute(
            text(f"SELECT {function}(:value, false)"),
            {"value": _sealed("mk-new")},
        )
    ).scalar_one()
    assert old_allowed is False
    assert new_allowed is True


@requires_postgres
async def test_subject_fence_checks_v2_header_and_preserves_erasure(
    fence_db: AsyncSession,
) -> None:
    await fence_db.execute(
        text(
            "INSERT INTO master_key_write_fence_state ("
            "singleton_id, phase, current_key_id, previous_key_id, generation, "
            "prepared_at, narrowed_at) VALUES ("
            "1, 'prepared', 'mk-new', 'mk-old', 1, now(), NULL)"
        )
    )
    namespace = f"fence-{uuid.uuid4().hex}"
    for suffix, key_id in (("new", "mk-new"), ("old", "mk-old")):
        await fence_db.execute(
            text(
                "INSERT INTO subject_keys (namespace, subject_id, enc_key) "
                "VALUES (:namespace, :subject_id, :enc_key)"
            ),
            {
                "namespace": namespace,
                "subject_id": suffix,
                "enc_key": _subject_wrapper(key_id),
            },
        )
    await _expect_rejected(
        fence_db,
        "INSERT INTO subject_keys (namespace, subject_id, enc_key) "
        "VALUES (:namespace, 'unknown', :enc_key)",
        {"namespace": namespace, "enc_key": _subject_wrapper("mk-unknown")},
    )
    await _expect_rejected(
        fence_db,
        "INSERT INTO subject_keys (namespace, subject_id, enc_key) "
        "VALUES (:namespace, 'legacy', :enc_key)",
        {"namespace": namespace, "enc_key": b"x" * 60},
    )
    await _expect_rejected(
        fence_db,
        "INSERT INTO subject_keys (namespace, subject_id, enc_key) "
        "VALUES (:namespace, 'live-null', NULL)",
        {"namespace": namespace},
    )
    await fence_db.execute(
        text(
            "INSERT INTO subject_keys (namespace, subject_id, enc_key, destroyed_at) "
            "VALUES (:namespace, 'destroyed-null', NULL, now()), "
            "(:namespace, 'destroyed-zero', :zeroed, now())"
        ),
        {"namespace": namespace, "zeroed": b"\x00" * 81},
    )
    await _expect_rejected(
        fence_db,
        "INSERT INTO subject_keys (namespace, subject_id, enc_key, destroyed_at) "
        "VALUES (:namespace, 'destroyed-nonzero', :enc_key, now())",
        {"namespace": namespace, "enc_key": _subject_wrapper("mk-new")},
    )


@requires_postgres
async def test_closure_trigger_rejects_plaintext_without_touching_immutable_trigger(
    fence_db: AsyncSession,
) -> None:
    await fence_db.execute(
        text(
            "INSERT INTO master_key_write_fence_state ("
            "singleton_id, phase, current_key_id, previous_key_id, generation, "
            "prepared_at, narrowed_at) VALUES ("
            "1, 'prepared', 'mk-new', 'mk-old', 1, now(), NULL)"
        )
    )
    connection = await fence_db.connection()
    schema = str(
        (await fence_db.execute(text("SELECT current_schema()"))).scalar_one()
    )
    assert schema.replace("_", "").isalnum()
    await connection.exec_driver_sql(
        "CREATE TEMP TABLE _lians_fence_closure ("
        "statement text, statement_encrypted text)"
    )
    await connection.exec_driver_sql(
        "CREATE TRIGGER trg_test_closure_fence BEFORE INSERT OR UPDATE "
        "ON _lians_fence_closure FOR EACH ROW EXECUTE FUNCTION "
        f"{schema}.lians_master_key_fence_closure()"
    )
    await _expect_rejected(
        fence_db,
        "INSERT INTO _lians_fence_closure (statement, statement_encrypted) "
        "VALUES ('legacy plaintext', NULL)",
        {},
    )
    await fence_db.execute(
        text(
            "INSERT INTO _lians_fence_closure (statement, statement_encrypted) "
            "VALUES (NULL, :value)"
        ),
        {"value": _sealed("mk-new")},
    )
    immutable_trigger = (
        await fence_db.execute(
            text(
                "SELECT t.tgenabled::text FROM pg_trigger t "
                "JOIN pg_class c ON c.oid = t.tgrelid "
                "JOIN pg_namespace n ON n.oid = c.relnamespace "
                "WHERE n.nspname = current_schema() "
                "AND c.relname = 'control_closure_attestations' "
                "AND t.tgname = 'trg_control_closure_attestations_append_only'"
            )
        )
    ).scalar_one()
    assert immutable_trigger == "O"


@requires_postgres
async def test_operator_validates_exact_fence_artifacts(
    fence_db: AsyncSession,
) -> None:
    assert (
        await rotation._validate_schema_and_privileges(fence_db)
        == rotation.EXPECTED_ROTATION_REVISION
    )
