"""Constrained runtime decisions without becoming a generic model gateway."""

from __future__ import annotations

import hashlib
import json
import logging
import time
import uuid
from datetime import UTC, datetime
from typing import Any
from urllib.parse import urlsplit
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from .improvement_models import AgentVersion, Comparison, EvaluationAttestation
from .improvement_service import (
    barrier_scope,
    evaluation_attestation_out,
    sha256_json,
    verify_evaluation_attestation,
    visible_by_id,
)
from .runtime_models import CacheDecision, ConcurrencyPlan, RoutingDecision, RuntimePolicyVersion
from .runtime_schemas import (
    CacheAccessRequest,
    CacheDecisionOut,
    ConcurrencyPlanOut,
    ConcurrencyPlanRequest,
    RouteDecideRequest,
    RoutedCandidateOut,
    RoutingDecisionOut,
    RuntimePolicyCreate,
    RuntimePolicyOut,
)
from .secret_storage import seal_text, unseal_text

logger = logging.getLogger("lians.runtime")
_runtime_redis: Any = None
_CACHE_PURPOSE = "runtime-exact-cache"


class RuntimeContractError(ValueError):
    """No safe runtime decision can satisfy the declared constraints."""


def _get_runtime_redis() -> Any:
    global _runtime_redis
    if _runtime_redis is None:
        import redis.asyncio as aioredis

        from .config import get_settings

        redis_url = get_settings().redis_url
        tls_options: dict[str, Any] = {}
        if urlsplit(redis_url).scheme.casefold() == "rediss":
            tls_options = {"ssl_cert_reqs": "required", "ssl_check_hostname": True}
        _runtime_redis = aioredis.from_url(
            redis_url,
            decode_responses=True,
            socket_connect_timeout=1,
            socket_timeout=1,
            **tls_options,
        )
    return _runtime_redis


def runtime_policy_out(row: RuntimePolicyVersion) -> RuntimePolicyOut:
    return RuntimePolicyOut(
        id=row.id,
        namespace=row.namespace,
        barrier_group=row.barrier_group,
        name=row.name,
        version=row.version,
        quality_floor=row.quality_floor,
        objective=row.objective,
        request_budget=row.request_budget,
        timeout_retry_policy=row.timeout_retry_policy,
        fallback_policy=row.fallback_policy,
        cache_policy=row.cache_policy,
        policy_hash=row.policy_hash,
        created_by_principal_ref=row.created_by_principal_ref,
        created_at=row.created_at,
    )


async def create_runtime_policy(
    db: AsyncSession,
    *,
    namespace: str,
    barrier_group: str | None,
    principal_ref: str,
    body: RuntimePolicyCreate,
) -> RuntimePolicyVersion:
    document = {"schema": "lians.runtime-policy.v1", **body.model_dump(mode="json")}
    row = RuntimePolicyVersion(
        id=uuid.uuid4(),
        namespace=namespace,
        barrier_group=barrier_group,
        barrier_scope=barrier_scope(barrier_group),
        name=body.name,
        version=body.version,
        quality_floor=body.quality_floor,
        objective=body.objective.model_dump(mode="json"),
        request_budget=body.request_budget.model_dump(mode="json"),
        timeout_retry_policy=body.timeout_retry_policy.model_dump(mode="json"),
        fallback_policy=body.fallback_policy.model_dump(mode="json"),
        cache_policy=body.cache_policy.model_dump(mode="json"),
        policy_hash=sha256_json(document),
        created_by_principal_ref=principal_ref,
    )
    db.add(row)
    await db.flush()
    return row


def _comparison_candidate_metric(comparison: Comparison, name: str | None) -> float | None:
    if name is None:
        return None
    for aggregate in comparison.aggregates:
        if aggregate.get("name") == name:
            return float(aggregate["candidate_mean"])
    return None


