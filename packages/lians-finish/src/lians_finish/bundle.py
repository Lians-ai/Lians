"""Portable, checksummed Lians agent bundles."""

from __future__ import annotations

import hashlib
import json
import re
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .governance import MissionInput
from .policy import ROUTER_VERSION, RoutePlan, build_route
from .runtime import parse_verifier_command, validate_verifier_command

AGENT_BUNDLE_SCHEMA = "lians.agent.bundle.v1"
AGENT_BUNDLE_VERSION = 1


class AgentBundleError(ValueError):
    """Raised when a portable agent bundle is unsafe or invalid."""


def _reject_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    value: dict[str, Any] = {}
    for key, item in pairs:
        if key in value:
            raise AgentBundleError(f"agent bundle contains a duplicate field: {key}")
        value[key] = item
    return value


def _reject_constant(value: str) -> Any:
    raise AgentBundleError(f"agent bundle contains a non-finite number: {value}")


def _now_iso() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


def _sha256(value: Any) -> str:
    canonical = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _exact_fields(value: Any, required: set[str], label: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise AgentBundleError(f"{label} must be an object")
    missing = required - set(value)
    unknown = set(value) - required
    if missing:
        raise AgentBundleError(f"{label} is missing: {', '.join(sorted(missing))}")
    if unknown:
        raise AgentBundleError(f"{label} contains unknown fields: {', '.join(sorted(unknown))}")
    return value


def _portable_proof(command: str) -> str:
    if not isinstance(command, str) or not command.strip():
        raise AgentBundleError("a proof command is required before exporting an agent")
    try:
        argv = parse_verifier_command(command.strip())
        validate_verifier_command(argv)
    except ValueError as exc:
        raise AgentBundleError(
            "portable agents require a supported proof runner; custom executable approvals "
            "cannot be exported"
        ) from exc
    return command.strip()


def create_agent_bundle(
    mission: MissionInput,
    route: RoutePlan,
    verification_command: str,
    *,
    product_version: str,
    exported_at: str | None = None,
) -> dict[str, Any]:
    """Create a portable agent without persisting or executing the mission."""

    proof = _portable_proof(verification_command)
    route_document = route.to_dict()
    envelope: dict[str, Any] = {
        "schema": AGENT_BUNDLE_SCHEMA,
        "bundle_version": AGENT_BUNDLE_VERSION,
        "product_version": product_version,
        "exported_at": exported_at or _now_iso(),
        "agent": {
            "mission": {
                "goal": mission.goal,
                "workspace_name": mission.repository.name,
                "constraints": list(mission.constraints),
                "definition_of_done": mission.definition_of_done,
                "valid_until": mission.valid_until,
            },
            "policy": {
                "mode": route.mode,
                "premium_budget": route.max_premium_calls,
                "router_version": route.router_version,
                "route_sha256": route_document["route_sha256"],
            },
            "proof": {"command": proof},
        },
    }
    envelope["bundle_sha256"] = _sha256(envelope)
    return envelope


def parse_agent_bundle(text: str) -> dict[str, Any]:
    """Parse an untrusted exported file without collapsing ambiguous JSON."""

    if not isinstance(text, str) or not text.strip():
        raise AgentBundleError("agent bundle file is empty")
    if len(text.encode("utf-8")) > 60 * 1024:
        raise AgentBundleError("agent bundle file is larger than 60 KB")
    try:
        value = json.loads(
            text,
            object_pairs_hook=_reject_duplicate_keys,
            parse_constant=_reject_constant,
        )
    except AgentBundleError:
        raise
    except json.JSONDecodeError as exc:
        raise AgentBundleError("agent bundle file is not valid JSON") from exc
    if not isinstance(value, dict):
        raise AgentBundleError("agent bundle root must be an object")
    return value


def verify_agent_bundle(value: Any) -> dict[str, Any]:
    """Verify a bundle and return normalized, inert form values plus a fresh route."""

    root = _exact_fields(
        value,
        {
            "schema",
            "bundle_version",
            "product_version",
            "exported_at",
            "agent",
            "bundle_sha256",
        },
        "agent bundle",
    )
    if root["schema"] != AGENT_BUNDLE_SCHEMA or root["bundle_version"] != AGENT_BUNDLE_VERSION:
        raise AgentBundleError("unsupported agent bundle version")
    if not isinstance(root["product_version"], str) or not root["product_version"].strip():
        raise AgentBundleError("agent bundle has an invalid product version")
    if not isinstance(root["exported_at"], str):
        raise AgentBundleError("agent bundle has an invalid export time")
    try:
        timestamp = datetime.fromisoformat(root["exported_at"])
    except ValueError as exc:
        raise AgentBundleError("agent bundle has an invalid export time") from exc
    if timestamp.tzinfo is None:
        raise AgentBundleError("agent bundle export time must include a timezone")
    digest = root["bundle_sha256"]
    if not isinstance(digest, str) or re.fullmatch(r"[0-9a-f]{64}", digest) is None:
        raise AgentBundleError("agent bundle has an invalid checksum")
    unsigned = {key: item for key, item in root.items() if key != "bundle_sha256"}
    if _sha256(unsigned) != digest:
        raise AgentBundleError("agent bundle checksum does not match its contents")

    agent = _exact_fields(root["agent"], {"mission", "policy", "proof"}, "agent")
    mission_value = _exact_fields(
        agent["mission"],
        {"goal", "workspace_name", "constraints", "definition_of_done", "valid_until"},
        "agent mission",
    )
    workspace_name = mission_value["workspace_name"]
    if (
        not isinstance(workspace_name, str)
        or not workspace_name.strip()
        or len(workspace_name) > 255
        or any(character in workspace_name for character in "/\\\r\n")
    ):
        raise AgentBundleError("agent mission has an invalid workspace name")
    workspace_name = workspace_name.strip()
    try:
        mission = MissionInput.from_payload(
            {
                "task": mission_value["goal"],
                "constraints": mission_value["constraints"],
                "definition_of_done": mission_value["definition_of_done"],
                "valid_until": mission_value["valid_until"],
            },
            Path.cwd(),
        )
    except (TypeError, ValueError) as exc:
        raise AgentBundleError(str(exc)) from exc

    policy = _exact_fields(
        agent["policy"],
        {"mode", "premium_budget", "router_version", "route_sha256"},
        "agent policy",
    )
    if not isinstance(policy["mode"], str) or type(policy["premium_budget"]) is not int:
        raise AgentBundleError("agent policy has an invalid mode or premium budget")
    if not isinstance(policy["router_version"], str):
        raise AgentBundleError("agent policy has an invalid router version")
    if (
        not isinstance(policy["route_sha256"], str)
        or re.fullmatch(r"[0-9a-f]{64}", policy["route_sha256"]) is None
    ):
        raise AgentBundleError("agent policy has an invalid route checksum")
    try:
        route = build_route(
            mission.goal,
            mode=policy["mode"],
            premium_budget=policy["premium_budget"],
        )
    except ValueError as exc:
        raise AgentBundleError(str(exc)) from exc
    current_route = route.to_dict()
    route_stale = policy["router_version"] != ROUTER_VERSION
    if not route_stale and policy["route_sha256"] != current_route["route_sha256"]:
        raise AgentBundleError("agent policy route checksum does not match its contents")

    proof = _exact_fields(agent["proof"], {"command"}, "agent proof")
    verification_command = _portable_proof(proof["command"])
    return {
        "schema": root["schema"],
        "bundle_sha256": digest,
        "product_version": root["product_version"],
        "exported_at": timestamp.astimezone(UTC).isoformat().replace("+00:00", "Z"),
        "task": mission.goal,
        "repository": "",
        "workspace_name": workspace_name,
        "constraints": "\n".join(mission.constraints),
        "definition_of_done": mission.definition_of_done,
        "valid_until": mission.valid_until,
        "mode": route.mode,
        "premium_budget": route.max_premium_calls,
        "verification_command": verification_command,
        "route": current_route,
        "route_stale": route_stale,
        "claim_boundary": (
            "Import verifies integrity and rebuilds policy locally. It never executes the agent, "
            "runs its proof command, transfers custom executable approvals, or selects a local "
            "workspace on the user's behalf."
        ),
    }


def bundle_filename(repository: Path, mode: str) -> str:
    """Return a predictable filename without exposing a full local path."""

    safe = re.sub(r"[^a-z0-9]+", "-", repository.name.casefold()).strip("-") or "agent"
    return f"{safe}-{mode}.lians-agent"
