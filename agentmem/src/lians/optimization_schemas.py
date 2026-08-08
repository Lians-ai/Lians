"""API contracts for exact context compilation and tool optimization."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class TokenizerSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")

    engine: Literal["tiktoken", "tokenizers-json"]
    name: str = Field(min_length=1, max_length=255)
    definition: str | None = Field(default=None, min_length=2, max_length=8_000_000)

    @model_validator(mode="after")
    def definition_matches_engine(self):
        if self.engine == "tokenizers-json" and self.definition is None:
            raise ValueError("tokenizers-json requires an immutable tokenizer definition")
        if self.engine == "tiktoken" and self.definition is not None:
            raise ValueError("tiktoken resolves the named installed encoding; omit definition")
        return self


class TraceableCompression(BaseModel):
    model_config = ConfigDict(extra="forbid")

    content: str = Field(min_length=1, max_length=1_000_000)
    method: Literal["extractive", "abstractive"]
    compressor_id: str = Field(min_length=1, max_length=255)
    compressor_version: str = Field(min_length=1, max_length=255)
    compressor_config_hash: str = Field(pattern=r"^[0-9a-f]{64}$")


class ContextItem(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str = Field(min_length=1, max_length=512)
    content: str = Field(min_length=1, max_length=1_000_000)
    content_hash: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    relevance: float = Field(default=0.5, ge=0, le=1)
    freshness: float = Field(default=0.5, ge=0, le=1)
    evidence_refs: list[str] = Field(min_length=1, max_length=100)
    contradicts: list[str] = Field(default_factory=list, max_length=100)
    mandatory: bool = False
    compression: TraceableCompression | None = None

    @field_validator("evidence_refs", "contradicts")
    @classmethod
    def normalize_refs(cls, values: list[str]) -> list[str]:
        normalized = sorted({value.strip() for value in values if value.strip()})
        if any(len(value) > 2048 for value in normalized):
            raise ValueError("context references cannot exceed 2048 characters")
        return normalized


class ContextCompileRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    provider: str = Field(min_length=1, max_length=64)
    model: str = Field(min_length=1, max_length=255)
    tokenizer: TokenizerSpec
    max_tokens: int = Field(ge=1, le=10_000_000)
    separator: str = Field(default="\n\n", max_length=100)
    items: list[ContextItem] = Field(min_length=1, max_length=10_000)

    @field_validator("items")
    @classmethod
    def unique_item_ids(cls, values: list[ContextItem]) -> list[ContextItem]:
        ids = [item.id for item in values]
        if len(ids) != len(set(ids)):
            raise ValueError("context item ids must be unique")
        known = set(ids)
        for item in values:
            unknown = set(item.contradicts) - known
            if unknown:
                raise ValueError(f"context item {item.id} contradicts unknown item ids")
        return values


class ContextLineageItem(BaseModel):
    id: str
    original_content_hash: str
    retained_content_hash: str
    evidence_refs: list[str]
    token_count: int
    compressed: bool
    compression: dict[str, Any] | None


class ExactTokenizerOut(BaseModel):
    engine: Literal["tiktoken", "tokenizers-json"]
    name: str
    definition_hash: str = Field(pattern=r"^[0-9a-f]{64}$")


class ContextCompileOut(BaseModel):
    id: UUID
    provider: str
    model: str
    tokenizer: ExactTokenizerOut
    tokenizer_hash: str
    exact_token_count: Literal[True]
    max_tokens: int
    original_tokens: int
    compiled_tokens: int
    token_reduction: int
    reduction_ratio: float
    compiled_context: str
    compiled_context_hash: str
    lineage: list[ContextLineageItem]
    analysis: dict[str, Any]
    bundle_hash: str


class ToolDefinition(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=255, pattern=r"^[A-Za-z0-9][A-Za-z0-9._:-]*$")
    description: str = Field(min_length=1, max_length=4000)
    input_schema: dict[str, Any] = Field(default_factory=dict, max_length=1000)
    capabilities: list[str] = Field(default_factory=list, max_length=100)
    required_permission_scopes: list[str] = Field(default_factory=list, max_length=100)
    read_only: bool = True
    consequential: bool = False

    @field_validator("capabilities", "required_permission_scopes")
    @classmethod
    def normalize_labels(cls, values: list[str]) -> list[str]:
        return sorted({value.strip().casefold() for value in values if value.strip()})

    @model_validator(mode="after")
    def consequential_is_not_read_only(self):
        if self.consequential and self.read_only:
            raise ValueError("a consequential tool cannot be declared read-only")
        return self


class ToolRegistryCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=255)
    version: str = Field(min_length=1, max_length=255)
    tools: list[ToolDefinition] = Field(min_length=1, max_length=1000)

    @field_validator("tools")
    @classmethod
    def unique_tool_names(cls, values: list[ToolDefinition]) -> list[ToolDefinition]:
        names = [tool.name for tool in values]
        if len(names) != len(set(names)):
            raise ValueError("tool names must be unique within a registry version")
        return values


class ToolRegistryOut(BaseModel):
    id: UUID
    namespace: str
    barrier_group: str | None
    name: str
    version: str
    tools: list[ToolDefinition]
    registry_hash: str
    created_by_principal_ref: str
    created_at: datetime


class ToolFailureObservation(BaseModel):
    model_config = ConfigDict(extra="forbid")

    tool_name: str = Field(min_length=1, max_length=255)
    error_code: str = Field(min_length=1, max_length=128)
    consecutive_count: int = Field(ge=1, le=1_000_000)


class ToolSelectRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    registry_version_id: UUID
    query: str = Field(min_length=1, max_length=100_000)
    required_capabilities: list[str] = Field(default_factory=list, max_length=100)
    granted_permission_scopes: list[str] = Field(default_factory=list, max_length=1000)
    allow_consequential: bool = False
    max_tools: int = Field(default=20, ge=1, le=1000)
    tokenizer: TokenizerSpec
    schema_token_budget: int = Field(ge=1, le=10_000_000)
    failed_loop_threshold: int = Field(default=3, ge=1, le=1000)
    recent_failures: list[ToolFailureObservation] = Field(default_factory=list, max_length=1000)

    @field_validator("required_capabilities", "granted_permission_scopes")
    @classmethod
    def normalize_query_labels(cls, values: list[str]) -> list[str]:
        return sorted({value.strip().casefold() for value in values if value.strip()})


class SelectedTool(BaseModel):
    name: str
    relevance_score: float
    selection_reasons: list[str]
    slimmed_input_schema: dict[str, Any]
    original_schema_hash: str
    slimmed_schema_hash: str
    schema_tokens: int
    consequential: bool


class ToolSelectOut(BaseModel):
    id: UUID
    registry_version_id: UUID
    registry_hash: str
    selected_tools: list[SelectedTool]
    excluded_tools: list[dict[str, Any]]
    failed_loops: list[dict[str, Any]]
    selected_schema_tokens: int
    schema_token_budget: int
    tokenizer_hash: str
    exact_token_count: Literal[True]
    advisory_only: Literal[True]
    selection_hash: str


__all__ = [name for name in globals() if not name.startswith("_")]
