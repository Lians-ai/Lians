"""ValidMind custom-integration reference API."""
from __future__ import annotations

import hashlib
from collections import Counter, defaultdict
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, ConfigDict
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..db import get_db
from ..decision_evidence import assess_completeness, decision_out
from ..models import (
    Agent,
    ConflictFlag,
    DecisionEnvelope,
    DecisionEvidenceLink,
    DecisionRecord,
    OTelSpan,
    ValidMindModelLink,
)
from .deps import AuthContext, get_auth

router = APIRouter(prefix="/v1/integrations/validmind", tags=["validmind"])
legacy_router = APIRouter(
    prefix="/api/v1",
    tags=["validmind-legacy"],
    include_in_schema=False,
)


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


@legacy_router.get("/health")
@router.get("/health")
async def validmind_health(
    auth: AuthContext = Depends(get_auth),
):
    auth.require("read")
    auth.require_unbarriered()
    return {"status": "healthy"}


@legacy_router.get("/models")
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


@legacy_router.get("/models/{external_id}")
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


@legacy_router.put("/models/{external_id}")
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


@legacy_router.get("/tickets")
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


@legacy_router.get("/evidence-readiness")
@router.get("/evidence-readiness")
async def validmind_evidence_readiness(
    limit: int = Query(100, ge=1, le=1000),
    auth: AuthContext = Depends(get_auth),
    db: AsyncSession = Depends(get_db),
):
    """Expose honest decision-evidence grades for model-risk validation."""
    auth.require("read")
    auth.require_unbarriered()
    decisions = list(
        (
            await db.execute(
                select(DecisionRecord)
                .where(
                    DecisionRecord.namespace == auth.namespace,
                    DecisionRecord.envelope_id.is_not(None),
                )
                .order_by(DecisionRecord.decided_at.desc())
                .limit(limit)
            )
        )
        .scalars()
        .all()
    )
    envelope_ids = [row.envelope_id for row in decisions if row.envelope_id]
    envelopes = {
        row.id: row
        for row in (
            (
                await db.execute(
                    select(DecisionEnvelope).where(
                        DecisionEnvelope.namespace == auth.namespace,
                        DecisionEnvelope.id.in_(envelope_ids),
                    )
                )
            )
            .scalars()
            .all()
            if envelope_ids
            else []
        )
    }
    links_by_envelope: dict = defaultdict(list)
    if envelope_ids:
        links = (
            (
                await db.execute(
                    select(DecisionEvidenceLink).where(
                        DecisionEvidenceLink.namespace == auth.namespace,
                        DecisionEvidenceLink.envelope_id.in_(envelope_ids),
                    )
                )
            )
            .scalars()
            .all()
        )
        for link in links:
            links_by_envelope[link.envelope_id].append(link)

    records = []
    grade_counts: Counter[str] = Counter()
    gap_counts: Counter[str] = Counter()
    for decision in decisions:
        envelope = envelopes.get(decision.envelope_id)
        if envelope is None:
            continue
        assessment = assess_completeness(
            envelope, decision, links_by_envelope[envelope.id]
        )
        grade = assessment.grade or "unrecorded"
        grade_counts[grade] += 1
        for gap in assessment.gaps:
            gap_counts[gap.code] += 1
        records.append(
            {
                "id": f"lians-decision-{decision.id}",
                "resource_type": "decision_evidence",
                "decision": decision_out(decision).model_dump(mode="json"),
                "completeness": assessment.model_dump(mode="json"),
            }
        )
    reconstructable_or_better = sum(
        grade_counts[grade]
        for grade in ("reconstructable", "verifiable", "replayable")
    )
    verifiable_or_better = sum(
        grade_counts[grade] for grade in ("verifiable", "replayable")
    )
    total = len(records)
    return {
        "schema": "https://lians.ai/schemas/validmind/evidence-readiness/v1",
        "generated_at": datetime.now(timezone.utc),
        "summary": {
            "decisions": total,
            "grades": dict(grade_counts),
            "reconstructable_rate": (
                round(reconstructable_or_better / total, 6) if total else 0.0
            ),
            "verifiable_rate": (
                round(verifiable_or_better / total, 6) if total else 0.0
            ),
            "top_gaps": [
                {"code": code, "decisions": count}
                for code, count in gap_counts.most_common(10)
            ],
        },
        "records": records,
    }


@legacy_router.get("/tickets/{ticket_id}")
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


@legacy_router.get("/schema")
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


@legacy_router.get("/resource-types")
@router.get("/resource-types")
async def validmind_resource_types(auth: AuthContext = Depends(get_auth)):
    auth.require("read")
    auth.require_unbarriered()
    return [
        {"id": "ml_model", "name": "Machine-learning model"},
        {"id": "llm", "name": "Large language model"},
        {"id": "agent", "name": "AI agent"},
        {"id": "decision_evidence", "name": "Decision evidence record"},
    ]
