"""Select the one healthy Fly machine running an exact production build."""

from __future__ import annotations

import argparse
import json
import re
import sys
from typing import Any

EXPECTED_IMAGE_PREFIX = "registry.fly.io/agentmem-lotus:deployment-"
MACHINE_ID_PATTERN = re.compile(r"^[0-9a-f]{14}$")


def _published_image(machine: dict[str, Any]) -> str | None:
    image_ref = machine.get("image_ref")
    if not isinstance(image_ref, dict):
        return None
    registry = image_ref.get("registry")
    repository = image_ref.get("repository")
    tag = image_ref.get("tag")
    if not all(isinstance(value, str) and value for value in (registry, repository, tag)):
        return None
    return f"{registry}/{repository}:{tag}"


def select_machine(
    machines: object,
    *,
    expected_image: str,
    expected_sha: str,
) -> str | None:
    """Return the exact ready machine, or ``None`` while Fly is converging."""
    if not expected_image.startswith(EXPECTED_IMAGE_PREFIX):
        raise ValueError("unexpected production image reference")
    if not re.fullmatch(r"[0-9a-f]{40}", expected_sha):
        raise ValueError("expected SHA must be a full lowercase Git commit SHA")
    if not isinstance(machines, list) or not all(
        isinstance(machine, dict) for machine in machines
    ):
        raise TypeError("Fly machines payload must be a JSON array of objects")

    started = [machine for machine in machines if machine.get("state") == "started"]
    if len(started) != 1:
        return None

    machine = started[0]
    config = machine.get("config")
    image_ref = machine.get("image_ref")
    checks = machine.get("checks")
    if not isinstance(config, dict) or not isinstance(image_ref, dict):
        return None
    labels = image_ref.get("labels")
    if not isinstance(labels, dict):
        return None
    if config.get("image") != expected_image:
        return None
    if _published_image(machine) != expected_image:
        return None
    if labels.get("GH_SHA") != expected_sha:
        return None
    if machine.get("host_status") != "ok" or machine.get("cordoned") is not False:
        return None
    if not isinstance(checks, list) or not checks:
        return None
    if not all(
        isinstance(check, dict) and check.get("status") == "passing"
        for check in checks
    ):
        return None

    machine_id = machine.get("id")
    if not isinstance(machine_id, str) or not MACHINE_ID_PATTERN.fullmatch(machine_id):
        raise ValueError("Fly returned an invalid production machine ID")
    return machine_id


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--expected-image", required=True)
    parser.add_argument("--expected-sha", required=True)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    try:
        machines = json.load(sys.stdin)
        machine_id = select_machine(
            machines,
            expected_image=args.expected_image,
            expected_sha=args.expected_sha,
        )
    except (json.JSONDecodeError, TypeError, ValueError) as exc:
        print(f"Invalid Fly machine data: {exc}", file=sys.stderr)
        return 2
    if machine_id is None:
        return 1
    print(machine_id)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
