"""Versioned memory behavior profiles shared by local and hosted runtimes."""
from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from .admission_service import evaluate_memory_admission
from .audit_chain import chain_log
from .memory_priority import apply_memory_priority
from .models import Agent
from .schemas import AgentPolicyOut, AgentPolicyUpdate, MemoryAdd, PolicyProfileOut

PROFILE_VERSION = "2026-08-11"
DEFAULT_PROFILE = "balanced"

_PROFILES: dict[str, dict[str, Any]] = {
    "balanced": {
        "description": "General-purpose capture with durable facts promoted and risky writes observed.",
        "capture": {"admission_mode": "monitor", "durable_importance_floor": 0.88},
        "recall": {"default_mode": "fast", "preference_first": True},
        "lifecycle": {"retention_days": None, "human_review": "on-signal"},
    },
    "personal_assistant": {
        "description": "Preserve preferences, accessibility needs, identity facts, and explicit remember requests.",
        "capture": {"admission_mode": "monitor", "durable_importance_floor": 0.94},
        "recall": {"default_mode": "fast", "preference_first": True},
        "lifecycle": {"retention_days": None, "human_review": "user-controlled"},
    },
    "coding_agent": {
        "description": "Prioritize project constraints, architecture decisions, conventions, and verified outcomes.",
        "capture": {"admission_mode": "monitor", "durable_importance_floor": 0.90},
        "recall": {"default_mode": "fast", "preference_first": True},
        "lifecycle": {"retention_days": 365, "human_review": "on-conflict"},
    },
    "support_agent": {
        "description": "Retain customer context while routing sensitive records to review.",
        "capture": {"admission_mode": "enforce", "durable_importance_floor": 0.90},
        "recall": {"default_mode": "fast", "preference_first": True},
        "lifecycle": {"retention_days": 180, "human_review": "sensitive-and-conflict"},
    },
    "regulated_analyst": {
        "description": "Fail closed on risky capture and favor evidence-rich, reconstructable recall.",
        "capture": {"admission_mode": "enforce", "durable_importance_floor": 0.92},
        "recall": {"default_mode": "reconstruct", "preference_first": False},
        "lifecycle": {"retention_days": 2555, "human_review": "required"},
    },
}

_OVERRIDE_KEYS = {
    "admission_mode",
    "durable_importance_floor",
    "default_recall_mode",
    "retention_days",
}
_MODE_ORDER = {"off": 0, "monitor": 1, "enforce": 2}


def list_policy_profiles() -> list[PolicyProfileOut]:
    return [
        PolicyProfileOut(name=name, version=PROFILE_VERSION, **deepcopy(profile))
        for name, profile in sorted(_PROFILES.items())
    ]


def _stored_policy(agent: Agent | None) -> dict[str, Any]:
    config = dict(agent.config or {}) if agent is not None else {}
    value = config.get("memory_policy")
    return dict(value) if isinstance(value, dict) else {}


def _resolve(stored: dict[str, Any]) -> tuple[str, dict[str, Any], dict[str, Any]]:
    profile_name = str(stored.get("profile") or DEFAULT_PROFILE)
    if profile_name not in _PROFILES:
        profile_name = DEFAULT_PROFILE
    effective = deepcopy(_PROFILES[profile_name])
    overrides = {
        key: value
        for key, value in dict(stored.get("overrides") or {}).items()
        if key in _OVERRIDE_KEYS
    }
    capture = effective["capture"]
    recall = effective["recall"]
    lifecycle = effective["lifecycle"]
    if "admission_mode" in overrides:
        capture["admission_mode"] = overrides["admission_mode"]
    if "durable_importance_floor" in overrides:
        capture["durable_importance_floor"] = overrides["durable_importance_floor"]
    if "default_recall_mode" in overrides:
        recall["default_mode"] = overrides["default_recall_mode"]
    if "retention_days" in overrides:
        lifecycle["retention_days"] = overrides["retention_days"]
    return profile_name, effective, overrides


async def get_agent_policy(
    db: AsyncSession, namespace: str, agent_id: str,
) -> AgentPolicyOut:
    agent = await db.get(Agent, (namespace, agent_id))
    stored = _stored_policy(agent)
    profile_name, effective, overrides = _resolve(stored)
    assigned_at = stored.get("assigned_at")
    return AgentPolicyOut(
        agent_id=agent_id,
        profile=profile_name,
        profile_version=str(stored.get("profile_version") or PROFILE_VERSION),
        revision=int(stored.get("revision") or 0),
        effective=effective,
        overrides=overrides,
        assigned_at=datetime.fromisoformat(assigned_at) if assigned_at else None,
        assigned_by=stored.get("assigned_by"),
    )


