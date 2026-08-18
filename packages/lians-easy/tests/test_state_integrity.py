from __future__ import annotations

import json
from concurrent.futures import ThreadPoolExecutor

import pytest
from lians_easy.mcp import MCPServer
from lians_easy.project import Project
from lians_easy.state_integrity import StateIntegrityService
from lians_easy.store import ConcurrentUpdateError, MemoryStore
from lians_easy.task_contract import TaskContractService


def _call(server, request_id, name, arguments):
    return server.handle(
        {
            "jsonrpc": "2.0",
            "id": request_id,
            "method": "tools/call",
            "params": {"name": name, "arguments": arguments},
        }
    )["result"]["structuredContent"]


def test_state_change_invalidates_transitive_memory_and_artifact_without_overreach(tmp_path):
    store = MemoryStore(tmp_path / "memory.sqlite3")
    integrity = StateIntegrityService(store)
    project = Project("project-one", "One", str(tmp_path), None)
    requirement = store.set_current(
        "product/authentication",
        "Use password authentication.",
        project_id=project.id,
        event_time="2026-08-01T12:00:00Z",
    )
    implementation = store.remember(
        "The login service hashes and verifies passwords.",
        kind="project",
        scope="project",
        project_id=project.id,
    )
    unrelated = store.remember(
        "The product uses the approved blue lotus.",
        kind="project",
        scope="project",
        project_id=project.id,
    )
    integrity.link(
        requirement["id"],
        implementation["id"],
        dependent_type="memory",
        downstream_memory_id=implementation["id"],
        project_id=project.id,
        label="Login implementation assumption",
    )
    integrity.link(
        implementation["id"],
        "src/auth/passwords.py",
        dependent_type="artifact",
        project_id=project.id,
        label="Password authentication module",
    )

    before = store.recall("How does the login service verify passwords?", project_id=project.id)
    assert implementation["id"] in {item["id"] for item in before}

    replacement = store.set_current(
        "product/authentication",
        "Use passwordless passkey authentication.",
        project_id=project.id,
        event_time="2026-08-10T12:00:00Z",
        reason="passwordless launch requirement",
    )

    invalidations = integrity.invalidations(project_id=project.id)
    assert len(invalidations) == 2
    assert {item["dependent_ref"] for item in invalidations} == {
        implementation["id"],
        "src/auth/passwords.py",
    }
    assert {item["root_trigger_memory_id"] for item in invalidations} == {requirement["id"]}
    assert {item["replacement_memory_id"] for item in invalidations} == {replacement["id"]}

    after = store.recall("How does the login service verify passwords?", project_id=project.id)
    recalled_ids = {item["id"] for item in after}
    assert implementation["id"] not in recalled_ids
    assert unrelated["id"] in {
        item["id"] for item in store.recall("approved blue lotus", project_id=project.id)
    }

    pack = store.context_pack(
        "How does the login service verify passwords?",
        project=project,
        client="codex",
    )
    assert pack["receipt"]["excluded"]["invalidated"] >= 1
    assert implementation["content"] not in pack["context"]

    brief = integrity.repair_brief(project_id=project.id, max_tokens=512)
    assert brief["status"] == "repair_required"
    assert brief["impact_count"] == 2
    assert replacement["content"] in brief["context"]
    assert "src/auth/passwords.py" in brief["context"]
    assert unrelated["content"] not in brief["context"]


def test_repair_rebinds_dependency_to_current_state_and_future_changes(tmp_path):
    store = MemoryStore(tmp_path / "memory.sqlite3")
    integrity = StateIntegrityService(store)
    project_id = "project-repair"
    first_requirement = store.set_current(
        "research/sample",
        "Analyze 1,000 posts.",
        project_id=project_id,
        event_time="2026-08-01T12:00:00Z",
    )
    first_analysis = store.remember(
        "The analysis covers a 1,000-post sample.",
        kind="project",
        scope="project",
        project_id=project_id,
        event_time="2026-08-01T13:00:00Z",
    )
    integrity.link(
        first_requirement["id"],
        first_analysis["id"],
        dependent_type="memory",
        downstream_memory_id=first_analysis["id"],
    )
    second_requirement = store.set_current(
        "research/sample",
        "Analyze 10,000 posts.",
        project_id=project_id,
        event_time="2026-08-02T12:00:00Z",
        reason="expanded corpus",
    )
    [invalidation] = integrity.invalidations(project_id=project_id)
    repaired_analysis = store.correct(
        first_analysis["id"],
        "The analysis covers a 10,000-post sample.",
        event_time="2026-08-02T13:00:00Z",
        reason="recomputed for expanded corpus",
    )
    resolved = integrity.resolve(
        invalidation["id"],
        status="repaired",
        evidence="The analysis was recomputed against all 10,000 records.",
        replacement_downstream_memory_id=repaired_analysis["id"],
    )
    assert resolved["status"] == "repaired"
    assert not integrity.invalidations(project_id=project_id)

    third_requirement = store.set_current(
        "research/sample",
        "Analyze 12,000 posts.",
        project_id=project_id,
        event_time="2026-08-03T12:00:00Z",
        reason="late records arrived",
    )
    [new_invalidation] = integrity.invalidations(project_id=project_id)
    assert new_invalidation["root_trigger_memory_id"] == second_requirement["id"]
    assert new_invalidation["replacement_memory_id"] == third_requirement["id"]
    assert new_invalidation["downstream_memory_id"] == repaired_analysis["id"]


