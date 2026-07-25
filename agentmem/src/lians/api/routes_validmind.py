"""ValidMind custom-integration reference API."""
from __future__ import annotations

import hashlib
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, ConfigDict
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..db import get_db
from ..models import (
    Agent,
    ConflictFlag,
    DecisionRecord,
    OTelSpan,
    ValidMindModelLink,
)
from .deps import AuthContext, get_auth

router = APIRouter(prefix="/api/v1", tags=["validmind"])


class ValidMindUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    vm_cuid: str


def _external_id(kind: str, source_id: str) -> str:
    digest = hashlib.sha256(f"{kind}:{source_id}".encode()).hexdigest()[:20]
    return f"lians-{kind}-{digest}"


async def _model_records(db: AsyncSession, namespace: str) -> list[dict]:
    decisions = (
        await db.execute(
            select(DecisionRecord).where(
                DecisionRecord.namespace == namespace,
                DecisionRecord.model_id.is_not(None),
            )
        )
    ).scalars().all()
    spans = (
        await db.execute(
            select(OTelSpan).where(
                OTelSpan.namespace == namespace,
                OTelSpan.model_id.is_not(None),
            )
        )
    ).scalars().all()
    agents = (
        await db.execute(select(Agent).where(Agent.namespace == namespace))
    ).scalars().all()
    links = {
        row.external_id: row.vm_cuid
        for row in (
            await db.execute(
                select(ValidMindModelLink).where(
                    ValidMindModelLink.namespace == namespace
                )
            )
        ).scalars().all()
    }

    grouped: dict[str, dict] = {}
    for row in decisions:
        source_id = str(row.model_id)
        item = grouped.setdefault(
            source_id,
            {
                "versions": set(),
                "decision_count": 0,
                "span_count": 0,
                "created_at": row.recorded_at,
                "updated_at": row.recorded_at,
            },
        )
        if row.model_version:
            item["versions"].add(row.model_version)
        item["decision_count"] += 1
        item["created_at"] = min(item["created_at"], row.recorded_at)
        item["updated_at"] = max(item["updated_at"], row.recorded_at)
    for row in spans:
        source_id = str(row.model_id)
        item = grouped.setdefault(
            source_id,
            {
                "versions": set(),
                "decision_count": 0,
                "span_count": 0,
                "created_at": row.received_at,
                "updated_at": row.received_at,
            },
        )
        if row.model_version:
            item["versions"].add(row.model_version)
        item["span_count"] += 1
        item["created_at"] = min(item["created_at"], row.received_at)
        item["updated_at"] = max(item["updated_at"], row.received_at)

    records: list[dict] = []
    for source_id, item in grouped.items():
        external_id = _external_id("model", source_id)
        metadata = {
            "lians_model_id": source_id,
            "versions": sorted(item["versions"]),
            "decision_count": item["decision_count"],
            "genai_span_count": item["span_count"],
        }
        if external_id in links:
            metadata["vm_cuid"] = links[external_id]
        records.append(
            {
                "id": external_id,
                "name": source_id,
                "status": "active",
                "resource_type": "llm" if item["span_count"] else "ml_model",
                "metadata": metadata,
                "created_at": item["created_at"],
                "updated_at": item["updated_at"],
            }
        )
    for agent in agents:
        external_id = _external_id("agent", agent.agent_id)
        metadata = {"lians_agent_id": agent.agent_id, **dict(agent.config or {})}
        if external_id in links:
            metadata["vm_cuid"] = links[external_id]
        records.append(
            {
                "id": external_id,
                "name": agent.agent_id,
                "status": "active",
                "resource_type": "agent",
                "metadata": metadata,
                "created_at": agent.created_at,
                "updated_at": agent.created_at,
            }
        )
    return sorted(records, key=lambda item: (item["resource_type"], item["name"]))


@router.get("/health")
async def validmind_health(
    auth: AuthContext = Depends(get_auth),
):
    auth.require("read")
    auth.require_unbarriered()
    return {"status": "healthy"}


@router.get("/models")
async def list_validmind_models(
    resource_type: str | None = None,
    offset: int = Query(0, ge=0),
    limit: int = Query(1000, ge=1, le=5000),
    auth: AuthContext = Depends(get_auth),
    db: AsyncSession = Depends(get_db),
):
    auth.require("read")
    auth.require_unbarriered()
    records = await _model_records(db, auth.namespace)
    if resource_type:
        records = [item for item in records if item["resource_type"] == resource_type]
    return records[offset : offset + limit]


