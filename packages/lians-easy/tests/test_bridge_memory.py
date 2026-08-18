from __future__ import annotations

import base64
import json
import threading
from concurrent.futures import ThreadPoolExecutor

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
from lians_easy.crypto import LocalCipher
from lians_easy.project import Project, detect_project
from lians_easy.store import MemoryStore


def _canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()


def test_project_identity_follows_repository_origin_across_nested_paths(tmp_path, monkeypatch):
    root = tmp_path / "checkout-one"
    nested = root / "src" / "api"
    nested.mkdir(parents=True)
    (root / ".git").mkdir()
    (root / ".git" / "config").write_text(
        '[remote "origin"]\n\turl = git@github.com:Example/FastAPI-App.git\n',
        encoding="utf-8",
    )

    monkeypatch.chdir(nested)
    project = detect_project(nested)

    assert project.name == "fastapi-app"
    assert project.origin == "github.com/example/fastapi-app"
    assert project.root == str(root)
    assert project.id.startswith("project-")


def test_simultaneous_clients_publish_one_complete_root_key(tmp_path):
    key_path = tmp_path / "bridge.key"
    clients = 8
    barrier = threading.Barrier(clients)

    def initialize(_index):
        barrier.wait()
        return LocalCipher(key_path).fingerprint

    with ThreadPoolExecutor(max_workers=clients) as pool:
        fingerprints = list(pool.map(initialize, range(clients)))

    assert len(set(fingerprints)) == 1
    assert key_path.is_file()
    assert not list(tmp_path.glob(".bridge.key.*.tmp"))


def test_encrypted_cross_tool_context_receipt_and_immediate_correction(tmp_path):
    database = tmp_path / "bridge.sqlite3"
    project = Project(
        id="project-fastapi",
        name="FastAPI app",
        root=str(tmp_path),
        origin="github.com/example/fastapi-app",
    )
    cursor = MemoryStore(database)
    global_preference = cursor.remember(
        "Do not use em dashes in anything written for me.",
        kind="preference",
        scope="global",
        source="explicit user instruction",
        source_client="cursor",
        source_ref="cursor-chat-1",
    )
    stack = cursor.remember(
        "We use FastAPI and never write migrations manually.",
        kind="preference",
        scope="project",
        project_id=project.id,
        source="explicit user instruction",
        source_client="cursor",
        source_ref="cursor-chat-1",
    )
    cursor.remember(
        "The previous task completed the user model; continue with the API routes.",
        kind="handoff",
        scope="project",
        project_id=project.id,
        source="task handoff",
        source_client="cursor",
        source_ref="cursor-chat-1",
    )
    other = cursor.remember(
        "The unrelated project uses Django.",
        kind="project",
        scope="project",
        project_id="project-other",
        source="other project",
    )
    paused = cursor.remember(
        "Use the retired v1 endpoint.",
        kind="project",
        scope="project",
        project_id=project.id,
        source="old decision",
    )
    cursor.pause(paused["id"])

    raw_database = database.read_bytes()
    assert b"FastAPI" not in raw_database
    assert b"migrations manually" not in raw_database

    codex = MemoryStore(database)
    pack = codex.context_pack(
        "Continue the API work and create the next migration",
        project=project,
        client="codex",
        limit=3,
        max_tokens=512,
    )

    assert pack["receipt_line"].startswith("3 memories used · Lians FastAPI app · ")
    assert "FastAPI" in pack["context"]
    assert global_preference["content"] in pack["context"]
    assert other["content"] not in pack["context"]
    assert paused["content"] not in pack["context"]
    assert pack["receipt"]["token_estimate"] <= 512
    assert pack["receipt"]["excluded"]["scope"] == 1
    assert pack["receipt"]["excluded"]["paused"] == 1
    assert all(item["reason"] for item in pack["receipt"]["memories"])
    efficiency = pack["receipt"]["efficiency"]
    assert efficiency["available_memory_count"] == 3
    assert efficiency["selected_memory_count"] == 3
    assert efficiency["repeated_memory_tokens_avoided_estimate"] == 0
    assert efficiency["basis"] == "active in-scope memory content compared with full replay"

    signature = pack["receipt"]["signature"]
    protected = {key: value for key, value in pack["receipt"].items() if key != "signature"}
    Ed25519PublicKey.from_public_bytes(base64.b64decode(signature["public_key"])).verify(
        base64.b64decode(signature["value"]), _canonical(protected)
    )

    corrected = cursor.correct(
        stack["id"], "We use FastAPI with Alembic-generated migrations only."
    )
    corrected_pack = codex.context_pack(
        "Continue the FastAPI migration",
        project=project,
        client="claude",
        limit=3,
    )
    assert corrected["content"] in corrected_pack["context"]
    assert stack["content"] not in corrected_pack["context"]

    totals = codex.stats()["efficiency"]
    assert totals["context_events"] == 2
    assert totals["memories_reused"] == 6
    assert totals["clients_used"] == 2

    erased = cursor.forget(corrected["id"], confirmed=True)
    forgotten_pack = codex.context_pack(
        "Continue the FastAPI migration",
        project=project,
        client="codex",
        limit=3,
    )
    assert corrected["content"] not in forgotten_pack["context"]
    assert erased["erased_versions"] == 2
    lineage = [
        item for item in codex.list(state="all") if item["id"] in {stack["id"], corrected["id"]}
    ]
    assert len(lineage) == 2
    assert all(item["content"] is None and item["state"] == "forgotten" for item in lineage)
    assert {item["event"] for item in codex.activity()} >= {
        "context_used",
        "corrected",
        "forgotten",
    }


def test_credentials_are_rejected_before_encryption(tmp_path):
    store = MemoryStore(tmp_path / "bridge.sqlite3")

    with pytest.raises(ValueError, match="excluded and not stored"):
        store.remember("API_KEY=" + "sk-" + ("example-secret-value-" * 2))

    assert store.list() == []


def test_scope_change_is_versioned_and_applies_to_other_projects(tmp_path):
    store = MemoryStore(tmp_path / "bridge.sqlite3")
    first = Project("project-one", "One", str(tmp_path / "one"), None)
    second = Project("project-two", "Two", str(tmp_path / "two"), None)
    original = store.remember(
        "Never use em dashes.",
        kind="preference",
        scope="project",
        project_id=first.id,
    )

    changed = store.rescope(original["id"], scope="global")

    assert changed["scope"] == "global"
    assert changed["supersedes_id"] == original["id"]
    assert (
        changed["content"]
        in store.context_pack("Draft an update", project=second, client="claude")["context"]
    )
    [superseded] = store.list(state="superseded")
    assert superseded["id"] == original["id"]
    assert any(item["event"] == "scope_changed" for item in store.activity())

    result = store.forget(changed["id"], confirmed=True)
    assert result["erased_versions"] == 2
    assert all(item["content"] is None for item in store.list(state="all"))
