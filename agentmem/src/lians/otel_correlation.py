"""Convert accepted OTLP GenAI traces into idempotent decision records."""
from __future__ import annotations

import hashlib
import json
import uuid
from datetime import datetime, timezone
from typing import Any, Iterable

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from .audit_chain import chain_log
from .models import DecisionRecord, LedgerEvent, Memory
from .otel_contract import (
    CAPTURE_STATUS,
    CAPTURE_STATUSES,
    DECISION_ID,
    DECISION_OUTCOME,
    DECISION_TYPE,
    EVIDENCE_IDS,
    GRAFANA_TRACE_URL,
    KNOWLEDGE_AS_OF,
    MEMORY_IDS,
    POLICY_VERSION,
    WORKFLOW_ID,
    WORKSPACE_ID,
)


def _canonical(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), default=str)


def _timestamp(nanos: str) -> datetime:
    try:
        return datetime.fromtimestamp(int(nanos) / 1_000_000_000, tz=timezone.utc)
    except (TypeError, ValueError, OSError):
        return datetime.now(timezone.utc)


def _datetime(value: Any, fallback: datetime) -> datetime:
    if not value:
        return fallback
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
    except ValueError:
        return fallback


def _string_list(value: Any) -> list[str]:
    if isinstance(value, list):
        return [str(item) for item in value]
    if isinstance(value, str):
        return [item.strip() for item in value.split(",") if item.strip()]
    return []


def _decision_uuid(namespace: str, trace_id: str, explicit: Any) -> uuid.UUID:
    if explicit:
        try:
            return uuid.UUID(str(explicit))
        except ValueError:
            pass
    return uuid.uuid5(uuid.NAMESPACE_URL, f"lians:{namespace}:otel:{trace_id}")


async def correlate_genai_trace(
    db: AsyncSession,
    *,
    namespace: str,
    barrier_group: str | None,
    spans: Iterable[Any],
) -> tuple[list[uuid.UUID], int]:
    """Create one decision per GenAI trace, returning IDs and created count."""
    by_trace: dict[str, list[Any]] = {}
    for span in spans:
        if span.is_genai:
            by_trace.setdefault(span.trace_id, []).append(span)

    result: list[uuid.UUID] = []
    created = 0
    for trace_id, trace_spans in by_trace.items():
        root = next(
            (span for span in trace_spans if not span.parent_span_id),
            min(trace_spans, key=lambda span: int(span.start_time_unix_nano or "0")),
        )
        attrs = dict(root.attributes or {})
        decision_id = _decision_uuid(namespace, trace_id, attrs.get(DECISION_ID))
        result.append(decision_id)
        if await db.get(DecisionRecord, decision_id):
            continue

        decided_at = _timestamp(root.end_time_unix_nano)
        knowledge_as_of = _datetime(attrs.get(KNOWLEDGE_AS_OF), decided_at)
        agent_id = str(
            attrs.get("gen_ai.agent.id")
            or attrs.get("gen_ai.agent.name")
            or root.service_name
            or "otel-agent"
        )
        raw_ids = _string_list(attrs.get(MEMORY_IDS)) + _string_list(attrs.get(EVIDENCE_IDS))
        candidate_ids: list[uuid.UUID] = []
        for value in raw_ids:
            try:
                candidate_ids.append(uuid.UUID(value))
            except ValueError:
                continue
        existing_ids: list[str] = []
        if candidate_ids:
            existing_ids = [
                str(value)
                for value in (
                    await db.execute(
                        select(Memory.id).where(
                            Memory.namespace == namespace,
                            Memory.id.in_(candidate_ids),
                        )
                    )
                ).scalars()
            ]

        capture_status = str(attrs.get(CAPTURE_STATUS) or "partial")
        if capture_status not in CAPTURE_STATUSES:
            capture_status = "unverifiable"
        metadata = {
            "source": "opentelemetry",
            "trace_id": trace_id,
            "root_span_id": root.span_id,
            "span_count": len(trace_spans),
            "capture_status": capture_status,
            "workflow_id": attrs.get(WORKFLOW_ID),
            "workspace_id": attrs.get(WORKSPACE_ID),
            "grafana_trace_url": attrs.get(GRAFANA_TRACE_URL),
            "unresolved_evidence_ids": sorted(set(raw_ids) - set(existing_ids)),
        }
        body = {
            "id": str(decision_id),
            "namespace": namespace,
            "agent_id": agent_id,
            "decision_type": str(
                attrs.get(DECISION_TYPE) or attrs.get("gen_ai.operation.name") or root.name
            )[:100],
            "outcome": str(attrs.get(DECISION_OUTCOME) or "observed")[:500],
            "decided_at": decided_at.isoformat(),
            "knowledge_as_of": knowledge_as_of.isoformat(),
            "evidence_memory_ids": existing_ids,
            "metadata": metadata,
        }
        record_hash = hashlib.sha256(_canonical(body).encode()).hexdigest()
        decision = DecisionRecord(
            id=decision_id,
            namespace=namespace,
            agent_id=agent_id,
            barrier_group=barrier_group,
            decision_type=body["decision_type"],
            outcome=body["outcome"],
            reason_codes=["otel_observed"],
            session_id=attrs.get("gen_ai.conversation.id"),
            model_id=root.model_id,
            model_version=root.model_version,
            policy_version=attrs.get(POLICY_VERSION),
            decided_at=decided_at,
            recorded_at=datetime.now(timezone.utc),
            knowledge_as_of=knowledge_as_of,
            evidence_memory_ids=existing_ids,
            metadata_=metadata,
            record_hash=record_hash,
        )
        db.add(decision)
        created += 1
        db.add(
            LedgerEvent(
                namespace=namespace,
                event_type="inference",
                agent_id=agent_id,
                barrier_group=barrier_group,
                occurred_at=decided_at,
                decision_id=decision_id,
                model_id=root.model_id,
                model_version=root.model_version,
                payload={
                    "trace_id": trace_id,
                    "span_ids": [span.span_id for span in trace_spans],
                    "capture_status": capture_status,
                },
                artifact_hash=root.payload_hash,
                event_hash=hashlib.sha256(
                    _canonical({"decision_id": decision_id, "trace_id": trace_id}).encode()
                ).hexdigest(),
            )
        )
        await db.flush()
        await chain_log(
            db,
            namespace,
            agent_id,
            "decision_recorded_from_otel",
            content_hash=record_hash,
            payload={"decision_id": str(decision_id), "trace_id": trace_id},
        )
    return result, created