def test_dependency_graph_rejects_self_links_cycles_and_cross_project_links(tmp_path):
    store = MemoryStore(tmp_path / "memory.sqlite3")
    integrity = StateIntegrityService(store)
    first = store.remember("First", scope="project", project_id="one")
    second = store.remember("Second", scope="project", project_id="one")
    other = store.remember("Other", scope="project", project_id="two")

    with pytest.raises(ValueError, match="cannot depend on itself"):
        integrity.link(
            first["id"],
            first["id"],
            dependent_type="memory",
            downstream_memory_id=first["id"],
        )
    integrity.link(
        first["id"],
        second["id"],
        dependent_type="memory",
        downstream_memory_id=second["id"],
    )
    with pytest.raises(ValueError, match="cycle"):
        integrity.link(
            second["id"],
            first["id"],
            dependent_type="memory",
            downstream_memory_id=first["id"],
        )
    with pytest.raises(ValueError, match="cross project"):
        integrity.link(
            first["id"],
            other["id"],
            dependent_type="memory",
            downstream_memory_id=other["id"],
        )


def test_state_integrity_sensitive_fields_are_not_plaintext_in_sqlite(tmp_path):
    path = tmp_path / "memory.sqlite3"
    store = MemoryStore(path)
    integrity = StateIntegrityService(store)
    state = store.set_current(
        "private/requirement",
        "Use private launch rules.",
        scope="global",
    )
    integrity.link(
        state["id"],
        "clients/secret-acquisition-plan.md",
        dependent_type="document",
        label="Confidential acquisition plan",
    )
    store.set_current(
        "private/requirement",
        "Use revised private launch rules.",
        scope="global",
        reason="state changed",
    )
    [invalidation] = integrity.invalidations()
    integrity.resolve(
        invalidation["id"],
        status="dismissed",
        evidence="Reviewed privately by the owner.",
    )

    raw = path.read_bytes()
    for secret in (
        b"clients/secret-acquisition-plan.md",
        b"Confidential acquisition plan",
        b"Reviewed privately by the owner",
    ):
        assert secret not in raw


def test_blast_radius_is_bounded_and_idempotent(tmp_path):
    store = MemoryStore(tmp_path / "memory.sqlite3")
    integrity = StateIntegrityService(store)
    root = store.remember("Root")
    child = store.remember("Child")
    first = integrity.link(
        root["id"],
        child["id"],
        dependent_type="memory",
        downstream_memory_id=child["id"],
    )
    confirmed = integrity.link(
        root["id"],
        child["id"],
        dependent_type="memory",
        downstream_memory_id=child["id"],
    )
    integrity.link(child["id"], "report.md", dependent_type="artifact")

    impact = integrity.blast_radius(root["id"], max_depth=20)
    assert confirmed["id"] == first["id"]
    assert impact["impact_count"] == 2
    assert impact["maximum_depth"] == 2
    assert [item["depth"] for item in impact["impacts"]] == [1, 2]


