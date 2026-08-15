from __future__ import annotations

import base64
import json
import sqlite3

import pytest
from lians_easy.crypto import LocalCipher
from lians_easy.store import MemoryStore


def test_remember_recall_correct_and_confirmed_forget(tmp_path):
    store = MemoryStore(tmp_path / "memory.sqlite3")
    original = store.remember(
        "The campaign targets independent coffee shops",
        topic="market research",
    )

    assert store.recall("Who does the campaign target?")[0]["id"] == original["id"]
    with pytest.raises(ValueError, match="confirmed=true"):
        store.forget(original["id"])

    corrected = store.correct(original["id"], "The campaign targets regional coffee chains")
    assert store.list(state="current")[0]["id"] == corrected["id"]
    assert store.list(state="superseded")[0]["id"] == original["id"]
    assert store.recall("campaign coffee")[0]["content"] == corrected["content"]

    result = store.forget(corrected["id"], confirmed=True)
    assert result["status"] == "forgotten"
    assert store.list(state="forgotten")[0]["content"] is None
    assert store.recall("campaign coffee") == []


def test_correct_memory_rejects_oversized_content_without_superseding_original(tmp_path):
    store = MemoryStore(tmp_path / "memory.sqlite3")
    original = store.remember("The campaign targets coffee shops")

    with pytest.raises(ValueError, match="20,000 characters or fewer"):
        store.correct(original["id"], "x" * 20_001)

    current = store.list()
    assert [item["id"] for item in current] == [original["id"]]


def test_profiles_are_isolated(tmp_path):
    personal = MemoryStore(tmp_path / "memory.sqlite3", profile="personal")
    work = MemoryStore(tmp_path / "memory.sqlite3", profile="work")
    personal.remember("My favorite color is blue")
    assert work.recall("favorite color") == []


def test_local_cipher_protects_binary_state_and_domain_separates_derived_keys(tmp_path):
    store = MemoryStore(tmp_path / "memory.sqlite3")
    payload = b"workspace-key-material"
    associated_data = b"lians-sync-state-v1"

    ciphertext, nonce = store.cipher.seal_bytes(payload, associated_data=associated_data)

    assert payload not in ciphertext
    assert store.cipher.open_bytes(
        ciphertext,
        nonce,
        associated_data=associated_data,
    ) == payload
    assert store.cipher.derive_key(info=b"lians-sync-exchange-v1") != store.cipher.derive_key(
        info=b"lians-sync-signing-v1"
    )


@pytest.mark.parametrize(
    ("version", "protection"),
    [(2, "owner-file"), (1, "plaintext"), (None, "owner-file")],
)
def test_local_cipher_rejects_unknown_key_descriptors(tmp_path, version, protection):
    key_path = tmp_path / "memory.key"
    key_path.write_text(
        json.dumps(
            {
                "version": version,
                "protection": protection,
                "key": base64.b64encode(b"x" * 32).decode("ascii"),
            }
        ),
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="key file is invalid"):
        LocalCipher(key_path)


def test_every_operation_closes_its_sqlite_connection(tmp_path, monkeypatch):
    real_connect = sqlite3.connect
    connections = []

    class TrackingConnection(sqlite3.Connection):
        closed = False

        def close(self):
            self.closed = True
            super().close()

    def tracking_connect(*args, **kwargs):
        kwargs["factory"] = TrackingConnection
        connection = real_connect(*args, **kwargs)
        connections.append(connection)
        return connection

    monkeypatch.setattr("lians_easy.store.sqlite3.connect", tracking_connect)
    store = MemoryStore(tmp_path / "memory.sqlite3")
    memory = store.remember("The project uses FastAPI", scope="global")
    store.recall("Which framework does the project use?")
    store.stats()
    store.forget(memory["id"], confirmed=True)

    assert connections
    assert all(connection.closed for connection in connections)


def test_confirmed_profile_erasure_clears_history_and_keeps_store_usable(tmp_path):
    data = tmp_path / "memory.sqlite3"
    store = MemoryStore(data)
    original = store.remember("The project uses FastAPI", scope="global")
    store.correct(original["id"], "The project uses Django")
    store.context_pack("Which framework?", project=None, client="codex")

    with pytest.raises(ValueError, match="ERASE ALL LIANS MEMORY"):
        store.erase_profile(confirmed=True, confirmation="erase")

    result = store.erase_profile(
        confirmed=True,
        confirmation="ERASE ALL LIANS MEMORY",
    )

    assert result["status"] == "erased"
    assert result["memory_records_erased"] == 2
    assert result["activity_records_erased"] >= 2
    assert result["receipt_records_erased"] == 1
    assert store.list(state="all") == []
    assert store.activity() == []
    assert store.receipts() == []
    assert store.stats()["current"] == 0
    assert not data.with_name(f"{data.name}-wal").exists()

    replacement = store.remember("Fresh memory after erasure", scope="global")
    assert store.recall("fresh erasure")[0]["id"] == replacement["id"]
