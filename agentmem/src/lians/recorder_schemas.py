"""Public contracts for Universal Recorder ingestion and readiness APIs."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator

RecorderProtocol = Literal["lians", "otlp.genai", "mcp", "a2a"]
CaptureMode = Literal["metadata_only", "hash_only", "full"]


class RecorderActor(BaseModel):
    """Caller-reported actor claim, not authenticated ingestion identity."""

    model_config = ConfigDict(extra="forbid")

    agent_id: str | None = Field(None, min_length=1, max_length=255)
    principal_id: str | None = Field(None, min_length=1, max_length=512)
    roles: list[str] = Field(default_factory=list, max_length=100)
    authentication_context: dict[str, Any] = Field(default_factory=dict, max_length=100)
    extensions: dict[str, Any] = Field(default_factory=dict, max_length=256)


class RecorderCorrelation(BaseModel):
    """Optional identifiers used to join events across protocols and vendors."""

    model_config = ConfigDict(extra="forbid")

    run_id: str | None = Field(None, min_length=1, max_length=512)
    trace_id: str | None = Field(None, min_length=1, max_length=64)
    span_id: str | None = Field(None, min_length=1, max_length=64)
    parent_span_id: str | None = Field(None, min_length=1, max_length=64)
    session_id: str | None = Field(None, min_length=1, max_length=512)
    task_id: str | None = Field(None, min_length=1, max_length=512)
    context_id: str | None = Field(None, min_length=1, max_length=512)
    message_id: str | None = Field(None, min_length=1, max_length=512)
    tool_call_id: str | None = Field(None, min_length=1, max_length=512)
    decision_id: UUID | None = None
    extensions: dict[str, Any] = Field(default_factory=dict, max_length=256)


class RecorderCapturePolicy(BaseModel):
    """Per-envelope data-minimization policy.

    ``hash_only`` is the default: prompt, arguments, output, result, and content
    fields are replaced by deterministic SHA-256 references before persistence.
    Secret-like fields are always redacted, including when mode is ``full``.
    """

    model_config = ConfigDict(extra="forbid")

    mode: CaptureMode = "hash_only"
    sensitive_fields: list[str] = Field(default_factory=list, max_length=100)

    @field_validator("sensitive_fields")
    @classmethod
    def normalize_sensitive_fields(cls, values: list[str]) -> list[str]:
        return sorted({value.strip().casefold() for value in values if value.strip()})


class RecorderEnvelope(BaseModel):
    """Provider-neutral envelope accepted by every Recorder adapter."""

    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["0.1"] = "0.1"
    protocol: RecorderProtocol
    event_type: str | None = Field(None, min_length=1, max_length=128)
    event_id: str | None = Field(None, min_length=1, max_length=512)
    idempotency_key: str | None = Field(None, min_length=1, max_length=512)
    occurred_at: datetime | None = None
    subject_id: str | None = Field(None, min_length=1, max_length=512)
    actor: RecorderActor = Field(default_factory=RecorderActor)
    correlation: RecorderCorrelation = Field(default_factory=RecorderCorrelation)
    capture: RecorderCapturePolicy = Field(default_factory=RecorderCapturePolicy)
    payload: dict[str, Any] = Field(default_factory=dict, max_length=1000)
    extensions: dict[str, Any] = Field(default_factory=dict, max_length=256)


class RecorderBatchRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    events: list[RecorderEnvelope] = Field(min_length=1, max_length=500)
    atomic: bool = True


class RecorderEventOut(BaseModel):
    id: UUID
    run_id: UUID
    protocol: RecorderProtocol
    event_kind: str
    event_name: str | None
    phase: str
    status: str | None
    occurred_at: datetime
    recorded_at: datetime
    agent_id: str | None
    actor_attribution: Literal["claimed_unverified", "not_supplied"]
    ingested_by_principal_ref: str
    ingested_by_auth_method: str
    ingested_by_credential_id: str | None
    trace_id: str | None
    span_id: str | None
    task_id: str | None
    decision_id: UUID | None
    model_id: str | None
    input_hash: str | None
    output_hash: str | None
    capture_mode: CaptureMode
    capture_gaps: list[str]
    diagnostics: list[dict[str, Any]]
    event_hash: str
    event_hash_version: Literal[1, 2]


class RecorderRunReadiness(BaseModel):
    run_id: UUID
    correlation_type: str
    boundary_kind: Literal["run", "decision"]
    status: str
    event_count: int
    protocols: list[RecorderProtocol]
    score: int = Field(ge=0, le=100)
    receipt_ready: bool
    ready_at: datetime | None
    missing_fields: list[str]
    diagnostics: list[dict[str, Any]]
    first_event_at: datetime
    last_event_at: datetime
    time_to_readiness_ms: int | None


class RecorderIngestResult(BaseModel):
    accepted: bool
    duplicate: bool
    event: RecorderEventOut
    readiness: RecorderRunReadiness


class RecorderBatchRejection(BaseModel):
    index: int
    code: str
    detail: str


class RecorderBatchResult(BaseModel):
    received: int
    accepted: int
    duplicates: int
    rejected: int
    results: list[RecorderIngestResult]
    rejections: list[RecorderBatchRejection]
    ready_run_ids: list[UUID]


class FirstReceiptReadinessSummary(BaseModel):
    namespace: str
    evaluated_at: datetime
    total_runs: int
    ready_runs: int
    waiting_runs: int
    readiness_rate: float = Field(ge=0.0, le=1.0)
    first_ready_run_id: UUID | None
    first_ready_at: datetime | None
    next_actions: list[str]
    runs: list[RecorderRunReadiness]


class RecorderEvidenceIndexJobOut(BaseModel):
    """Truthful fixed-snapshot progress for deferred decision back-linking."""

    id: UUID
    decision_id: UUID
    status: Literal["pending", "running", "completed", "failed"]
    snapshot_max_recorded_at: datetime
    snapshot_max_event_id: UUID
    snapshot_event_count: int
    cursor_recorded_at: datetime | None
    cursor_event_id: UUID | None
    events_indexed: int
    events_remaining: int
    artifacts_created: int
    links_created: int
    pages_completed: int
    processing_attempts: int
    progress_ratio: float = Field(ge=0.0, le=1.0)
    complete: bool
    next_attempt_at: datetime
    last_error_code: str | None
    last_error_digest: str | None
    failure_code: str | None
    created_at: datetime
    started_at: datetime | None
    updated_at: datetime
    completed_at: datetime | None
    failed_at: datetime | None