async def create_routing_decision(
    db: AsyncSession,
    *,
    policy: RuntimePolicyVersion,
    namespace: str,
    barrier_group: str | None,
    principal_ref: str,
    body: RouteDecideRequest,
) -> RoutingDecision:
    started = time.perf_counter()
    budget = policy.request_budget
    if body.input_tokens > int(budget["max_input_tokens"]):
        raise RuntimeContractError("request input exceeds the immutable token budget")
    if body.requested_output_tokens > int(budget["max_output_tokens"]):
        raise RuntimeContractError("requested output exceeds the immutable output-length contract")
    objective = policy.objective
    qualified: list[dict[str, Any]] = []
    rejected: list[dict[str, Any]] = []
    for candidate in body.candidates:
        if not candidate.available:
            rejected.append(
                {"agent_version_id": str(candidate.agent_version_id), "reason": "unavailable"}
            )
            continue
        version = await visible_by_id(
            db,
            AgentVersion,
            candidate.agent_version_id,
            namespace=namespace,
            barrier_group=barrier_group,
        )
        attestation = await visible_by_id(
            db,
            EvaluationAttestation,
            candidate.evaluation_attestation_id,
            namespace=namespace,
            barrier_group=barrier_group,
        )
        verification = verify_evaluation_attestation(evaluation_attestation_out(attestation))
        if not verification.valid:
            raise RuntimeContractError("a routing candidate Evaluation Attestation is invalid")
        payload_candidate_id = str(attestation.payload.get("candidate", {}).get("agent_version_id"))
        if payload_candidate_id != str(version.id):
            raise RuntimeContractError(
                "routing candidate version does not match its Evaluation Attestation"
            )
        comparison = await visible_by_id(
            db,
            Comparison,
            attestation.comparison_id,
            namespace=namespace,
            barrier_group=barrier_group,
        )
        if comparison.comparison_hash != attestation.payload["comparison"]["hash"]:
            raise RuntimeContractError("routing candidate comparison hash is invalid")
        if comparison.verdict != "eligible_for_review" or not comparison.critical_invariants_passed:
            raise RuntimeContractError(
                "routing candidates require verified improvement and passing invariants"
            )
        quality = _comparison_candidate_metric(comparison, objective["quality_metric"])
        latency = _comparison_candidate_metric(comparison, objective.get("latency_metric"))
        cost = _comparison_candidate_metric(comparison, objective.get("cost_metric"))
        if quality is None or quality < policy.quality_floor:
            rejected.append(
                {
                    "agent_version_id": str(version.id),
                    "reason": "quality_floor",
                    "observed_quality": quality,
                }
            )
            continue
        if latency is not None and latency > float(budget["deadline_ms"]):
            rejected.append({"agent_version_id": str(version.id), "reason": "latency_budget"})
            continue
        if budget.get("max_cost") is not None and (
            cost is None or cost > float(budget["max_cost"])
        ):
            rejected.append({"agent_version_id": str(version.id), "reason": "cost_budget"})
            continue
        latency_component = float(latency or 0) * float(objective.get("latency_weight", 0))
        cost_component = float(cost or 0) * float(objective.get("cost_weight", 0))
        routed = RoutedCandidateOut(
            agent_version_id=version.id,
            evaluation_attestation_id=attestation.id,
            provider=candidate.provider,
            model=candidate.model,
            quality=quality,
            predicted_latency=latency,
            predicted_cost=cost,
            evaluation_attestation_hash=attestation.payload_hash,
        ).model_dump(mode="json")
        qualified.append({"score": latency_component + cost_component, "candidate": routed})
    if not qualified:
        raise RuntimeContractError(
            "no routing candidate satisfies the approved quality and budget floor"
        )
    qualified.sort(
        key=lambda item: (
            item["score"],
            -float(item["candidate"]["quality"]),
            item["candidate"]["provider"],
            item["candidate"]["model"],
        )
    )
    selected = qualified[0]["candidate"]
    fallback_count = (
        int(policy.fallback_policy.get("max_fallbacks", 0))
        if policy.fallback_policy.get("enabled", True)
        else 0
    )
    fallbacks = [item["candidate"] for item in qualified[1 : 1 + fallback_count]]
    request_hash = sha256_json(
        {
            "policy_hash": policy.policy_hash,
            "input_hash": body.input_hash,
            "input_tokens": body.input_tokens,
            "requested_output_tokens": body.requested_output_tokens,
            "candidate_attestation_ids": [
                str(candidate.evaluation_attestation_id) for candidate in body.candidates
            ],
        }
    )
    overhead_ms = (time.perf_counter() - started) * 1000
    row_id = uuid.uuid4()
    decided_at = datetime.now(UTC)
    document = {
        "schema": "lians.routing-decision.v1",
        "id": str(row_id),
        "policy_hash": policy.policy_hash,
        "request_hash": request_hash,
        "selected": selected,
        "fallbacks": fallbacks,
        "rejected": rejected,
        "budget": budget,
        "overhead_ms": overhead_ms,
        "decided_at": decided_at,
    }
    row = RoutingDecision(
        id=row_id,
        namespace=namespace,
        barrier_group=barrier_group,
        barrier_scope=barrier_scope(barrier_group),
        runtime_policy_version_id=policy.id,
        agent_version_id=UUID(selected["agent_version_id"]),
        request_hash=request_hash,
        selected=selected,
        fallbacks=fallbacks,
        rejected=rejected,
        budget=budget,
        overhead_ms=overhead_ms,
        decision_hash=sha256_json(document),
        created_by_principal_ref=principal_ref,
        decided_at=decided_at,
    )
    db.add(row)
    await db.flush()
    return row


