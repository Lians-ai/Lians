from __future__ import annotations

import copy
import importlib.util
import sys
from pathlib import Path

import pytest

SCRIPT_PATH = Path(__file__).parents[2] / "scripts" / "select_fly_production_machine.py"
SPEC = importlib.util.spec_from_file_location(
    "select_fly_production_machine", SCRIPT_PATH
)
assert SPEC is not None
assert SPEC.loader is not None
MODULE = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = MODULE
SPEC.loader.exec_module(MODULE)

EXPECTED_IMAGE = "registry.fly.io/agentmem-lotus:deployment-github-123-1"
EXPECTED_SHA = "a" * 40


def machine(*, state: str = "started") -> dict[str, object]:
    return {
        "id": "78451d0fe5d158",
        "state": state,
        "image_ref": {
            "registry": "registry.fly.io",
            "repository": "agentmem-lotus",
            "tag": "deployment-github-123-1",
            "labels": {"GH_SHA": EXPECTED_SHA},
        },
        "config": {"image": EXPECTED_IMAGE},
        "checks": [{"name": "servicecheck", "status": "passing"}],
        "host_status": "ok",
        "cordoned": False,
    }


def select(machines: object) -> str | None:
    return MODULE.select_machine(
        machines,
        expected_image=EXPECTED_IMAGE,
        expected_sha=EXPECTED_SHA,
    )


def test_selects_only_exact_healthy_release_machine() -> None:
    assert select([machine()]) == "78451d0fe5d158"


@pytest.mark.parametrize("state", ["created", "starting", "replacing", "stopping"])
def test_waits_during_legitimate_fly_transition_states(state: str) -> None:
    assert select([machine(state=state)]) is None


def test_waits_for_exact_image_commit_and_health() -> None:
    wrong_config = machine()
    wrong_config["config"] = {"image": EXPECTED_IMAGE + "-stale"}

    wrong_commit = machine()
    wrong_commit_image_ref = copy.deepcopy(wrong_commit["image_ref"])
    assert isinstance(wrong_commit_image_ref, dict)
    wrong_commit_image_ref["labels"] = {"GH_SHA": "b" * 40}
    wrong_commit["image_ref"] = wrong_commit_image_ref

    failing_check = machine()
    failing_check["checks"] = [{"name": "servicecheck", "status": "critical"}]

    for candidate in (wrong_config, wrong_commit, failing_check):
        assert select([candidate]) is None


def test_does_not_choose_during_ambiguous_multi_machine_state() -> None:
    second = machine()
    second["id"] = "28692e6b642758"
    assert select([machine(), second]) is None


def test_rejects_malformed_expected_identity() -> None:
    with pytest.raises(ValueError, match="image reference"):
        MODULE.select_machine(
            [machine()],
            expected_image="docker.io/untrusted/image:latest",
            expected_sha=EXPECTED_SHA,
        )
    with pytest.raises(ValueError, match="commit SHA"):
        MODULE.select_machine(
            [machine()],
            expected_image=EXPECTED_IMAGE,
            expected_sha="main",
        )
