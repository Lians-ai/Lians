"""One bounded enterprise view over memory, evidence, security, and operations."""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from .audit_chain import verify_chain
from .config import get_settings
from .models import (
    ConflictFlag,
    Connector,
    DecisionEvidenceLink,
    DecisionEnvelope,
    DecisionRecord,
    DurableJob,
    EventLog,
    Memory,
    NamespacePolicy,
    PendingAdmission,
)
from .schemas import ControlPlaneOverview


async def _count(db: AsyncSession, model, *conditions) -> int:
    return int((await db.execute(
        select(func.count()).select_from(model).where(*conditions)
    )).scalar_one())


async def control_plane_overview(
    db: AsyncSession,
    namespace: str,
    *,
    verify_audit: bool = False,
) -> ControlPlaneOverview:
    settings = get_settings()
    total_memories = await _count(db, Memory, Memory.namespace == namespace)
    active_memories = await _count(
        db, Memory, Memory.namespace == namespace, Memory.valid_to.is_(None), Memory.erased_at.is_(None)
    )
    historical_memories = await _count(
        db, Memory, Memory.namespace == namespace, Memory.valid_to.is_not(None)
    )
    erased_memories = await _count(
        db, Memory, Memory.namespace == namespace, Memory.erased_at.is_not(None)
    )
    open_conflicts = await _count(
        db, ConflictFlag, ConflictFlag.namespace == namespace, ConflictFlag.status == "open"
    )
    pending_admissions = await _count(
        db, PendingAdmission, PendingAdmission.namespace == namespace, PendingAdmission.status == "pending"
    )
    decisions = await _count(db, DecisionRecord, DecisionRecord.namespace == namespace)
    replayable = await _count(
        db,
        DecisionRecord,
        DecisionRecord.namespace == namespace,
        DecisionRecord.replay_manifest_hash.is_not(None),
    )
    human_reviewed = await _count(
        db,
        DecisionRecord,
        DecisionRecord.namespace == namespace,
        DecisionRecord.human_reviewed_at.is_not(None),
    )
    sealed_envelopes = await _count(
        db,
        DecisionEnvelope,
        DecisionEnvelope.namespace == namespace,
        DecisionEnvelope.status == "sealed",
    )
    evidence_links = await _count(
        db, DecisionEvidenceLink, DecisionEvidenceLink.namespace == namespace
    )
    audit_rows = await _count(db, EventLog, EventLog.namespace == namespace)

    job_counts = {
        status: await _count(
            db, DurableJob, DurableJob.namespace == namespace, DurableJob.status == status
        )
        for status in ("pending", "leased", "dead", "completed")
    }
    connector_counts = {
        status: await _count(
            db, Connector, Connector.namespace == namespace, Connector.status == status
        )
        for status in ("active", "paused", "disabled")
    }
    retention = await db.get(NamespacePolicy, namespace)
    chain_status: dict[str, Any]
    if verify_audit:
        chain_status = await verify_chain(db, namespace)
    else:
        chain_status = {"status": "unchecked", "rows_checked": audit_rows, "violations": []}

    production = settings.deployment_environment.strip().lower() in {"prod", "production"}
    encrypted = bool(settings.master_encryption_key) or settings.kms_provider != "env"
    trusted_origins = settings.cors_origins.strip() != "*"
    production_ready = bool(
        production and encrypted and trusted_origins and settings.rls_barriers_enabled
    )

    attention: list[dict[str, Any]] = []
    if open_conflicts:
        attention.append({"severity": "high", "code": "open_conflicts", "count": open_conflicts})
    if pending_admissions:
        attention.append({"severity": "high", "code": "pending_admissions", "count": pending_admissions})
    if job_counts["dead"]:
        attention.append({"severity": "critical", "code": "dead_jobs", "count": job_counts["dead"]})
    if verify_audit and chain_status.get("status") != "ok":
        attention.append({"severity": "critical", "code": "audit_chain", "count": len(chain_status.get("violations", []))})
    if production and not production_ready:
        attention.append({"severity": "critical", "code": "production_posture", "count": 1})

    return ControlPlaneOverview(
        namespace=namespace,
        generated_at=datetime.now(timezone.utc),
        posture={
            "environment": settings.deployment_environment,
            "production_ready": production_ready,
            "encryption_configured": encrypted,
            "kms_provider": settings.kms_provider,
            "tenant_rls_enabled": settings.rls_barriers_enabled,
            "trusted_cors_origins": trusted_origins,
            "airgap_mode": settings.airgap_mode,
            "worm_mode": settings.worm_mode,
            "audit_chain": chain_status,
        },
        memory={
            "total": total_memories,
            "active": active_memories,
            "historical": historical_memories,
            "erased": erased_memories,
        },
        governance={
            "open_conflicts": open_conflicts,
            "pending_admissions": pending_admissions,
        },
        evidence={
            "decisions": decisions,
            "sealed_envelopes": sealed_envelopes,
            "evidence_links": evidence_links,
            "replayable_decisions": replayable,
            "human_reviewed_decisions": human_reviewed,
            "replayable_rate": round(replayable / decisions, 6) if decisions else 0.0,
            "human_review_rate": round(human_reviewed / decisions, 6) if decisions else 0.0,
        },
        operations={"jobs": job_counts, "connectors": connector_counts},
        retention={
            "content_ttl_days": retention.content_ttl_days if retention else None,
            "audit_retention_days": retention.audit_retention_days if retention else 1825,
            "legal_hold": retention.legal_hold if retention else False,
        },
        attention=attention,
    )