def routing_decision_out(row: RoutingDecision, policy: RuntimePolicyVersion) -> RoutingDecisionOut:
    return RoutingDecisionOut(
        id=row.id,
        runtime_policy_version_id=row.runtime_policy_version_id,
        request_hash=row.request_hash,
        selected=row.selected,
        fallbacks=row.fallbacks,
        rejected=row.rejected,
        budget=row.budget,
        timeout_retry_policy=policy.timeout_retry_policy,
        overhead_ms=row.overhead_ms,
        overhead_target_met=row.overhead_ms < 25,
        decision_hash=row.decision_hash,
        decided_at=row.decided_at,
    )


def _cache_key_document(
    *,
    namespace: str,
    barrier_group: str | None,
    policy: RuntimePolicyVersion,
    version: AgentVersion,
    body: CacheAccessRequest,
) -> dict[str, Any]:
    return {
        "schema": "lians.runtime-cache-key.v1",
        "namespace_hash": hashlib.sha256(namespace.encode("utf-8")).hexdigest(),
        "barrier_scope": barrier_scope(barrier_group),
        "policy_hash": policy.policy_hash,
        "agent_manifest_hash": version.manifest_hash,
        "mode": body.mode,
        "provider": body.provider,
        "model": body.model,
        "request_hash": body.request_hash,
        "prompt_hash": body.prompt_hash,
        "tool_name_hash": (
            hashlib.sha256(body.tool_name.encode("utf-8")).hexdigest() if body.tool_name else None
        ),
        "tool_definition_hash": body.tool_definition_hash,
        "permission_scope_hash": sha256_json(body.permission_scopes),
        "release_reference_hash": (
            hashlib.sha256(body.release_reference.encode("utf-8")).hexdigest()
            if body.release_reference
            else None
        ),
    }


def _cache_storage_context(cache_key_hash: str) -> str:
    return cache_key_hash


