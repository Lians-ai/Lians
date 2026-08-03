"""Convert accepted OTLP GenAI traces into idempotent decision records."""
from __future__ import annotations

import hashlib
import json
import uuid
from datetime import datetime, timezone
from typing import Any, Iterable

from sqlalchemy import or_, select, text
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import load_only

from .audit_chain import chain_log
from .decision_record_integrity import (
    DECISION_RECORD_HASH_VERSION,
    VERIFIED_INTEGRITY_STATUS,
    assert_decision_record_integrity,
    compute_decision_record_hash,
    decision_record_binding_payload,
)
from .evidence_service import decision_artifact_specs, index_decision_evidence
from .governance_service import reserve_namespace_usage
from .metering import enqueue_authoritative_decision_usage_event
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
from .recorder_service import index_recorder_evidence_for_decision


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
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(timezone.utc)
    except ValueError:
        return fallback


def _string_list(value: Any) -> list[str]:
    if isinstance(value, list):
        return [str(item) for item in value]
    if isinstance(value, str):
        return [item.strip() for item in value.split(",") if item.strip()]
    return []


def _optional_string(value: Any, *, max_length: int | None = None) -> str | None:
    if value is None:
        return None
    result = str(value)
    return result[:max_length] if max_length is not None else result


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
    recorded_by_principal_ref: str,
    recorded_by_auth_method: str,
    recorded_by_credential_ref: str,
    recorded_by_principal_type: str,
    recorded_by_role: str | None,
    recorded_by_scopes: list[str],
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
        if db.get_bind().dialect.name == "postgresql":
            # Deterministic trace IDs make retries idempotent only if concurrent
            # first writers serialize before the existence check.
            await db.execute(
                text("SELECT pg_advisory_xact_lock(hashtextextended(:key, 0))"),
                {"key": f"lians:otel-decision:{namespace}:{decision_id}"},
            )
        existing_decision = await db.get(DecisionRecord, decision_id)
        if existing_decision is not None:
            await assert_decision_record_integrity(db, existing_decision)
            continue

        # The OTLP request bytes are reserved once by the transport route; this
        # derived decision consumes only the decision-record quota.
        await reserve_namespace_usage(
            db,
            namespace=namespace,
            decision_records=1,
        )

        decided_at = _timestamp(root.end_time_unix_nano)
        knowledge_as_of = _datetime(attrs.get(KNOWLEDGE_AS_OF), decided_at)
        agent_id = str(
            attrs.get("gen_ai.agent.id")
            or attrs.get("gen_ai.agent.name")
            or root.service_name
            or "otel-agent"
        )[:255]
        raw_ids = _string_list(attrs.get(MEMORY_IDS)) + _string_list(attrs.get(EVIDENCE_IDS))
        candidate_ids: list[uuid.UUID] = []
        for value in raw_ids:
            try:
                candidate_ids.append(uuid.UUID(value))
            except ValueError:
                continue
        evidence_rows: list[Memory] = []
        existing_ids: list[str] = []
        if candidate_ids:
            evidence_filters = [
                Memory.namespace == namespace,
                Memory.id.in_(candidate_ids),
            ]
            if barrier_group is not None:
                evidence_filters.append(
                    or_(Memory.barrier_group.is_(None), Memory.barrier_group == barrier_group)
                )
            evidence_rows = list(
                (
                    await db.execute(
                        select(Memory)
                        .options(
                            load_only(
                                Memory.id,
                                Memory.source,
                                Memory.content_hash,
                                Memory.metadata_,
                                Memory.barrier_group,
                            )
                        )
                        .where(*evidence_filters)
                    )
                ).scalars()
            )
            existing_ids = sorted(str(row.id) for row in evidence_rows)

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
        recorded_at = datetime.now(timezone.utc)
        decision = DecisionRecord(
            id=decision_id,
            namespace=namespace,
            agent_id=agent_id,
            recorded_by_principal_ref=recorded_by_principal_ref,
            recorded_by_auth_method=recorded_by_auth_method,
            recorded_by_credential_ref=recorded_by_credential_ref,
            recorded_by_principal_type=recorded_by_principal_type,
            recorded_by_role=recorded_by_role,
            recorded_by_scopes=recorded_by_scopes,
            barrier_group=barrier_group,
            decision_type=str(
                attrs.get(DECISION_TYPE) or attrs.get("gen_ai.operation.name") or root.name
            )[:100],
            outcome=str(attrs.get(DECISION_OUTCOME) or "observed")[:500],
            reason_codes=["otel_observed"],
            session_id=_optional_string(attrs.get("gen_ai.conversation.id")),
            model_id=_optional_string(root.model_id),
            model_version=_optional_string(root.model_version),
            policy_version=_optional_string(attrs.get(POLICY_VERSION)),
            decided_at=decided_at,
            recorded_at=recorded_at,
            knowledge_as_of=knowledge_as_of,
            knowledge_recorded_as_of=recorded_at,
            evidence_memory_ids=existing_ids,
            metadata_=metadata,
            record_hash_version=DECISION_RECORD_HASH_VERSION,
            record_integrity_status=VERIFIED_INTEGRITY_STATUS,
            record_hash="",
        )
        decision.record_hash = compute_decision_record_hash(decision)
        evidence_candidate_plan = decision_artifact_specs(decision, evidence_rows)
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
        await index_decision_evidence(
            db,
            decision,
            evidence_rows,
            candidate_plan=evidence_candidate_plan,
        )
        await index_recorder_evidence_for_decision(db, decision)
        await chain_log(
            db,
            namespace,
            recorded_by_principal_ref,
            "decision_recorded",
            content_hash=decision.record_hash,
            payload=decision_record_binding_payload(decision),
        )
        await enqueue_authoritative_decision_usage_event(
            db,
            namespace=namespace,
            decision_id=decision.id,
            occurred_at=decision.recorded_at,
        )
    return result, created
