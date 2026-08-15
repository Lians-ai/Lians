from __future__ import annotations

import json
import sqlite3

import pytest
from lians_easy.portability import export_backup, import_backup, verify_backup
from lians_easy.store import MemoryStore

PASSPHRASE = "correct horse battery staple"


def _populated_store(tmp_path, name: str = "source") -> MemoryStore:
    store = MemoryStore(tmp_path / name / "memory.sqlite3")
    preference = store.remember(
        "Never use em dashes in my writing.",
        source="portable backup test",
        kind="preference",
        scope="global",
        source_client="cursor",
    )
    corrected = store.correct(preference["id"], "Never use em dashes or semicolons in my writing.")
    store.remember(
        "This project uses FastAPI.",
        source="portable backup test",
        kind="project",
        scope="project",
        project_id="project-fastapi",
        source_client="codex",
    )
    temporary = store.remember(
        "Temporary preference to erase.",
        source="portable backup test",
        kind="preference",
        scope="global",
        source_client="claude",
    )
    store.forget(temporary["id"], confirmed=True)
    store.context_pack(
        "What are my writing preferences?",
        project=None,
        client="gemini",
        limit=3,
        max_tokens=256,
    )
    assert corrected["content"] == "Never use em dashes or semicolons in my writing."
    return store


def _snapshot(store: MemoryStore) -> dict:
    return {
        "memories": store.list(state="all", limit=200),
        "activity": store.activity(limit=500),
        "receipts": store.receipts(limit=200),
    }


def test_portable_backup_is_encrypted_verified_reencrypted_and_idempotent(tmp_path) -> None:
    source = _populated_store(tmp_path)
    destination = tmp_path / "Lians Memory.liansbackup"
    report = export_backup(source, destination, PASSPHRASE)

    encoded = destination.read_bytes()
    assert report["encrypted"] is True
    assert report["memories"] == 4
    assert b"FastAPI" not in encoded
    assert b"em dashes" not in encoded
    assert b"portable backup test" not in encoded
    document = json.loads(encoded)
    assert set(document) == {"format", "version", "kdf", "cipher", "ciphertext"}
    assert document["kdf"]["name"] == "scrypt"
    assert document["cipher"]["name"] == "AES-256-GCM"

    verified = verify_backup(destination, PASSPHRASE)
    assert verified["status"] == "verified"
    assert verified["memories"] == 4
    assert verified["receipts"] == 1

    target = MemoryStore(tmp_path / "target" / "memory.sqlite3")
    assert target.cipher.fingerprint != source.cipher.fingerprint
    imported = import_backup(target, destination, PASSPHRASE)
    assert imported["re_encrypted_for_this_device"] is True
    assert imported["imported"] == {"memories": 4, "activity": 6, "receipts": 1}
    assert _snapshot(target) == _snapshot(source)

    with sqlite3.connect(source.path) as source_database, sqlite3.connect(
        target.path
    ) as target_database:
        source_ciphertext = source_database.execute(
            "SELECT content_cipher FROM memories WHERE content_cipher IS NOT NULL ORDER BY id LIMIT 1"
        ).fetchone()[0]
        target_ciphertext = target_database.execute(
            "SELECT content_cipher FROM memories WHERE content_cipher IS NOT NULL ORDER BY id LIMIT 1"
        ).fetchone()[0]
    assert source_ciphertext != target_ciphertext

    repeated = import_backup(target, destination, PASSPHRASE)
    assert repeated["imported"] == {"memories": 0, "activity": 0, "receipts": 0}
    assert repeated["already_present"] == {"memories": 4, "activity": 6, "receipts": 1}


def test_backup_rejects_wrong_passphrase_tampering_and_unsafe_output(tmp_path) -> None:
    source = _populated_store(tmp_path)
    destination = tmp_path / "memory.liansbackup"

    with pytest.raises(ValueError, match="at least 12"):
        export_backup(source, destination, "too short")
    with pytest.raises(ValueError, match=r"\.liansbackup"):
        export_backup(source, tmp_path / "memory.json", PASSPHRASE)

    export_backup(source, destination, PASSPHRASE)
    with pytest.raises(FileExistsError, match="already exists"):
        export_backup(source, destination, PASSPHRASE)
    with pytest.raises(ValueError, match="incorrect or the backup was changed"):
        verify_backup(destination, "this is the wrong passphrase")

    document = json.loads(destination.read_bytes())
    replacement = "A" if document["ciphertext"][0] != "A" else "B"
    document["ciphertext"] = replacement + document["ciphertext"][1:]
    destination.write_text(json.dumps(document), encoding="utf-8")
    with pytest.raises(ValueError, match="incorrect or the backup was changed"):
        verify_backup(destination, PASSPHRASE)


def test_import_conflict_rolls_back_every_new_record(tmp_path) -> None:
    source = _populated_store(tmp_path)
    first_backup = tmp_path / "first.liansbackup"
    export_backup(source, first_backup, PASSPHRASE)
    target = MemoryStore(tmp_path / "target" / "memory.sqlite3")
    import_backup(target, first_backup, PASSPHRASE)

    new_memory = source.remember(
        "New memory that must not partially import.",
        source="portable backup test",
        kind="project",
        scope="project",
        project_id="project-fastapi",
    )
    second_backup = tmp_path / "second.liansbackup"
    export_backup(source, second_backup, PASSPHRASE)

    conflict_id = target.list(state="current", limit=1)[0]["id"]
    with sqlite3.connect(target.path) as database:
        database.execute(
            "UPDATE memories SET source = ? WHERE id = ?",
            ("conflicting target source", conflict_id),
        )
        database.commit()

    with pytest.raises(ValueError, match="Import conflict for memory ID"):
        import_backup(target, second_backup, PASSPHRASE)
    assert all(
        memory["id"] != new_memory["id"] for memory in target.list(state="all", limit=200)
    )


def test_export_refuses_a_tampered_signed_receipt(tmp_path) -> None:
    source = _populated_store(tmp_path)
    with sqlite3.connect(source.path) as database:
        row = database.execute("SELECT id, receipt_json FROM context_receipts LIMIT 1").fetchone()
        receipt = json.loads(row[1])
        receipt["memory_count"] += 1
        database.execute(
            "UPDATE context_receipts SET receipt_json = ? WHERE id = ?",
            (json.dumps(receipt), row[0]),
        )
        database.commit()

    with pytest.raises(ValueError, match="receipt index does not match"):
        export_backup(source, tmp_path / "tampered.liansbackup", PASSPHRASE)
