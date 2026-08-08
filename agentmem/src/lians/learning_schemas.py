"""Contracts for outcomes, corrections, incidents, drift, and learning queues."""

from __future__ import annotations

import math
from datetime import datetime
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

MetricProvenance = Literal[
    "provider-reported",
    "workload-reported",
    "client-measured",
    "deterministic",
    "human-authored",
    "model-judged",
    "external",
    "estimated",
]


class OutcomeMetric(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=255)
    value: float
    unit: str = Field(min_length=1, max_length=64)
    provenance: MetricProvenance

    @field_validator("value")
    @classmethod
    def finite_value(cls, value: float):
        if not math.isfinite(value):
            raise ValueError("outcome metrics must be finite")
        return value


class OutcomeCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    agent_version_id: UUID
    decision_id: UUID | None = None
    deployment_id: UUID | None = None
    correlation_id: str = Field(min_length=1, max_length=512)
    kind: Literal["success", "failure", "correction", "dispute", "override", "incident", "business"]
    metrics: list[OutcomeMetric] = Field(min_length=1, max_length=256)
    payload: dict[str, Any] | None = Field(default=None, max_length=1000)
    occurred_at: datetime

    @field_validator("metrics")
    @classmethod
    def unique_metrics(cls, values: list[OutcomeMetric]):
        names = [metric.name for metric in values]
        if len(names) != len(set(names)):
            raise ValueError("outcome metric names must be unique")
        return values


class OutcomeOut(BaseModel):
    id: UUID
    agent_version_id: UUID
    decision_id: UUID | None
    deployment_id: UUID | None
    correlation_hash: str
    kind: str
    metrics: list[OutcomeMetric]
    payload: dict[str, Any] | None
    payload_hash: str | None
    outcome_hash: str
    occurred_at: datetime
    recorded_at: datetime
    recorded_by_principal_ref: str


class FeedbackCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    agent_version_id: UUID
    outcome_id: UUID | None = None
    decision_id: UUID | None = None
    decision_receipt_hash: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    kind: Literal["correction", "dispute", "human_override", "incident", "rating", "comment"]
    payload: dict[str, Any] = Field(min_length=1, max_length=1000)
    auto_create_eval_case: bool = True

    @model_validator(mode="after")
    def eval_case_source_pair(self):
        if (self.decision_id is None) != (self.decision_receipt_hash is None):
            raise ValueError("decision_id and decision_receipt_hash must be supplied together")
        if (
            self.auto_create_eval_case
            and self.kind in {"correction", "dispute", "human_override", "incident"}
            and self.decision_id is None
        ):
            raise ValueError("automatic regression cases require a decision and receipt hash")
        return self


class FeedbackOut(BaseModel):
    id: UUID
    agent_version_id: UUID
    outcome_id: UUID | None
    decision_id: UUID | None
    decision_receipt_hash: str | None
    kind: str
    payload: dict[str, Any]
    payload_hash: str
    generated_eval_case_id: UUID | None
    feedback_hash: str
    authored_by_principal_ref: str
    authored_at: datetime


class FeedbackCreateOut(BaseModel):
    feedback: FeedbackOut
    learning_proposal: "LearningProposalOut"


class DriftAnalyzeRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    agent_version_id: UUID
    metric_name: str = Field(min_length=1, max_length=255)
    baseline_start: datetime
    baseline_end: datetime
    current_start: datetime
    current_end: datetime
    direction: Literal["increase", "decrease", "absolute"] = "absolute"
    threshold: float = Field(ge=0)
    max_samples_per_window: int = Field(default=10_000, ge=2, le=100_000)

    @model_validator(mode="after")
    def ordered_windows(self):
        if self.baseline_end <= self.baseline_start or self.current_end <= self.current_start:
            raise ValueError("drift windows must have positive duration")
        if self.current_start < self.baseline_end:
            raise ValueError("current drift window cannot overlap the baseline window")
        return self


class DriftSignalOut(BaseModel):
    id: UUID
    agent_version_id: UUID
    metric_name: str
    baseline: dict[str, Any]
    current: dict[str, Any]
    direction: str
    magnitude: float
    threshold: float
    drifted: bool
    method: Literal["two-window-mean-v1"]
    signal_hash: str
    detected_by_principal_ref: str
    detected_at: datetime


class LearningProposalOut(BaseModel):
    id: UUID
    agent_version_id: UUID
    source_feedback_id: UUID | None
    source_drift_signal_id: UUID | None
    eval_case_id: UUID | None
    proposal_type: str
    recommendation: dict[str, Any]
    priority: float
    status: Literal["awaiting_customer_approval"]
    proposal_hash: str
    created_by_principal_ref: str
    created_at: datetime


class DriftAnalysisOut(BaseModel):
    signal: DriftSignalOut
    learning_proposal: LearningProposalOut | None


__all__ = [name for name in globals() if not name.startswith("_")]
