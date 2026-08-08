"""Immutable routing, cache, budget, and concurrency decision records."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from sqlalchemy import (
    JSON,
    CheckConstraint,
    Column,
    DateTime,
    Float,
    ForeignKeyConstraint,
    Index,
    Integer,
    String,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import UUID

from .db import Base


def _now() -> datetime:
    return datetime.now(UTC)


class RuntimePolicyVersion(Base):
    """Immutable runtime constraints; it routes but never invokes a provider."""

    __tablename__ = "runtime_policy_versions"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    namespace = Column(String(255), nullable=False)
    barrier_group = Column(String(255), nullable=True)
    barrier_scope = Column(String(64), nullable=False)
    name = Column(String(255), nullable=False)
    version = Column(String(255), nullable=False)
    quality_floor = Column(Float, nullable=False)
    objective = Column(JSON, nullable=False)
    request_budget = Column(JSON, nullable=False)
    timeout_retry_policy = Column(JSON, nullable=False)
    fallback_policy = Column(JSON, nullable=False)
    cache_policy = Column(JSON, nullable=False)
    policy_hash = Column(String(64), nullable=False)
    created_by_principal_ref = Column(String(512), nullable=False)
    created_at = Column(DateTime(timezone=True), nullable=False, default=_now)

    __table_args__ = (
        UniqueConstraint("id", "namespace", name="uq_runtime_policy_id_namespace"),
        UniqueConstraint(
            "namespace",
            "barrier_scope",
            "name",
            "version",
            name="uq_runtime_policy_scope_name_version",
        ),
        UniqueConstraint(
            "namespace", "barrier_scope", "policy_hash", name="uq_runtime_policy_scope_hash"
        ),
        Index("ix_runtime_policy_scope_page", "namespace", "barrier_group", "created_at", "id"),
        CheckConstraint(
            "quality_floor >= 0 AND quality_floor <= 1", name="ck_runtime_policy_quality_floor"
        ),
        CheckConstraint("length(policy_hash) = 64", name="ck_runtime_policy_hash"),
    )


class RoutingDecision(Base):
    """Append-only constrained provider/model choice with measured overhead."""

    __tablename__ = "routing_decisions"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    namespace = Column(String(255), nullable=False)
    barrier_group = Column(String(255), nullable=True)
    barrier_scope = Column(String(64), nullable=False)
    runtime_policy_version_id = Column(UUID(as_uuid=True), nullable=False)
    agent_version_id = Column(UUID(as_uuid=True), nullable=False)
    request_hash = Column(String(64), nullable=False)
    selected = Column(JSON, nullable=False)
    fallbacks = Column(JSON, nullable=False)
    rejected = Column(JSON, nullable=False)
    budget = Column(JSON, nullable=False)
    overhead_ms = Column(Float, nullable=False)
    decision_hash = Column(String(64), nullable=False)
    created_by_principal_ref = Column(String(512), nullable=False)
    decided_at = Column(DateTime(timezone=True), nullable=False, default=_now)

    __table_args__ = (
        ForeignKeyConstraint(
            ["runtime_policy_version_id", "namespace"],
            ["runtime_policy_versions.id", "runtime_policy_versions.namespace"],
            ondelete="RESTRICT",
            name="fk_routing_decision_policy_namespace",
        ),
        ForeignKeyConstraint(
            ["agent_version_id", "namespace"],
            ["agent_versions.id", "agent_versions.namespace"],
            ondelete="RESTRICT",
            name="fk_routing_decision_agent_version_namespace",
        ),
        UniqueConstraint("id", "namespace", name="uq_routing_decision_id_namespace"),
        UniqueConstraint(
            "namespace", "barrier_scope", "decision_hash", name="uq_routing_decision_scope_hash"
        ),
        Index("ix_routing_decision_scope_page", "namespace", "barrier_group", "decided_at", "id"),
        CheckConstraint("overhead_ms >= 0", name="ck_routing_decision_overhead"),
        CheckConstraint(
            "length(request_hash) = 64 AND length(decision_hash) = 64",
            name="ck_routing_decision_hashes",
        ),
    )


class CacheDecision(Base):
    """Permission- and version-bound exact cache outcome; payload stays in Redis."""

    __tablename__ = "cache_decisions"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    namespace = Column(String(255), nullable=False)
    barrier_group = Column(String(255), nullable=True)
    barrier_scope = Column(String(64), nullable=False)
    runtime_policy_version_id = Column(UUID(as_uuid=True), nullable=False)
    agent_version_id = Column(UUID(as_uuid=True), nullable=False)
    mode = Column(String(32), nullable=False)
    operation = Column(String(16), nullable=False)
    disposition = Column(String(16), nullable=False)
    cache_key_hash = Column(String(64), nullable=False)
    request_hash = Column(String(64), nullable=False)
    permission_scope_hash = Column(String(64), nullable=False)
    release_reference_hash = Column(String(64), nullable=True)
    reason_codes = Column(JSON, nullable=False)
    ttl_seconds = Column(Integer, nullable=True)
    decision_hash = Column(String(64), nullable=False)
    created_by_principal_ref = Column(String(512), nullable=False)
    decided_at = Column(DateTime(timezone=True), nullable=False, default=_now)

    __table_args__ = (
        ForeignKeyConstraint(
            ["runtime_policy_version_id", "namespace"],
            ["runtime_policy_versions.id", "runtime_policy_versions.namespace"],
            ondelete="RESTRICT",
            name="fk_cache_decision_policy_namespace",
        ),
        ForeignKeyConstraint(
            ["agent_version_id", "namespace"],
            ["agent_versions.id", "agent_versions.namespace"],
            ondelete="RESTRICT",
            name="fk_cache_decision_agent_version_namespace",
        ),
        UniqueConstraint("id", "namespace", name="uq_cache_decision_id_namespace"),
        Index("ix_cache_decision_scope_page", "namespace", "barrier_group", "decided_at", "id"),
        CheckConstraint(
            "mode IN ('exact_response','provider_prompt','tool_result')",
            name="ck_cache_decision_mode",
        ),
        CheckConstraint("operation IN ('lookup','store')", name="ck_cache_decision_operation"),
        CheckConstraint(
            "disposition IN ('hit','miss','stored','bypass','unavailable')",
            name="ck_cache_decision_disposition",
        ),
        CheckConstraint(
            "ttl_seconds IS NULL OR ttl_seconds BETWEEN 1 AND 86400",
            name="ck_cache_decision_ttl",
        ),
        CheckConstraint(
            "length(cache_key_hash) = 64 AND length(request_hash) = 64 "
            "AND length(permission_scope_hash) = 64 AND length(decision_hash) = 64",
            name="ck_cache_decision_hashes",
        ),
    )


class ConcurrencyPlan(Base):
    """Dependency-derived tool batches; consequential calls remain serialized."""

    __tablename__ = "runtime_concurrency_plans"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    namespace = Column(String(255), nullable=False)
    barrier_group = Column(String(255), nullable=True)
    barrier_scope = Column(String(64), nullable=False)
    agent_version_id = Column(UUID(as_uuid=True), nullable=False)
    calls_hash = Column(String(64), nullable=False)
    calls = Column(JSON, nullable=False)
    batches = Column(JSON, nullable=False)
    critical_path_depth = Column(Integer, nullable=False)
    plan_hash = Column(String(64), nullable=False)
    created_by_principal_ref = Column(String(512), nullable=False)
    created_at = Column(DateTime(timezone=True), nullable=False, default=_now)

    __table_args__ = (
        ForeignKeyConstraint(
            ["agent_version_id", "namespace"],
            ["agent_versions.id", "agent_versions.namespace"],
            ondelete="RESTRICT",
            name="fk_concurrency_plan_agent_version_namespace",
        ),
        UniqueConstraint("id", "namespace", name="uq_concurrency_plan_id_namespace"),
        UniqueConstraint(
            "namespace", "barrier_scope", "plan_hash", name="uq_concurrency_plan_scope_hash"
        ),
        CheckConstraint("critical_path_depth > 0", name="ck_concurrency_plan_depth"),
        CheckConstraint(
            "length(calls_hash) = 64 AND length(plan_hash) = 64",
            name="ck_concurrency_plan_hashes",
        ),
    )


__all__ = ["CacheDecision", "ConcurrencyPlan", "RoutingDecision", "RuntimePolicyVersion"]
