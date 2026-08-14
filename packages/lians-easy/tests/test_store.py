from __future__ import annotations

import sqlite3

import pytest
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


def test_profiles_are_isolated(tmp_path):
    personal = MemoryStore(tmp_path / "memory.sqlite3", profile="personal")
    work = MemoryStore(tmp_path / "memory.sqlite3", profile="work")
    personal.remember("My favorite color is blue")
    assert work.recall("favorite color") == []


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
