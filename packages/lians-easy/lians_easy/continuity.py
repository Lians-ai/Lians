"""Build a bounded, explainable graph of work carried across AI agents."""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from typing import Any, Protocol

from .state_integrity import StateIntegrityService
from .store import MemoryStore


class SemanticLinker(Protocol):
    """Optional on-device encoder seam; never required for the fast path."""

    def link(
        self,
        items: Sequence[Mapping[str, str]],
        *,
        max_edges: int,
    ) -> Sequence[tuple[str, str, float]]: ...


def build_continuity_graph(
    store: MemoryStore,
    *,
    project_id: str | None = None,
    limit: int = 200,
    semantic_linker: SemanticLinker | None = None,
) -> dict[str, Any]:
    """Return local work state as provenance nodes and explicit relationship edges.

    The base graph uses only stored facts, lineage, provenance, and signed recall
    receipts. A local neural encoder can add scored semantic links, but it cannot
    replace or mutate those verified relationships.
    """

    bounded_limit = max(1, min(int(limit), 500))
    memories = [
        item
        for item in store.list(state="all", limit=bounded_limit)
        if project_id is None or item["scope"] == "global" or item["project_id"] == project_id
    ]
    receipts = [
        receipt
        for receipt in store.receipts(limit=min(100, bounded_limit))
        if project_id is None or receipt.get("project", {}).get("id") == project_id
    ]
    observations = [
        activity
        for activity in store.activity(limit=min(100, bounded_limit))
        if activity.get("event") == "agent_observed"
        and (project_id is None or activity.get("project_id") == project_id)
    ]
    invalidations = StateIntegrityService(store).invalidations(
        status="open",
        project_id=project_id,
        limit=min(500, bounded_limit),
    )

    nodes: dict[str, dict[str, Any]] = {}
    edges: dict[str, dict[str, Any]] = {}
    task_contracts: dict[str, dict[str, Any]] = {}
    task_states: dict[str, dict[str, Any]] = {}

    def add_node(node_id: str, node_type: str, label: str, **details: Any) -> None:
        nodes.setdefault(
            node_id,
            {"id": node_id, "type": node_type, "label": label, **details},
        )

    def add_edge(
        source: str,
        target: str,
        relation: str,
        *,
        method: str = "explicit",
        score: float | None = None,
    ) -> None:
        edge_id = f"{source}|{relation}|{target}"
        edges.setdefault(
            edge_id,
            {
                "id": edge_id,
                "source": source,
                "target": target,
                "relation": relation,
                "method": method,
                "score": score,
            },
        )

    for memory in memories:
        if memory.get("state") == "current" and memory.get("kind") in {
            "task_contract",
            "task_state",
        }:
            try:
                document = json.loads(memory.get("content") or "")
            except (TypeError, json.JSONDecodeError):
                document = None
            if isinstance(document, dict) and document.get("task_id"):
                task_id = str(document["task_id"])
                if memory["kind"] == "task_contract":
                    task_contracts[task_id] = document
                else:
                    task_states[task_id] = document

    for memory in memories:
        memory_id = f"memory:{memory['id']}"
        content = str(memory.get("content") or "")
        label = str(memory.get("memory_key") or memory.get("topic") or memory["kind"])
        node_type = "memory"
        if memory.get("kind") == "control_policy":
            try:
                policy = json.loads(content)
            except (TypeError, json.JSONDecodeError):
                policy = {}
            mode = str(policy.get("mode") or "guide").title()
            node_type = "policy"
            label = f"{mode} control policy"
            content = (
                f"{mode} mode · {policy.get('context_budget_tokens', 512)} token budget · "
                f"{len(policy.get('approval_actions') or [])} approval boundaries"
            )
        elif memory.get("kind") == "verification_policy":
            try:
                verification_policy = json.loads(content)
            except (TypeError, json.JSONDecodeError):
                verification_policy = {}
            node_type = "verification_policy"
            label = "Verification policy"
            content = (
                f"{len(verification_policy.get('allowed_paths') or [])} approved scopes · "
                f"{len(verification_policy.get('required_checks') or [])} required checks · "
                f"{len(verification_policy.get('formal_proofs') or [])} formal proofs"
            )
        elif memory.get("kind") == "verification_receipt":
            try:
                verification_receipt = json.loads(content)
            except (TypeError, json.JSONDecodeError):
                verification_receipt = {}
            verdict = str(verification_receipt.get("verdict") or "unknown")
            blocker_count = len(verification_receipt.get("blockers") or [])
            formal_proofs = verification_receipt.get("formal_proofs") or []
            proved_count = sum(item.get("status") == "proved" for item in formal_proofs)
            node_type = "verification_receipt"
            label = (
                "Proof-backed ship review"
                if verdict == "ready_for_human_ship_review" and formal_proofs
                else "Ready for ship review"
                if verdict == "ready_for_human_ship_review"
                else "Verification blocked"
            )
            content = (
                f"{verdict.replace('_', ' ')} · {blocker_count} blocking gates · "
                f"{proved_count}/{len(formal_proofs)} formal proofs"
            )
        add_node(
            memory_id,
            node_type,
            label,
            detail=content[:240] or "Forgotten memory",
            state=memory["state"],
            kind=memory["kind"],
            memory_key=memory.get("memory_key"),
            memory_id=memory["id"],
            project_id=memory.get("project_id"),
            valid_from=memory.get("valid_from"),
            valid_to=memory.get("valid_to"),
            is_current=memory["superseded_by_id"] is None and memory["forgotten_at"] is None,
        )
        if memory.get("project_id"):
            project_node = f"project:{memory['project_id']}"
            add_node(project_node, "project", "Project", project_id=memory["project_id"])
            add_edge(project_node, memory_id, "contains")
        if memory.get("source_client"):
            client = str(memory["source_client"])
            agent_node = f"agent:{client}"
            add_node(agent_node, "agent", client.title(), client=client)
            add_edge(agent_node, memory_id, "created")
        if memory.get("topic"):
            topic = str(memory["topic"])
            topic_node = f"topic:{topic.casefold()}"
            add_node(topic_node, "topic", topic)
            add_edge(memory_id, topic_node, "about")
        if memory.get("supersedes_id"):
            predecessor = f"memory:{memory['supersedes_id']}"
            add_edge(memory_id, predecessor, "supersedes")

    for task_id, contract in task_contracts.items():
        task_node = f"task:{task_id}"
        state = task_states.get(task_id) or {}
        evidence = state.get("evidence") or {}
        checks = state.get("constraint_checks") or {}
        criteria = list(contract.get("success_criteria") or [])
        constraints = list(contract.get("constraints") or [])
        trusted_evidence = {"measured_local", "measured_ci", "human_confirmed"}
        missing_count = 0
        for item in criteria:
            record = evidence.get(str(item.get("id") or "")) or {}
            if not isinstance(record, dict):
                missing_count += 1
                continue
            if not str(record.get("evidence") or "").strip():
                missing_count += 1
                continue
            if str(record.get("trust_class") or "") not in trusted_evidence:
                missing_count += 1
        failed_count = sum(
            str((checks.get(str(item.get("id") or "")) or {}).get("status") or "unknown")
            == "failed"
            for item in constraints
        )
        unknown_count = sum(
            str((checks.get(str(item.get("id") or "")) or {}).get("status") or "unknown")
            == "unknown"
            for item in constraints
        )
        blockers = list(state.get("blockers") or [])
        task_status = (
            "blocked"
            if blockers
            else "at_risk"
            if failed_count
            else "ready_for_human_review"
            if not missing_count and not unknown_count
            else "active"
        )
        add_node(
            task_node,
            "task",
            str(contract.get("title") or task_id),
            detail=str(contract.get("goal") or "")[:240],
            task_id=task_id,
            current_action=state.get("current_action"),
            blockers=blockers,
            state=task_status,
            satisfied_criteria=len(criteria) - missing_count,
            criterion_count=len(criteria),
        )
        contract_memory = next(
            (
                memory
                for memory in memories
                if memory.get("memory_key") == f"tasks/{task_id}/contract"
                and memory.get("state") == "current"
            ),
            None,
        )
        if contract_memory:
            contract_project_id = contract_memory.get("project_id")
            if contract_project_id:
                project_node = f"project:{contract_project_id}"
                add_node(
                    project_node,
                    "project",
                    "Project",
                    project_id=contract_project_id,
                )
                add_edge(project_node, task_node, "pursues")
            add_edge(task_node, f"memory:{contract_memory['id']}", "governed_by")

        verification_policy_memory = next(
            (
                memory
                for memory in memories
                if memory.get("memory_key") == f"tasks/{task_id}/verification-policy"
                and memory.get("state") == "current"
            ),
            None,
        )
        verification_receipt_memory = next(
            (
                memory
                for memory in memories
                if memory.get("memory_key") == f"tasks/{task_id}/verification-receipt"
                and memory.get("state") == "current"
            ),
            None,
        )
        if verification_policy_memory:
            policy_node = f"memory:{verification_policy_memory['id']}"
            add_edge(task_node, policy_node, "bounded_by")
        if verification_receipt_memory:
            receipt_node = f"memory:{verification_receipt_memory['id']}"
            add_edge(task_node, receipt_node, "verified_by")
            if verification_policy_memory:
                add_edge(
                    f"memory:{verification_policy_memory['id']}",
                    receipt_node,
                    "governs",
                )

        for criterion in contract.get("success_criteria") or []:
            criterion_id = str(criterion.get("id") or "")
            if not criterion_id:
                continue
            node_id = f"criterion:{task_id}:{criterion_id}"
            satisfied = bool(str(evidence.get(criterion_id) or "").strip())
            add_node(
                node_id,
                "criterion",
                str(criterion.get("description") or criterion_id),
                detail=(
                    str(evidence.get(criterion_id))[:240]
                    if satisfied
                    else "Evidence still required"
                ),
                task_id=task_id,
                criterion_id=criterion_id,
                satisfied=satisfied,
                state="satisfied" if satisfied else "missing",
            )
            add_edge(task_node, node_id, "requires")
            if satisfied:
                evidence_node = f"evidence:{task_id}:{criterion_id}"
                add_node(
                    evidence_node,
                    "evidence",
                    f"Evidence for {criterion_id}",
                    detail=str(evidence[criterion_id])[:240],
                    task_id=task_id,
                )
                add_edge(evidence_node, node_id, "proves")

        for constraint in contract.get("constraints") or []:
            constraint_id = str(constraint.get("id") or "")
            if not constraint_id:
                continue
            check = checks.get(constraint_id) or {}
            node_id = f"constraint:{task_id}:{constraint_id}"
            check_status = str(check.get("status") or "unknown")
            add_node(
                node_id,
                "constraint",
                str(constraint.get("description") or constraint_id),
                detail=str(check.get("evidence") or "Not checked")[:240],
                task_id=task_id,
                constraint_id=constraint_id,
                state=check_status,
            )
            add_edge(task_node, node_id, "bounded_by")

        for index, blocker in enumerate(state.get("blockers") or [], start=1):
            blocker_node = f"blocker:{task_id}:{index}"
            add_node(
                blocker_node,
                "blocker",
                str(blocker)[:120],
                detail=str(blocker)[:240],
                task_id=task_id,
            )
            add_edge(blocker_node, task_node, "blocks")

    for receipt in receipts:
        receipt_id = f"receipt:{receipt['id']}"
        client = str(receipt.get("client") or "agent")
        add_node(
            receipt_id,
            "receipt",
            f"{client.title()} context",
            created_at=receipt.get("created_at"),
            memory_count=receipt.get("memory_count", 0),
            token_estimate=receipt.get("token_estimate", 0),
        )
        agent_node = f"agent:{client}"
        add_node(agent_node, "agent", client.title(), client=client)
        add_edge(agent_node, receipt_id, "requested")
        for recalled in receipt.get("memories", []):
            memory_node = f"memory:{recalled.get('id')}"
            if memory_node in nodes:
                add_edge(memory_node, receipt_id, "recalled_in")

    for observation in observations:
        observation_id = f"observation:{observation['id']}"
        client = str(observation.get("client") or "agent")
        add_node(
            observation_id,
            "session",
            f"{client.title()} activity",
            detail="Observed locally without storing prompt content",
            created_at=observation.get("created_at"),
            state="observed",
        )
        agent_node = f"agent:{client}"
        add_node(agent_node, "agent", client.title(), client=client)
        add_edge(agent_node, observation_id, "observed")
        if observation.get("project_id"):
            project_node = f"project:{observation['project_id']}"
            add_node(
                project_node,
                "project",
                "Project",
                project_id=observation["project_id"],
            )
            add_edge(project_node, observation_id, "contains")

    for invalidation in invalidations:
        invalidation_node = f"invalidation:{invalidation['id']}"
        downstream_memory_id = invalidation.get("downstream_memory_id")
        affected_node = (
            f"memory:{downstream_memory_id}"
            if downstream_memory_id
            else f"impact:{invalidation['dependency_id']}"
        )
        if affected_node not in nodes:
            add_node(
                affected_node,
                "affected_work",
                str(invalidation.get("label") or invalidation.get("dependent_type") or "Work"),
                detail=str(invalidation.get("dependent_ref") or "")[:240],
                state="invalidated",
                dependent_type=invalidation.get("dependent_type"),
            )
        elif affected_node in nodes:
            nodes[affected_node]["state"] = "invalidated"
        add_node(
            invalidation_node,
            "invalidation",
            "State change requires review",
            detail=str(invalidation.get("reason") or "Current state changed")[:240],
            state="open",
            created_at=invalidation.get("created_at"),
            dependent_type=invalidation.get("dependent_type"),
            invalidation_id=invalidation["id"],
            project_id=invalidation.get("project_id"),
            root_trigger_memory_id=invalidation.get("root_trigger_memory_id"),
        )
        trigger_node = f"memory:{invalidation['root_trigger_memory_id']}"
        replacement_node = f"memory:{invalidation['replacement_memory_id']}"
        if trigger_node in nodes:
            add_edge(trigger_node, invalidation_node, "invalidated")
        if replacement_node in nodes:
            add_edge(replacement_node, invalidation_node, "current_state_for")
        add_edge(invalidation_node, affected_node, "requires_review")

    policy_nodes = [node["id"] for node in nodes.values() if node["type"] == "policy"]
    agent_nodes = [node["id"] for node in nodes.values() if node["type"] == "agent"]
    for policy_node in policy_nodes:
        for agent_node in agent_nodes:
            add_edge(policy_node, agent_node, "governs")

    neural_edge_count = 0
    if semantic_linker is not None:
        semantic_items = [
            {"id": node["id"], "text": f"{node['label']} {node.get('detail', '')}"}
            for node in nodes.values()
            if node["type"] == "memory" and node.get("detail")
        ]
        for source, target, raw_score in semantic_linker.link(
            semantic_items,
            max_edges=min(100, bounded_limit),
        ):
            if source not in nodes or target not in nodes or source == target:
                continue
            score = max(0.0, min(float(raw_score), 1.0))
            add_edge(source, target, "semantically_related", method="neural", score=score)
            neural_edge_count += 1

    return {
        "schema": "https://lians.ai/schemas/continuity-graph/v0.1",
        "nodes": list(nodes.values()),
        "edges": list(edges.values()),
        "summary": {
            "node_count": len(nodes),
            "edge_count": len(edges),
            "memory_count": sum(node["type"] == "memory" for node in nodes.values()),
            "agent_count": sum(node["type"] == "agent" for node in nodes.values()),
            "receipt_count": sum(node["type"] == "receipt" for node in nodes.values()),
            "task_count": sum(node["type"] == "task" for node in nodes.values()),
            "criterion_count": sum(node["type"] == "criterion" for node in nodes.values()),
            "session_count": sum(node["type"] == "session" for node in nodes.values()),
            "policy_count": sum(node["type"] == "policy" for node in nodes.values()),
            "verification_policy_count": sum(
                node["type"] == "verification_policy" for node in nodes.values()
            ),
            "verification_receipt_count": sum(
                node["type"] == "verification_receipt" for node in nodes.values()
            ),
            "invalidation_count": sum(
                node["type"] == "invalidation" for node in nodes.values()
            ),
            "neural_edge_count": neural_edge_count,
        },
        "intelligence": {
            "verified_graph": "explicit local state, provenance, lineage, and recall receipts",
            "semantic_linking": "on-device encoder" if semantic_linker else "not enabled",
            "semantic_links_never_override_verified_state": True,
        },
    }
