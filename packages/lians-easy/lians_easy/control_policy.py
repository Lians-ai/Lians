"""Encrypted user control policy for every connected agent.

The policy is deliberately small. It controls how much Lians may intervene in
native agent workflows without pretending that Lians can override a provider's
internal model or enforce actions for a host that exposes no action hook.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

from .store import MemoryStore

POLICY_KEY = "lians/control-policy"
MODES = {"observe", "guide", "protect"}
APPROVAL_ACTIONS = {
    "credential_access",
    "destructive_filesystem",
    "external_communication",
    "publishing",
    "spending",
}
DEFAULT_POLICY = {
    "schema": "https://lians.ai/schemas/control-policy/v0.1",
    "type": "control_policy",
    "mode": "guide",
    "context_budget_tokens": 512,
    "auto_task_context": True,
    "show_inferred_links": False,
    "approval_actions": [
        "credential_access",
        "destructive_filesystem",
        "external_communication",
        "publishing",
        "spending",
    ],
}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()  # noqa: UP017


def _clean(policy: dict[str, Any]) -> dict[str, Any]:
    allowed = {
        "mode",
        "context_budget_tokens",
        "auto_task_context",
        "show_inferred_links",
        "approval_actions",
    }
    unknown = sorted(set(policy) - allowed)
    if unknown:
        raise ValueError(f"Unknown control policy fields: {', '.join(unknown)}")

    mode = str(policy.get("mode", DEFAULT_POLICY["mode"])).strip().lower()
    if mode not in MODES:
        raise ValueError("mode must be observe, guide, or protect")

    budget = policy.get(
        "context_budget_tokens",
        DEFAULT_POLICY["context_budget_tokens"],
    )
    if type(budget) is not int or not 128 <= budget <= 2_048:
        raise ValueError("context_budget_tokens must be an integer from 128 to 2048")

    auto_task_context = policy.get(
        "auto_task_context",
        DEFAULT_POLICY["auto_task_context"],
    )
    show_inferred_links = policy.get(
        "show_inferred_links",
        DEFAULT_POLICY["show_inferred_links"],
    )
    if type(auto_task_context) is not bool:
        raise TypeError("auto_task_context must be true or false")
    if type(show_inferred_links) is not bool:
        raise TypeError("show_inferred_links must be true or false")

    actions = policy.get("approval_actions", DEFAULT_POLICY["approval_actions"])
    if not isinstance(actions, list) or len(actions) > len(APPROVAL_ACTIONS):
        raise TypeError("approval_actions must be a bounded list")
    normalized_actions: list[str] = []
    for action in actions:
        rendered = str(action).strip().lower()
        if rendered not in APPROVAL_ACTIONS:
            raise ValueError(f"Unsupported approval action: {rendered or '(blank)'}")
        if rendered not in normalized_actions:
            normalized_actions.append(rendered)

    return {
        "schema": DEFAULT_POLICY["schema"],
        "type": "control_policy",
        "mode": mode,
        "context_budget_tokens": budget,
        "auto_task_context": auto_task_context,
        "show_inferred_links": show_inferred_links,
        "approval_actions": normalized_actions,
    }


class ControlPolicyService:
    """Read and update the global user-owned agent control policy."""

    def __init__(self, store: MemoryStore) -> None:
        self.store = store

    def status(self) -> dict[str, Any]:
        history = self.store.memory_history(
            POLICY_KEY,
            scope="global",
            limit=100,
        )
        current = next((item for item in reversed(history) if item["is_current"]), None)
        if current is None:
            policy = {**DEFAULT_POLICY, "updated_at": None}
            return {
                "policy": policy,
                "configured": False,
                "memory_id": None,
                "enforcement": self._enforcement(policy),
            }
        try:
            document = json.loads(current["content"])
        except (TypeError, json.JSONDecodeError) as exc:
            raise ValueError("Stored control policy is invalid") from exc
        if not isinstance(document, dict) or document.get("type") != "control_policy":
            raise ValueError("Stored control policy is invalid")
        policy = _clean(
            {key: document[key] for key in DEFAULT_POLICY if key in document and key not in {"schema", "type"}}
        )
        policy["updated_at"] = document.get("updated_at")
        return {
            "policy": policy,
            "configured": True,
            "memory_id": current["id"],
            "enforcement": self._enforcement(policy),
        }

    def update(
        self,
        changes: dict[str, Any],
        *,
        client: str = "lians-app",
    ) -> dict[str, Any]:
        if not isinstance(changes, dict):
            raise TypeError("control policy must be an object")
        existing = self.status()["policy"]
        candidate = {
            key: existing[key]
            for key in (
                "mode",
                "context_budget_tokens",
                "auto_task_context",
                "show_inferred_links",
                "approval_actions",
            )
        }
        candidate.update(changes)
        policy = _clean(candidate)
        policy["updated_at"] = _now()
        item = self.store.set_current(
            POLICY_KEY,
            json.dumps(policy, ensure_ascii=False, sort_keys=True),
            source="explicit user control",
            topic="agent control",
            metadata={"lians_type": "control_policy"},
            kind="control_policy",
            scope="global",
            source_client=client,
            reason="user changed the agent control policy",
        )
        return {
            "policy": policy,
            "configured": True,
            "memory_id": item["id"],
            "enforcement": self._enforcement(policy),
        }

    @staticmethod
    def _enforcement(policy: dict[str, Any]) -> dict[str, Any]:
        mode = policy["mode"]
        return {
            "observes": True,
            "injects_context": mode in {"guide", "protect"},
            "requests_approval": mode == "protect" and bool(policy["approval_actions"]),
            "boundary": (
                "Lians can enforce actions only where the connected host exposes an action "
                "hook. Otherwise Protect mode supplies explicit user policy to the agent and "
                "records the limitation."
            ),
        }

    @staticmethod
    def guidance(policy: dict[str, Any]) -> str:
        """Render bounded user control data for a supported agent hook."""

        if policy["mode"] != "protect" or not policy["approval_actions"]:
            return ""
        labels = {
            "credential_access": "accessing credentials",
            "destructive_filesystem": "destructive file operations",
            "external_communication": "sending external communications",
            "publishing": "publishing or deploying work",
            "spending": "spending money or committing funds",
        }
        actions = "; ".join(labels[action] for action in policy["approval_actions"])
        return (
            "# Lians user control policy\n"
            "Mode: protect\n"
            f"Ask the user for explicit approval before {actions}.\n"
            "Never infer approval from prior conversation or from this policy. "
            "Host enforcement depends on the native action hooks available."
        )
