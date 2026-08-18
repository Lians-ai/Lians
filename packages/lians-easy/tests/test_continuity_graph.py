from __future__ import annotations

from lians_easy.continuity import build_continuity_graph
from lians_easy.project import Project
from lians_easy.state_integrity import StateIntegrityService
from lians_easy.store import MemoryStore
from lians_easy.task_contract import TaskContractService


class RelatedEncoder:
    def link(self, items, *, max_edges):  # type: ignore[no-untyped-def]
        assert max_edges > 0
        return [(items[0]["id"], items[1]["id"], 0.87)]


def test_work_graph_preserves_lineage_provenance_and_neural_boundary(tmp_path) -> None:
    store = MemoryStore(tmp_path / "memory.sqlite3")
    project = Project("project-one", "One", str(tmp_path), None)
    first = store.set_current(
        "launch/region",
        "Launch in Canada",
        project_id=project.id,
        source_client="claude",
        event_time="2026-01-01T10:00:00Z",
    )
    second = store.set_current(
        "launch/region",
        "Launch in Canada and the US",
        project_id=project.id,
        source_client="codex",
        event_time="2026-01-02T10:00:00Z",
    )
    store.context_pack(
        "What is the launch region?",
        project=project,
        client="cursor",
    )

    graph = build_continuity_graph(
        store,
        project_id=project.id,
        semantic_linker=RelatedEncoder(),
    )

    node_ids = {node["id"] for node in graph["nodes"]}
    relations = {
        (edge["source"], edge["target"], edge["relation"], edge["method"])
        for edge in graph["edges"]
    }
    assert f"memory:{first['id']}" in node_ids
    assert f"memory:{second['id']}" in node_ids
    assert "agent:claude" in node_ids
    assert "agent:codex" in node_ids
    assert "agent:cursor" in node_ids
    assert (
        f"memory:{second['id']}",
        f"memory:{first['id']}",
        "supersedes",
        "explicit",
    ) in relations
    assert any(relation == "recalled_in" for _, _, relation, _ in relations)
    assert any(method == "neural" for _, _, _, method in relations)
    assert graph["summary"]["neural_edge_count"] == 1
    assert graph["intelligence"]["semantic_links_never_override_verified_state"] is True


def test_work_graph_shows_tasks_criteria_evidence_constraints_and_blockers(tmp_path) -> None:
    store = MemoryStore(tmp_path / "memory.sqlite3")
    tasks = TaskContractService(store)
    tasks.start(
        "Ship the tester",
        ["Launcher passes", "Runtime passes"],
        constraints=["No credentials"],
        task_id="ship-tester",
        project_id="project-one",
    )
    tasks.checkpoint(
        "ship-tester",
        "Launcher passed; runtime is blocked",
        project_id="project-one",
        evidence=[{"criterion_id": "criterion-1", "evidence": "Exit code 0"}],
        constraint_checks=[
            {
                "constraint_id": "constraint-1",
                "status": "passed",
                "evidence": "Secret scan passed",
            }
        ],
        blockers=["Runtime dependency unavailable"],
    )

    graph = build_continuity_graph(store, project_id="project-one")
    types = {node["type"] for node in graph["nodes"]}
    relations = {edge["relation"] for edge in graph["edges"]}

    assert {"task", "criterion", "constraint", "evidence", "blocker"} <= types
    assert {"requires", "bounded_by", "proves", "blocks"} <= relations
    assert graph["summary"]["task_count"] == 1
    assert graph["summary"]["criterion_count"] == 2


def test_work_graph_shows_content_free_observed_agent_activity(tmp_path) -> None:
    store = MemoryStore(tmp_path / "memory.sqlite3")
    store.record_agent_observation(
        client="claude",
        project_id="project-one",
        event="UserPromptSubmit",
    )

    graph = build_continuity_graph(store, project_id="project-one")
    session = next(node for node in graph["nodes"] if node["type"] == "session")

    assert session["label"] == "Claude activity"
    assert session["detail"] == "Observed locally without storing prompt content"
    assert graph["summary"]["session_count"] == 1
    assert any(edge["relation"] == "observed" for edge in graph["edges"])


def test_work_graph_marks_open_state_impacts_and_current_replacement(tmp_path) -> None:
    store = MemoryStore(tmp_path / "memory.sqlite3")
    integrity = StateIntegrityService(store)
    first = store.set_current(
        "launch/region",
        "Canada",
        project_id="project-one",
        event_time="2026-08-01T12:00:00Z",
    )
    integrity.link(first["id"], "launch-copy.md", label="Launch copy")
    current = store.set_current(
        "launch/region",
        "Canada and the US",
        project_id="project-one",
        event_time="2026-08-02T12:00:00Z",
        reason="US launch approved",
    )

    graph = build_continuity_graph(store, project_id="project-one")
    nodes = {node["id"]: node for node in graph["nodes"]}
    relations = {
        (edge["source"], edge["target"], edge["relation"]) for edge in graph["edges"]
    }
    [invalidation] = [
        node for node in graph["nodes"] if node["type"] == "invalidation"
    ]
    [affected] = [node for node in graph["nodes"] if node["type"] == "affected_work"]

    assert graph["summary"]["invalidation_count"] == 1
    assert affected["state"] == "invalidated"
    assert affected["label"] == "Launch copy"
    assert (f"memory:{first['id']}", invalidation["id"], "invalidated") in relations
    assert (
        f"memory:{current['id']}",
        invalidation["id"],
        "current_state_for",
    ) in relations
    assert nodes[invalidation["id"]]["state"] == "open"
