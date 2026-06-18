from __future__ import annotations
from datetime import datetime
from typing import Any, Optional
from uuid import UUID
from pydantic import BaseModel, Field


class MemoryAdd(BaseModel):
    agent_id: str
    content: str
    event_time: datetime
    source: Optional[str] = None
    subject_id: Optional[str] = None
    metadata: dict[str, Any] = Field(default_factory=dict)
    importance: float = Field(default=0.5, ge=0.0, le=1.0)


class MemoryOut(BaseModel):
    id: UUID
    namespace: str
    agent_id: str
    content: Optional[str]        # None if erased
    subject_id: Optional[str]
    event_time: datetime
    ingestion_time: datetime
    valid_from: datetime
    valid_to: Optional[datetime]
    superseded_by: Optional[UUID]
    supersession_confidence: Optional[float]
    importance: float
    source: Optional[str]
    content_hash: str
    erased_at: Optional[datetime]
    metadata: dict[str, Any]

    model_config = {"from_attributes": True}


class RecallRequest(BaseModel):
    agent_id: str
    query: str
    k: int = Field(default=5, ge=1, le=100)
    as_of: Optional[datetime] = None
    filters: dict[str, Any] = Field(default_factory=dict)


class RecallResult(BaseModel):
    memories: list[MemoryOut]
    as_of: Optional[datetime]
    total_candidates: int


class AuditReconstructRequest(BaseModel):
    agent_id: str
    as_of: datetime
    query: Optional[str] = None


class AuditReconstructResult(BaseModel):
    memories: list[MemoryOut]
    event_trail: list[dict[str, Any]]
    as_of: datetime


class EraseRequest(BaseModel):
    subject_id: str
    request_ref: str


class EraseResult(BaseModel):
    subject_id: str
    memories_erased: int
    request_ref: str


class ApiKeyCreate(BaseModel):
    namespace: str
    scopes: list[str] = Field(default=["read", "write"])
    label: Optional[str] = None


class ApiKeyOut(BaseModel):
    id: UUID
    namespace: str
    label: Optional[str]
    scopes: list[str]
    created_at: datetime
    rotated_at: Optional[datetime]
    revoked_at: Optional[datetime]

    model_config = {"from_attributes": True}


class ApiKeyCreated(ApiKeyOut):
    key: str  # plaintext raw key — returned ONCE at creation/rotation, never stored


class SupersessionResult(BaseModel):
    relation: str           # SUPERSEDES | CONFIRMS | ADDS | CONTRADICTS_SAME_TIME
    confidence: float
    superseded_ids: list[UUID] = Field(default_factory=list)
    rationale: Optional[str] = None
