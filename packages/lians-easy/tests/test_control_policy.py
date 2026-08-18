from __future__ import annotations

import pytest
from lians_easy.control_policy import ControlPolicyService
from lians_easy.store import MemoryStore


def test_control_policy_defaults_to_guide_and_is_encrypted(tmp_path) -> None:
    database = tmp_path / "memory.sqlite3"
    service = ControlPolicyService(MemoryStore(database))

    default = service.status()
    assert default["configured"] is False
    assert default["policy"]["mode"] == "guide"
    assert default["enforcement"]["injects_context"] is True

    protected = service.update(
        {
            "mode": "protect",
            "context_budget_tokens": 768,
            "approval_actions": ["publishing", "spending"],
        },
        client="lians-app",
    )

    assert protected["policy"]["mode"] == "protect"
    assert protected["policy"]["approval_actions"] == ["publishing", "spending"]
    assert protected["enforcement"]["requests_approval"] is True
    assert b'"mode": "protect"' not in database.read_bytes()


def test_control_policy_rejects_unknown_fields_types_and_actions(tmp_path) -> None:
    service = ControlPolicyService(MemoryStore(tmp_path / "memory.sqlite3"))

    with pytest.raises(ValueError, match="Unknown control policy fields"):
        service.update({"hidden_agent_override": True})
    with pytest.raises(ValueError, match="128 to 2048"):
        service.update({"context_budget_tokens": 50})
    with pytest.raises(TypeError, match="true or false"):
        service.update({"auto_task_context": "yes"})
    with pytest.raises(ValueError, match="Unsupported approval action"):
        service.update({"approval_actions": ["read_every_file"]})


def test_protect_guidance_is_bounded_and_requires_fresh_approval(tmp_path) -> None:
    service = ControlPolicyService(MemoryStore(tmp_path / "memory.sqlite3"))
    policy = service.update(
        {
            "mode": "protect",
            "approval_actions": ["external_communication", "destructive_filesystem"],
        }
    )["policy"]

    guidance = service.guidance(policy)

    assert "sending external communications" in guidance
    assert "destructive file operations" in guidance
    assert "Never infer approval" in guidance
    assert len(guidance) < 700
