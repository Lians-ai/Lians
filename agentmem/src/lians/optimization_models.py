"""Immutable records for exact context compilation and tool shortlisting."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from sqlalchemy import (
    JSON,
    CheckConstraint,
    Column,
    DateTime,
    ForeignKeyConstraint,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import UUID

from .db import Base


def _now() -> datetime:
    return datetime.now(UTC)


class ContextBundle(Base):
    """Exact-token, lineage-preserving compiled context artifact."""

    __tablename__ = "context_bundles"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    namespace = Column(String(255), nullable=False)
    barrier_group = Column(String(255), nullable=True)
    barrier_scope = Column(String(64), nullable=False)
    provider = Column(String(64), nullable=False)
    model = Column(String(255), nullable=False)
    tokenizer_engine = Column(String(32), nullable=False)
    tokenizer_name = Column(String(255), nullable=False)
    tokenizer_hash = Column(String(64), nullable=False)
    max_tokens = Column(Integer, nullable=False)
    original_tokens = Column(Integer, nullable=False)
    compiled_tokens = Column(Integer, nullable=False)
    compiled_context_encrypted = Column(Text, nullable=False)
    compiled_context_hash = Column(String(64), nullable=False)
    lineage = Column(JSON, nullable=False)
    analysis = Column(JSON, nullable=False)
    bundle_hash = Column(String(64), nullable=False)
    created_by_principal_ref = Column(String(512), nullable=False)
    created_at = Column(DateTime(timezone=True), nullable=False, default=_now)

    __table_args__ = (
        UniqueConstraint("id", "namespace", name="uq_context_bundle_id_namespace"),
        UniqueConstraint(
            "namespace", "barrier_scope", "bundle_hash", name="uq_context_bundle_scope_hash"
        ),
        Index("ix_context_bundle_scope_page", "namespace", "barrier_group", "created_at", "id"),
        CheckConstraint(
            "tokenizer_engine IN ('tiktoken','tokenizers-json')",
            name="ck_context_bundle_tokenizer_engine",
        ),
        CheckConstraint(
            "max_tokens > 0 AND original_tokens >= 0 AND compiled_tokens >= 0 "
            "AND compiled_tokens <= max_tokens",
            name="ck_context_bundle_token_counts",
        ),
        CheckConstraint(
            "length(tokenizer_hash) = 64 AND length(compiled_context_hash) = 64 "
            "AND length(bundle_hash) = 64",
            name="ck_context_bundle_hashes",
        ),
        CheckConstraint(
            "compiled_context_encrypted LIKE 'lians-sealed:v1:%' OR "
            "compiled_context_encrypted LIKE 'lians-sealed:v2:%'",
            name="ck_context_bundle_sealed",
        ),
    )


class ToolRegistryVersion(Base):
    """Immutable registry of available tools and permission requirements."""

    __tablename__ = "tool_registry_versions"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    namespace = Column(String(255), nullable=False)
    barrier_group = Column(String(255), nullable=True)
    barrier_scope = Column(String(64), nullable=False)
    name = Column(String(255), nullable=False)
    version = Column(String(255), nullable=False)
    tools = Column(JSON, nullable=False)
    registry_hash = Column(String(64), nullable=False)
    created_by_principal_ref = Column(String(512), nullable=False)
    created_at = Column(DateTime(timezone=True), nullable=False, default=_now)

    __table_args__ = (
        UniqueConstraint("id", "namespace", name="uq_tool_registry_id_namespace"),
        UniqueConstraint(
            "namespace",
            "barrier_scope",
            "name",
            "version",
            name="uq_tool_registry_scope_name_version",
        ),
        UniqueConstraint(
            "namespace",
            "barrier_scope",
            "registry_hash",
            name="uq_tool_registry_scope_hash",
        ),
        Index("ix_tool_registry_scope_page", "namespace", "barrier_group", "created_at", "id"),
        CheckConstraint("length(registry_hash) = 64", name="ck_tool_registry_hash"),
    )


class ToolSelectionDecision(Base):
    """Immutable, advisory shortlist and schema-slimming decision."""

    __tablename__ = "tool_selection_decisions"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    namespace = Column(String(255), nullable=False)
    barrier_group = Column(String(255), nullable=True)
    barrier_scope = Column(String(64), nullable=False)
    registry_version_id = Column(UUID(as_uuid=True), nullable=False)
    query_hash = Column(String(64), nullable=False)
    tokenizer = Column(JSON, nullable=False)
    token_budget = Column(Integer, nullable=False)
    selected_tools = Column(JSON, nullable=False)
    excluded_tools = Column(JSON, nullable=False)
    failed_loops = Column(JSON, nullable=False)
    selected_schema_tokens = Column(Integer, nullable=False)
    selection_hash = Column(String(64), nullable=False)
    created_by_principal_ref = Column(String(512), nullable=False)
    created_at = Column(DateTime(timezone=True), nullable=False, default=_now)

    __table_args__ = (
        ForeignKeyConstraint(
            ["registry_version_id", "namespace"],
            ["tool_registry_versions.id", "tool_registry_versions.namespace"],
            ondelete="RESTRICT",
            name="fk_tool_selection_registry_namespace",
        ),
        UniqueConstraint("id", "namespace", name="uq_tool_selection_id_namespace"),
        UniqueConstraint(
            "namespace",
            "barrier_scope",
            "selection_hash",
            name="uq_tool_selection_scope_hash",
        ),
        Index("ix_tool_selection_scope_page", "namespace", "barrier_group", "created_at", "id"),
        CheckConstraint(
            "token_budget > 0 AND selected_schema_tokens >= 0 "
            "AND selected_schema_tokens <= token_budget",
            name="ck_tool_selection_token_budget",
        ),
        CheckConstraint(
            "length(query_hash) = 64 AND length(selection_hash) = 64",
            name="ck_tool_selection_hashes",
        ),
    )


__all__ = ["ContextBundle", "ToolRegistryVersion", "ToolSelectionDecision"]