@router.get("/models/{external_id}")
async def get_validmind_model(
    external_id: str,
    auth: AuthContext = Depends(get_auth),
    db: AsyncSession = Depends(get_db),
):
    auth.require("read")
    auth.require_unbarriered()
    for item in await _model_records(db, auth.namespace):
        if item["id"] == external_id:
            return item
    raise HTTPException(status_code=404, detail="Model not found")


@router.put("/models/{external_id}")
async def update_validmind_model(
    external_id: str,
    req: ValidMindUpdate,
    auth: AuthContext = Depends(get_auth),
    db: AsyncSession = Depends(get_db),
):
    auth.require("write")
    auth.require_unbarriered()
    if not req.vm_cuid.strip():
        raise HTTPException(status_code=400, detail="vm_cuid must not be empty")
    known = {item["id"] for item in await _model_records(db, auth.namespace)}
    if external_id not in known:
        raise HTTPException(status_code=404, detail="Model not found")
    row = await db.get(ValidMindModelLink, (auth.namespace, external_id))
    if row is None:
        row = ValidMindModelLink(
            namespace=auth.namespace,
            external_id=external_id,
            vm_cuid=req.vm_cuid,
        )
        db.add(row)
    else:
        row.vm_cuid = req.vm_cuid
        row.updated_at = datetime.now(timezone.utc)
    await db.commit()
    return await get_validmind_model(external_id, auth, db)


def _ticket(row: ConflictFlag) -> dict:
    return {
        "id": str(row.id),
        "name": f"Memory conflict for agent {row.agent_id}",
        "title": f"Conflicting evidence for {row.agent_id}",
        "status": row.status,
        "resource_type": "finding",
        "metadata": {
            "agent_id": row.agent_id,
            "memory_a_id": str(row.memory_a_id),
            "memory_b_id": str(row.memory_b_id),
            "confidence": row.confidence,
            "resolver_note": row.resolver_note,
        },
        "created_at": row.detected_at,
        "updated_at": row.resolved_at or row.detected_at,
    }


@router.get("/tickets")
async def list_validmind_tickets(
    offset: int = Query(0, ge=0),
    limit: int = Query(1000, ge=1, le=5000),
    auth: AuthContext = Depends(get_auth),
    db: AsyncSession = Depends(get_db),
):
    auth.require("read")
    auth.require_unbarriered()
    rows = (
        await db.execute(
            select(ConflictFlag)
            .where(ConflictFlag.namespace == auth.namespace)
            .order_by(ConflictFlag.detected_at.desc())
            .offset(offset)
            .limit(limit)
        )
    ).scalars().all()
    return [_ticket(row) for row in rows]


@router.get("/tickets/{ticket_id}")
async def get_validmind_ticket(
    ticket_id: str,
    auth: AuthContext = Depends(get_auth),
    db: AsyncSession = Depends(get_db),
):
    auth.require("read")
    auth.require_unbarriered()
    try:
        import uuid
        parsed_id = uuid.UUID(ticket_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail="Ticket not found") from exc
    row = await db.get(ConflictFlag, parsed_id)
    if row is None or row.namespace != auth.namespace:
        raise HTTPException(status_code=404, detail="Ticket not found")
    return _ticket(row)


@router.get("/schema")
async def validmind_schema(auth: AuthContext = Depends(get_auth)):
    auth.require("read")
    auth.require_unbarriered()
    return {
        "models": {
            "id": {"type": "string", "required": True},
            "name": {"type": "string", "required": True},
            "status": {"type": "string"},
            "resource_type": {"type": "string"},
            "metadata": {"type": "object"},
            "created_at": {"type": "datetime"},
            "updated_at": {"type": "datetime"},
        },
        "tickets": {
            "id": {"type": "string", "required": True},
            "name": {"type": "string", "required": True},
            "status": {"type": "string"},
            "resource_type": {"type": "string"},
            "metadata": {"type": "object"},
            "created_at": {"type": "datetime"},
            "updated_at": {"type": "datetime"},
        },
    }


@router.get("/resource-types")
async def validmind_resource_types(auth: AuthContext = Depends(get_auth)):
    auth.require("read")
    auth.require_unbarriered()
    return [
        {"id": "ml_model", "name": "Machine-learning model"},
        {"id": "llm", "name": "Large language model"},
        {"id": "agent", "name": "AI agent"},
    ]