async def access_runtime_cache(
    db: AsyncSession,
    *,
    policy: RuntimePolicyVersion,
    version: AgentVersion,
    namespace: str,
    barrier_group: str | None,
    principal_ref: str,
    body: CacheAccessRequest,
) -> tuple[CacheDecision, dict[str, Any] | None]:
    from .config import get_settings

    key_document = _cache_key_document(
        namespace=namespace,
        barrier_group=barrier_group,
        policy=policy,
        version=version,
        body=body,
    )
    cache_key_hash = sha256_json(key_document)
    redis_key = f"lians:runtime-cache:v1:{cache_key_hash}"
    reasons: list[str] = []
    payload: dict[str, Any] | None = None
    ttl_seconds = body.ttl_seconds
    cache_policy = policy.cache_policy
    allowed = body.mode in set(cache_policy.get("modes") or [])
    if not allowed:
        reasons.append("mode_not_enabled_by_policy")
    if body.consequential or not body.read_only:
        reasons.append("consequential_or_mutating_replay_forbidden")
    if ttl_seconds is not None and ttl_seconds > int(cache_policy.get("max_ttl_seconds", 300)):
        reasons.append("ttl_exceeds_policy")
    if cache_policy.get("semantic_cache_enabled"):
        raise RuntimeContractError("semantic caching cannot be enabled by this contract")
    if not get_settings().runtime_cache_enabled:
        reasons.append("runtime_cache_disabled")
    disposition: str
    if reasons:
        disposition = "bypass"
    else:
        try:
            redis = _get_runtime_redis()
            if body.operation == "lookup":
                encoded = await redis.get(redis_key)
                if encoded is None:
                    disposition = "miss"
                else:
                    envelope = json.loads(encoded)
                    if envelope.get("cache_key_hash") != cache_key_hash:
                        raise RuntimeError("runtime cache key envelope mismatch")
                    plaintext = unseal_text(
                        envelope["payload"],
                        purpose=_CACHE_PURPOSE,
                        context=_cache_storage_context(cache_key_hash),
                    )
                    payload = json.loads(plaintext)
                    if sha256_json(payload) != envelope.get("payload_hash"):
                        raise RuntimeError("runtime cache payload hash mismatch")
                    disposition = "hit"
            else:
                payload_hash = sha256_json(body.payload)
                envelope = {
                    "schema": "lians.runtime-cache-entry.v1",
                    "cache_key_hash": cache_key_hash,
                    "payload_hash": payload_hash,
                    "payload": seal_text(
                        json.dumps(body.payload, sort_keys=True, separators=(",", ":")),
                        purpose=_CACHE_PURPOSE,
                        context=_cache_storage_context(cache_key_hash),
                    ),
                }
                stored = await redis.setex(redis_key, ttl_seconds, json.dumps(envelope))
                if stored is not True and stored != "OK":
                    raise RuntimeError("runtime cache write was not acknowledged")
                disposition = "stored"
        except Exception as exc:
            logger.warning(
                "runtime cache operation unavailable",
                extra={
                    "error_digest": hashlib.sha256(
                        f"{type(exc).__module__}.{type(exc).__qualname__}".encode()
                    ).hexdigest()[:16]
                },
            )
            disposition = "unavailable"
            reasons.append("cache_backend_unavailable")
    row_id = uuid.uuid4()
    permission_scope_hash = key_document["permission_scope_hash"]
    decided_at = datetime.now(UTC)
    document = {
        "schema": "lians.cache-decision.v1",
        "id": str(row_id),
        "policy_hash": policy.policy_hash,
        "agent_manifest_hash": version.manifest_hash,
        "mode": body.mode,
        "operation": body.operation,
        "disposition": disposition,
        "cache_key_hash": cache_key_hash,
        "request_hash": body.request_hash,
        "permission_scope_hash": permission_scope_hash,
        "reason_codes": reasons,
        "ttl_seconds": ttl_seconds,
        "decided_at": decided_at,
    }
    row = CacheDecision(
        id=row_id,
        namespace=namespace,
        barrier_group=barrier_group,
        barrier_scope=barrier_scope(barrier_group),
        runtime_policy_version_id=policy.id,
        agent_version_id=version.id,
        mode=body.mode,
        operation=body.operation,
        disposition=disposition,
        cache_key_hash=cache_key_hash,
        request_hash=body.request_hash,
        permission_scope_hash=permission_scope_hash,
        release_reference_hash=key_document["release_reference_hash"],
        reason_codes=reasons,
        ttl_seconds=ttl_seconds,
        decision_hash=sha256_json(document),
        created_by_principal_ref=principal_ref,
        decided_at=decided_at,
    )
    db.add(row)
    await db.flush()
    return row, payload