def _validate_overrides(overrides: dict[str, Any]) -> None:
    unknown = sorted(set(overrides) - _OVERRIDE_KEYS)
    if unknown:
        raise ValueError(f"Unsupported policy override(s): {', '.join(unknown)}")
    if "admission_mode" in overrides and overrides["admission_mode"] not in _MODE_ORDER:
        raise ValueError("admission_mode must be off, monitor, or enforce")
    if "default_recall_mode" in overrides and overrides["default_recall_mode"] not in {
        "fast", "deep", "reconstruct",
    }:
        raise ValueError("default_recall_mode must be fast, deep, or reconstruct")
    if "durable_importance_floor" in overrides:
        floor = overrides["durable_importance_floor"]
        if not isinstance(floor, (int, float)) or not 0 <= float(floor) <= 1:
            raise ValueError("durable_importance_floor must be between 0 and 1")
    if "retention_days" in overrides:
        days = overrides["retention_days"]
        if days is not None and (not isinstance(days, int) or days < 1):
            raise ValueError("retention_days must be null or a positive integer")


async def set_agent_policy(
    db: AsyncSession,
    namespace: str,
    agent_id: str,
    req: AgentPolicyUpdate,
) -> AgentPolicyOut:
    if req.profile not in _PROFILES:
        raise ValueError(f"Unknown policy profile: {req.profile}")
    _validate_overrides(req.overrides)
    agent = await db.get(Agent, (namespace, agent_id))
    if agent is None:
        agent = Agent(namespace=namespace, agent_id=agent_id, config={})
        db.add(agent)
    stored = _stored_policy(agent)
    revision = int(stored.get("revision") or 0)
    if req.expected_revision is not None and req.expected_revision != revision:
        raise RuntimeError(
            f"Policy revision changed: expected {req.expected_revision}, found {revision}"
        )
    now = datetime.now(timezone.utc)
    next_policy = {
        "profile": req.profile,
        "profile_version": PROFILE_VERSION,
        "revision": revision + 1,
        "overrides": dict(req.overrides),
        "assigned_at": now.isoformat(),
        "assigned_by": req.actor,
    }
    config = dict(agent.config or {})
    config["memory_policy"] = next_policy
    agent.config = config
    await chain_log(
        db,
        namespace=namespace,
        agent_id=agent_id,
        op="memory_policy_updated",
        payload={
            "profile": req.profile,
            "profile_version": PROFILE_VERSION,
            "revision": revision + 1,
            "override_keys": sorted(req.overrides),
            "actor": req.actor,
        },
    )
    await db.commit()
    return await get_agent_policy(db, namespace, agent_id)


async def evaluate_profiled_admission(
    db: AsyncSession,
    namespace: str,
    req: MemoryAdd,
    *,
    configured_mode: str,
    blocked_sources: str,
):
    policy = await get_agent_policy(db, namespace, req.agent_id)
    priority = apply_memory_priority(req)
    floor = float(policy.effective["capture"]["durable_importance_floor"])
    if priority.durable:
        req.importance = max(req.importance, floor)
        metadata = dict(req.metadata or {})
        priority_meta = dict(metadata.get("_memory_priority") or {})
        priority_meta["importance"] = req.importance
        metadata["_memory_priority"] = priority_meta
        req.metadata = metadata
    metadata = dict(req.metadata or {})
    metadata.pop("_policy", None)
    metadata["_policy"] = {
        "profile": policy.profile,
        "profile_version": policy.profile_version,
        "revision": policy.revision,
    }
    if req.scope:
        metadata["_scope_path"] = req.scope
    req.metadata = metadata
    profile_mode = str(policy.effective["capture"]["admission_mode"])
    mode = max((configured_mode, profile_mode), key=lambda value: _MODE_ORDER[value])
    return evaluate_memory_admission(
        req,
        mode=mode,
        blocked_sources=blocked_sources,
    )


def scope_paths(scope: str | None, include_parents: bool) -> list[str]:
    if not scope:
        return []
    parts = scope.split("/")
    if not include_parents:
        return [scope]
    return ["/".join(parts[:index]) for index in range(2, len(parts) + 1, 2)]
