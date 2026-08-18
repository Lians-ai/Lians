from __future__ import annotations

from lians_easy.bridge import context_for_event
from lians_easy.memory_health import MemoryHealthService
from lians_easy.store import MemoryStore
from lians_easy.understanding import UnderstandingService


def test_understanding_asks_one_blocking_question_for_a_vague_request() -> None:
    brief = UnderstandingService.analyze("Fix this", memories=[], max_questions=3)

    assert brief["readiness"] == "needs_clarification"
    assert brief["questions"][0]["priority"] == "blocking"
    assert "finished result" in brief["questions"][0]["question"]
    assert brief["privacy"] == {
        "request_persisted": False,
        "external_model_called": False,
        "memory_items_considered": 0,
    }


def test_understanding_uses_memory_and_does_not_block_an_actionable_build() -> None:
    brief = UnderstandingService.analyze(
        "Build the Windows desktop app and make all tests pass before shipping",
        memories=[
            {
                "id": "preference-1",
                "kind": "preference",
                "content": "Keep the interface simple for college students.",
            }
        ],
    )

    assert brief["intent"] == "build"
    assert brief["readiness"] == "ready"
    assert brief["known_context"][0]["layer"] == "identity"
    assert brief["privacy"]["memory_items_considered"] == 1


def test_bridge_only_injects_a_question_when_the_request_is_blocked(tmp_path) -> None:
    store = MemoryStore(tmp_path / "memory.sqlite3")

    vague = context_for_event(
        {"prompt": "Fix this", "cwd": str(tmp_path)},
        client="codex",
        store=store,
    )
    clear = context_for_event(
        {"prompt": "Review the Python tests and explain the failing assertion", "cwd": str(tmp_path)},
        client="codex",
        store=store,
    )

    assert vague["understanding"]["needs_clarification"] is True
    assert "Ask one question before acting" in vague["context"]
    assert clear["understanding"]["needs_clarification"] is False
    assert "Ask one question before acting" not in clear["context"]


def test_memory_health_finds_scope_duplicates_and_unversioned_decisions(tmp_path) -> None:
    store = MemoryStore(tmp_path / "memory.sqlite3")
    store.remember("The release uses Python 3.12", scope="global")
    store.remember("The release uses Python 3.12", scope="global")
    store.remember("Ship on Friday", kind="decision", scope="global")
    store.remember("Use the project handoff", kind="handoff", scope="global")

    report = MemoryHealthService(store).inspect()

    issue_types = {issue["type"] for issue in report["issues"]}
    assert {"duplicate", "unversioned_decision", "broad_scope"} <= issue_types
    assert report["mutated"] is False
    assert report["score"] < 100
    assert report["hierarchy"]["working"] == 1
