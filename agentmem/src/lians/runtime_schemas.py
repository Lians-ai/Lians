"""Contracts for constrained routing, exact caches, budgets, and tool concurrency."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class RoutingObjective(BaseModel):
    model_config = ConfigDict(extra="forbid")

    quality_metric: str = Field(min_length=1, max_length=255)
    latency_metric: str | None = Field(default=None, min_length=1, max_length=255)
    cost_metric: str | None = Field(default=None, min_length=1, max_length=255)
    latency_weight: float = Field(default=0.5, ge=0, le=1)
    cost_weight: float = Field(default=0.5, ge=0, le=1)

    @model_validator(mode="after")
    def normalized_weights(self):
        if self.latency_metric is None and self.cost_metric is None:
            raise ValueError("at least one runtime optimization metric is required")
        active = (self.latency_weight if self.latency_metric else 0) + (
            self.cost_weight if self.cost_metric else 0
        )
        if active <= 0:
            raise ValueError("active runtime objective weights must be positive")
        return self


class RequestBudget(BaseModel):
    model_config = ConfigDict(extra="forbid")

    max_input_tokens: int = Field(ge=1, le=10_000_000)
    max_output_tokens: int = Field(ge=1, le=10_000_000)
    max_cost: float | None = Field(default=None, ge=0)
    currency: str | None = Field(default=None, pattern=r"^[A-Z]{3}$")
    deadline_ms: int = Field(ge=1, le=3_600_000)

    @model_validator(mode="after")
    def cost_currency_pair(self):
        if (self.max_cost is None) != (self.currency is None):
            raise ValueError("max_cost and currency must be supplied together")
        return self


class TimeoutRetryPolicy(BaseModel):
    model_config = ConfigDict(extra="forbid")

    attempt_timeout_ms: int = Field(ge=1, le=3_600_000)
    max_attempts: int = Field(default=1, ge=1, le=10)
    retry_on: list[str] = Field(default_factory=list, max_length=100)
    total_retry_budget_ms: int = Field(default=0, ge=0, le=3_600_000)


class FallbackPolicy(BaseModel):
    model_config = ConfigDict(extra="forbid")

    enabled: bool = True
    max_fallbacks: int = Field(default=2, ge=0, le=10)
    allow_quality_degradation: bool = False


class RuntimeCachePolicy(BaseModel):
    model_config = ConfigDict(extra="forbid")

    modes: list[Literal["exact_response", "provider_prompt", "tool_result"]] = Field(
        default_factory=list, max_length=3
    )
    max_ttl_seconds: int = Field(default=300, ge=1, le=86_400)
    semantic_cache_enabled: Literal[False] = False


class RuntimePolicyCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=255)
    version: str = Field(min_length=1, max_length=255)
    quality_floor: float = Field(ge=0, le=1)
    objective: RoutingObjective
    request_budget: RequestBudget
    timeout_retry_policy: TimeoutRetryPolicy
    fallback_policy: FallbackPolicy = Field(default_factory=FallbackPolicy)
    cache_policy: RuntimeCachePolicy = Field(default_factory=RuntimeCachePolicy)


class RuntimePolicyOut(RuntimePolicyCreate):
    id: UUID
    namespace: str
    barrier_group: str | None
    policy_hash: str
    created_by_principal_ref: str
    created_at: datetime


class RoutingCandidate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    agent_version_id: UUID
    evaluation_attestation_id: UUID
    provider: str = Field(min_length=1, max_length=64)
    model: str = Field(min_length=1, max_length=255)
    available: bool = True


class RouteDecideRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    runtime_policy_version_id: UUID
    input_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    input_tokens: int = Field(ge=0, le=10_000_000)
    requested_output_tokens: int = Field(ge=1, le=10_000_000)
    candidates: list[RoutingCandidate] = Field(min_length=1, max_length=100)

    @field_validator("candidates")
    @classmethod
    def unique_candidates(cls, values: list[RoutingCandidate]):
        ids = [candidate.agent_version_id for candidate in values]
        if len(ids) != len(set(ids)):
            raise ValueError("routing candidate agent versions must be unique")
        return values


class RoutedCandidateOut(BaseModel):
    agent_version_id: UUID
    evaluation_attestation_id: UUID
    provider: str
    model: str
    quality: float
    predicted_latency: float | None
    predicted_cost: float | None
    evaluation_attestation_hash: str


class RoutingDecisionOut(BaseModel):
    id: UUID
    runtime_policy_version_id: UUID
    request_hash: str
    selected: RoutedCandidateOut
    fallbacks: list[RoutedCandidateOut]
    rejected: list[dict[str, Any]]
    budget: RequestBudget
    timeout_retry_policy: TimeoutRetryPolicy
    overhead_ms: float
    overhead_target_ms: int = 25
    overhead_target_met: bool
    decision_hash: str
    decided_at: datetime


class CacheAccessRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    runtime_policy_version_id: UUID
    mode: Literal["exact_response", "provider_prompt", "tool_result"]
    operation: Literal["lookup", "store"]
    agent_version_id: UUID
    provider: str = Field(min_length=1, max_length=64)
    model: str = Field(min_length=1, max_length=255)
    request_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    prompt_hash: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    tool_name: str | None = Field(default=None, min_length=1, max_length=255)
    tool_definition_hash: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    permission_scopes: list[str] = Field(default_factory=list, max_length=1000)
    release_reference: str | None = Field(default=None, min_length=1, max_length=512)
    read_only: bool = True
    consequential: bool = False
    ttl_seconds: int | None = Field(default=None, ge=1, le=86_400)
    payload: dict[str, Any] | None = Field(default=None, max_length=1000)

    @field_validator("permission_scopes")
    @classmethod
    def normalized_permissions(cls, values: list[str]) -> list[str]:
        return sorted({value.strip() for value in values if value.strip()})

    @model_validator(mode="after")
    def operation_shape(self):
        if self.operation == "store" and (self.payload is None or self.ttl_seconds is None):
            raise ValueError("cache store requires payload and ttl_seconds")
        if self.operation == "lookup" and (
            self.payload is not None or self.ttl_seconds is not None
        ):
            raise ValueError("cache lookup cannot include payload or ttl_seconds")
        if self.mode == "provider_prompt" and self.prompt_hash is None:
            raise ValueError("provider_prompt cache requires prompt_hash")
        if self.mode == "tool_result" and (
            self.tool_name is None or self.tool_definition_hash is None
        ):
            raise ValueError("tool_result cache requires tool_name and tool_definition_hash")
        return self


class CacheDecisionOut(BaseModel):
    id: UUID
    mode: Literal["exact_response", "provider_prompt", "tool_result"]
    operation: Literal["lookup", "store"]
    disposition: Literal["hit", "miss", "stored", "bypass", "unavailable"]
    cache_key_hash: str
    request_hash: str
    permission_scope_hash: str
    reason_codes: list[str]
    ttl_seconds: int | None
    payload: dict[str, Any] | None = None
    semantic_replay: Literal[False] = False
    decision_hash: str
    decided_at: datetime


class ToolCallNode(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str = Field(min_length=1, max_length=512)
    tool_name: str = Field(min_length=1, max_length=255)
    depends_on: list[str] = Field(default_factory=list, max_length=100)
    read_only: bool = True
    consequential: bool = False

    @model_validator(mode="after")
    def consequential_not_read_only(self):
        if self.consequential and self.read_only:
            raise ValueError("consequential calls cannot be declared read-only")
        return self


class ConcurrencyPlanRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    agent_version_id: UUID
    calls: list[ToolCallNode] = Field(min_length=1, max_length=1000)
    max_parallelism: int = Field(default=8, ge=1, le=100)

    @field_validator("calls")
    @classmethod
    def valid_graph(cls, values: list[ToolCallNode]):
        ids = [call.id for call in values]
        if len(ids) != len(set(ids)):
            raise ValueError("tool call ids must be unique")
        known = set(ids)
        for call in values:
            if call.id in call.depends_on:
                raise ValueError("a tool call cannot depend on itself")
            if set(call.depends_on) - known:
                raise ValueError(f"tool call {call.id} has unknown dependencies")
        return values


class ConcurrencyPlanOut(BaseModel):
    id: UUID
    agent_version_id: UUID
    batches: list[list[str]]
    critical_path_depth: int
    parallel_call_count: int
    serialized_consequential_call_count: int
    plan_hash: str
    created_at: datetime


__all__ = [name for name in globals() if not name.startswith("_")]