def test_mcp_closes_track_change_repair_loop_across_clients(tmp_path):
    database = tmp_path / "memory.sqlite3"
    claude = MCPServer(MemoryStore(database))
    codex = MCPServer(MemoryStore(database))
    project_root = str(tmp_path)
    requirement = _call(
        claude,
        1,
        "set_current",
        {
            "memory_key": "region",
            "content": "Launch in Canada.",
            "project_root": project_root,
            "event_time": "2026-08-01T12:00:00Z",
        },
    )
    artifact_memory = _call(
        codex,
        2,
        "remember",
        {
            "content": "The launch page says Canada only.",
            "scope": "project",
            "project_root": project_root,
        },
    )
    tracked = _call(
        claude,
        3,
        "track_dependencies",
        {
            "upstream_memory_id": requirement["id"],
            "project_root": project_root,
            "dependents": [
                {
                    "ref": artifact_memory["id"],
                    "type": "memory",
                    "downstream_memory_id": artifact_memory["id"],
                    "label": "Launch page region",
                }
            ],
        },
    )
    impact = _call(
        codex,
        4,
        "state_impact",
        {"memory_id": requirement["id"]},
    )
    replacement = _call(
        codex,
        5,
        "set_current",
        {
            "memory_key": "region",
            "content": "Launch in Canada and the United States.",
            "project_root": project_root,
            "event_time": "2026-08-02T12:00:00Z",
            "reason": "US launch approved",
        },
    )
    brief = _call(
        claude,
        6,
        "state_repair_brief",
        {"project_root": project_root, "root_trigger_memory_id": requirement["id"]},
    )

    assert tracked["count"] == 1
    assert impact["impact_count"] == 1
    assert brief["status"] == "repair_required"
    assert brief["current_state"][0]["id"] == replacement["id"]
    assert brief["affected_work"][0]["downstream_memory_id"] == artifact_memory["id"]


def test_task_checkpoint_artifacts_are_automatically_governed_by_contract(tmp_path):
    store = MemoryStore(tmp_path / "memory.sqlite3")
    tasks = TaskContractService(store)
    integrity = StateIntegrityService(store)
    started = tasks.start(
        "Launch in Canada",
        ["Landing page names the launch region"],
        task_id="launch",
        project_id="project-one",
        event_time="2026-08-01T12:00:00Z",
    )
    tasks.checkpoint(
        "launch",
        "Landing page drafted",
        project_id="project-one",
        artifacts=["src/launch-page.html"],
        event_time="2026-08-01T13:00:00Z",
    )
    contract = dict(started["contract"])
    contract["goal"] = "Launch in Canada and the United States"
    replacement = store.set_current(
        "tasks/launch/contract",
        json.dumps(contract, ensure_ascii=False, sort_keys=True),
        kind="task_contract",
        scope="project",
        project_id="project-one",
        event_time="2026-08-02T12:00:00Z",
        reason="US launch added",
        expected_current_id=started["lineage"]["contract_memory_id"],
    )

    [invalidation] = integrity.invalidations(project_id="project-one")
    assert invalidation["dependent_ref"] == "src/launch-page.html"
    assert invalidation["relation"] == "governs"
    assert invalidation["replacement_memory_id"] == replacement["id"]


def test_forgetting_and_profile_erasure_remove_integrity_lineage(tmp_path):
    store = MemoryStore(tmp_path / "memory.sqlite3")
    integrity = StateIntegrityService(store)
    first = store.set_current("launch/state", "Alpha", scope="global")
    integrity.link(first["id"], "private-plan.md", dependent_type="document")
    current = store.set_current(
        "launch/state",
        "Beta",
        scope="global",
        reason="state changed",
    )
    assert integrity.invalidation_count() == 1

    store.forget(current["id"], confirmed=True)
    assert integrity.invalidation_count() == 0
    with pytest.raises(LookupError):
        integrity.blast_radius(first["id"])

    another = store.set_current("launch/state", "Gamma", scope="global")
    integrity.link(another["id"], "another-private-plan.md", dependent_type="document")
    erased = store.erase_profile(
        confirmed=True,
        confirmation="ERASE ALL LIANS MEMORY",
    )
    assert erased["status"] == "erased"
    assert store.list(state="all") == []
    assert integrity.invalidation_count() == 0


def test_concurrent_state_writers_create_one_current_version_and_one_impact(tmp_path):
    path = tmp_path / "memory.sqlite3"
    initial_store = MemoryStore(path)
    initial = initial_store.set_current(
        "launch/state",
        "Alpha",
        scope="global",
        event_time="2026-08-01T12:00:00Z",
    )
    StateIntegrityService(initial_store).link(initial["id"], "launch.md")

    def update(content):
        store = MemoryStore(path)
        try:
            result = store.set_current(
                "launch/state",
                content,
                scope="global",
                event_time="2026-08-02T12:00:00Z",
                expected_current_id=initial["id"],
                reason="concurrent update",
            )
        except ConcurrentUpdateError:
            return None
        return result["id"]

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(update, ["Beta", "Gamma"]))

    winners = [item for item in results if item is not None]
    integrity = StateIntegrityService(MemoryStore(path))
    [invalidation] = integrity.invalidations()
    assert len(winners) == 1
    assert invalidation["replacement_memory_id"] == winners[0]
    assert integrity.invalidation_count() == 1