def cache_decision_out(
    row: CacheDecision, *, payload: dict[str, Any] | None = None
) -> CacheDecisionOut:
    return CacheDecisionOut(
        id=row.id,
        mode=row.mode,
        operation=row.operation,
        disposition=row.disposition,
        cache_key_hash=row.cache_key_hash,
        request_hash=row.request_hash,
        permission_scope_hash=row.permission_scope_hash,
        reason_codes=row.reason_codes,
        ttl_seconds=row.ttl_seconds,
        payload=payload,
        semantic_replay=False,
        decision_hash=row.decision_hash,
        decided_at=row.decided_at,
    )


async def create_concurrency_plan(
    db: AsyncSession,
    *,
    namespace: str,
    barrier_group: str | None,
    principal_ref: str,
    body: ConcurrencyPlanRequest,
) -> ConcurrencyPlan:
    calls = {call.id: call for call in body.calls}
    remaining_dependencies = {call.id: set(call.depends_on) for call in body.calls}
    batches: list[list[str]] = []
    completed: set[str] = set()
    while len(completed) < len(calls):
        ready = sorted(
            call_id
            for call_id, dependencies in remaining_dependencies.items()
            if call_id not in completed and dependencies.issubset(completed)
        )
        if not ready:
            raise RuntimeContractError("tool dependency graph contains a cycle")
        read_only = [
            call_id
            for call_id in ready
            if calls[call_id].read_only and not calls[call_id].consequential
        ]
        mutating = [call_id for call_id in ready if call_id not in read_only]
        for index in range(0, len(read_only), body.max_parallelism):
            batch = read_only[index : index + body.max_parallelism]
            batches.append(batch)
            completed.update(batch)
        for call_id in mutating:
            batches.append([call_id])
            completed.add(call_id)
    row_id = uuid.uuid4()
    calls_document = [call.model_dump(mode="json") for call in body.calls]
    calls_hash = sha256_json(calls_document)
    document = {
        "schema": "lians.runtime-concurrency-plan.v1",
        "id": str(row_id),
        "agent_version_id": str(body.agent_version_id),
        "calls_hash": calls_hash,
        "max_parallelism": body.max_parallelism,
        "batches": batches,
    }
    row = ConcurrencyPlan(
        id=row_id,
        namespace=namespace,
        barrier_group=barrier_group,
        barrier_scope=barrier_scope(barrier_group),
        agent_version_id=body.agent_version_id,
        calls_hash=calls_hash,
        calls=calls_document,
        batches=batches,
        critical_path_depth=len(batches),
        plan_hash=sha256_json(document),
        created_by_principal_ref=principal_ref,
    )
    db.add(row)
    await db.flush()
    return row


def concurrency_plan_out(row: ConcurrencyPlan, calls: list[dict[str, Any]]) -> ConcurrencyPlanOut:
    call_by_id = {call["id"]: call for call in calls}
    return ConcurrencyPlanOut(
        id=row.id,
        agent_version_id=row.agent_version_id,
        batches=row.batches,
        critical_path_depth=row.critical_path_depth,
        parallel_call_count=sum(len(batch) for batch in row.batches if len(batch) > 1),
        serialized_consequential_call_count=sum(
            1
            for batch in row.batches
            if len(batch) == 1 and call_by_id.get(batch[0], {}).get("consequential")
        ),
        plan_hash=row.plan_hash,
        created_at=row.created_at,
    )


__all__ = [
    "RuntimeContractError",
    "access_runtime_cache",
    "cache_decision_out",
    "concurrency_plan_out",
    "create_concurrency_plan",
    "create_routing_decision",
    "create_runtime_policy",
    "routing_decision_out",
    "runtime_policy_out",
]
