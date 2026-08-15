from __future__ import annotations

import json
import sqlite3
from types import SimpleNamespace

from lians_easy.diagnostics import system_check
from lians_easy.store import MemoryStore


def _integrations(*, configured: int) -> dict[str, SimpleNamespace]:
    labels = ["Cursor", "Codex", "Claude"]
    return {
        label.lower(): SimpleNamespace(
            label=label,
            configured=index < configured,
            detected=True,
            config_path=f"/private/user/{label}/settings.json",
        )
        for index, label in enumerate(labels)
    }


def test_system_check_is_shareable_and_proves_cross_tool_readiness(tmp_path):
    store = MemoryStore(tmp_path / "very-private" / "memory.sqlite3")
    secret_memory = "Secret launch preference that must never enter diagnostics."
    store.remember(secret_memory, source="private chat", source_ref="chat-secret")

    report = system_check(
        store,
        cloud={
            "state": "connected",
            "sync_state": "ready",
            "head_revision": 7,
            "access_token": "must-not-leak",
        },
        integrations=_integrations(configured=2),
        generated_at="2026-08-15T20:00:00+00:00",
    )

    assert report["schema"] == "lians-system-check/v1"
    assert report["overall"] == "ready"
    assert [item["key"] for item in report["checks"]] == [
        "bridge",
        "memory",
        "integrations",
        "cloud",
        "review",
    ]
    assert (
        next(item for item in report["checks"] if item["key"] == "integrations")["connected_count"]
        == 2
    )
    serialized = json.dumps(report)
    for excluded in (
        secret_memory,
        "private chat",
        "chat-secret",
        "must-not-leak",
        str(tmp_path),
        "config_path",
        store.cipher.fingerprint,
    ):
        assert excluded not in serialized
    assert all(value is False for value in report["privacy"].values())


def test_system_check_keeps_local_memory_ready_during_cloud_and_setup_attention(tmp_path):
    store = MemoryStore(tmp_path / "memory.sqlite3")
    report = system_check(
        store,
        cloud={
            "state": "connected",
            "sync_state": "ready",
            "sync_retry": {"active": True, "failures": 3, "retry_after_seconds": 30},
        },
        integrations=_integrations(configured=1),
    )

    assert report["overall"] == "attention"
    memory = next(item for item in report["checks"] if item["key"] == "memory")
    cloud = next(item for item in report["checks"] if item["key"] == "cloud")
    integrations = next(item for item in report["checks"] if item["key"] == "integrations")
    assert memory["status"] == "ready"
    assert memory["existing_memory_checked"] is False
    assert memory["existing_memory_readable"] is None
    assert cloud["status"] == "attention"
    assert "local memory is still working" in cloud["message"].lower()
    assert integrations["status"] == "attention"
    assert "Connect one more AI tool" in integrations["message"]


def test_store_health_detects_an_unreadable_record_without_exposing_it(tmp_path):
    store = MemoryStore(tmp_path / "memory.sqlite3")
    content = "Never expose this corrupted memory value."
    memory = store.remember(content, source="private source")
    with sqlite3.connect(store.path) as database:
        database.execute(
            "UPDATE memories SET content_cipher = ? WHERE id = ?",
            (b"not-valid-ciphertext", memory["id"]),
        )

    health = store.health()

    assert health["status"] == "problem"
    assert health["database_integrity"] is True
    assert health["foreign_key_integrity"] is True
    assert health["encryption_round_trip"] is True
    assert health["record_checked"] is True
    assert health["record_readable"] is False
    serialized = json.dumps(health)
    assert content not in serialized
    assert str(store.path) not in serialized
    assert store.cipher.fingerprint not in serialized
