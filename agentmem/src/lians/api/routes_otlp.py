"""Authenticated OTLP/HTTP trace ingestion."""
from __future__ import annotations

import uuid
from dataclasses import replace
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from sqlalchemy import insert
from sqlalchemy.ext.asyncio import AsyncSession

from ..capture_privacy import canonical_json, capture_sha256, sanitize_capture
from ..config import get_settings
from ..db import get_db
from ..decision_record_integrity import (
    DecisionRecordIntegrityError,
    authenticated_recorder_authorization_snapshot,
    authenticated_recorder_provenance,
)
from ..evidence_service import DecisionEvidenceCapacityExceeded
from ..governance_service import reserve_namespace_usage
from ..metrics import record_otel_ingest
from ..models import OTelSpan
from ..otel_correlation import correlate_genai_trace
from ..otlp import OtlpDecodeError, decode_trace_request
from .deps import AuthContext, get_auth

router = APIRouter(tags=["opentelemetry"])


def _protect_span_capture(span):
    """Apply the deployment capture policy before persistence or correlation."""
    settings = get_settings()
    mode = settings.otlp_capture_mode.strip().lower()
    if mode == "full" and not settings.recorder_allow_full_capture:
        # Startup catches this in production; this also protects development
        # instances whose settings are changed without a process restart.
        raise HTTPException(
            status_code=503,
            detail="OTLP full capture is disabled by deployment policy",
        )
    try:
        resource_attributes = sanitize_capture(
            span.resource_attributes,
            mode=mode,
        )
        attributes = sanitize_capture(span.attributes, mode=mode)
        events = sanitize_capture(span.events, mode=mode)
        links = sanitize_capture(span.links, mode=mode)
        status_message_value = (
            sanitize_capture(span.status_message, mode=mode, field_name="content")
            if span.status_message is not None
            else None
        )
        status_message = (
            canonical_json(status_message_value)
            if isinstance(status_message_value, (dict, list))
            else status_message_value
        )
        protected = replace(
            span,
            resource_attributes=resource_attributes,
            attributes=attributes,
            events=events,
            links=links,
            status_message=status_message,
        )
        document = {key: value for key, value in protected.__dict__.items() if key != "payload_hash"}
        return replace(protected, payload_hash=capture_sha256(document))
    except (TypeError, ValueError) as exc:
        raise HTTPException(
            status_code=400,
            detail="OTLP span contains a non-canonical capture value",
        ) from exc


@router.post(
    "/v1/traces",
    status_code=200,
    summary="Receive OTLP/HTTP traces",
    responses={
        200: {"description": "OTLP export accepted"},
        413: {
            "description": (
                "The trace, GenAI-trace, or normalized decision-evidence "
                "capacity was exceeded; no spans are committed"
            )
        },
    },
)
async def ingest_traces(
    request: Request,
    auth: AuthContext = Depends(get_auth),
    db: AsyncSession = Depends(get_db),
):
    """Accept every span in an OTLP export request without server-side sampling."""
    auth.require("write")
    content_type = request.headers.get("content-type", "").split(";", 1)[0].strip().lower()
    request_body = await request.body()
    try:
        spans = [
            _protect_span_capture(span)
            for span in decode_trace_request(request_body, content_type)
        ]
    except OtlpDecodeError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    settings = get_settings()
    if len(spans) > settings.otlp_max_spans_per_request:
        raise HTTPException(
            status_code=413,
            detail={
                "code": "otlp_span_limit_exceeded",
                "spans_received": len(spans),
                "spans_limit": settings.otlp_max_spans_per_request,
                "spans_committed": False,
            },
        )
    genai_trace_count = len({span.trace_id for span in spans if span.is_genai})
    if genai_trace_count > settings.otlp_max_genai_traces_per_request:
        raise HTTPException(
            status_code=413,
            detail={
                "code": "otlp_genai_trace_limit_exceeded",
                "genai_traces_received": genai_trace_count,
                "genai_traces_limit": settings.otlp_max_genai_traces_per_request,
                "spans_committed": False,
            },
        )

    await reserve_namespace_usage(
        db,
        namespace=auth.namespace,
        recorder_events=len(spans),
        estimated_ingest_bytes=len(request_body),
        capture_modes=(settings.otlp_capture_mode.strip().lower(),),
    )

    received_at = datetime.now(timezone.utc)
    accepted = 0
    values = [
        {
            "id": uuid.uuid4(),
            "namespace": auth.namespace,
            "barrier_group": auth.barrier_group,
            "barrier_scope_trusted": True,
            "received_at": received_at,
            **item.__dict__,
        }
        for item in spans
    ]
    dialect = db.get_bind().dialect.name
    # Chunking avoids driver parameter limits for large collector batches.
    for start in range(0, len(values), 250):
        chunk = values[start : start + 250]
        if dialect == "postgresql":
            from sqlalchemy.dialects.postgresql import insert as dialect_insert
            stmt = dialect_insert(OTelSpan).values(chunk).on_conflict_do_nothing()
        elif dialect == "sqlite":
            from sqlalchemy.dialects.sqlite import insert as dialect_insert
            stmt = dialect_insert(OTelSpan).values(chunk).on_conflict_do_nothing()
        else:
            stmt = insert(OTelSpan).values(chunk)
        result = await db.execute(stmt.returning(OTelSpan.id))
        accepted += len(result.scalars().all())
    try:
        principal_ref, auth_method, credential_ref = authenticated_recorder_provenance(
            principal_ref=auth.principal_id,
            auth_method=auth.auth_method,
            credential_id=auth.credential_id,
        )
        principal_type, role, scopes = authenticated_recorder_authorization_snapshot(
            principal_type=auth.principal_type,
            role=auth.role,
            effective_scopes=auth.scopes,
        )
    except DecisionRecordIntegrityError as exc:
        raise HTTPException(401, "Authenticated recorder provenance is required") from exc
    try:
        decision_ids, decisions_created = await correlate_genai_trace(
            db,
            namespace=auth.namespace,
            barrier_group=auth.barrier_group,
            recorded_by_principal_ref=principal_ref,
            recorded_by_auth_method=auth_method,
            recorded_by_credential_ref=credential_ref,
            recorded_by_principal_type=principal_type,
            recorded_by_role=role,
            recorded_by_scopes=scopes,
            spans=spans,
        )
    except DecisionRecordIntegrityError as exc:
        raise HTTPException(
            409,
            detail={
                "code": "decision_record_integrity_verification_failed",
                "message": (
                    "An existing correlated decision failed authenticated integrity "
                    "verification"
                ),
            },
        ) from exc
    except DecisionEvidenceCapacityExceeded as exc:
        from ..metrics import record_decision_evidence_capacity_rejection

        record_decision_evidence_capacity_rejection(
            "otlp",
            count_exceeded=exc.candidate_count > exc.candidate_limit,
            bytes_exceeded=exc.candidate_bytes > exc.candidate_bytes_limit,
        )
        raise HTTPException(
            status_code=413,
            detail={
                "code": exc.code,
                "message": str(exc),
                "candidate_count_lower_bound": exc.candidate_count,
                "candidate_limit": exc.candidate_limit,
                "candidate_bytes_lower_bound": exc.candidate_bytes,
                "candidate_bytes_limit": exc.candidate_bytes_limit,
                "spans_committed": False,
            },
        ) from exc
    await db.commit()
    record_otel_ingest(auth.namespace, accepted, decisions_created)

    # OTLP specifies an empty success response. JSON is used for JSON requests;
    # protobuf clients receive the serialized empty ExportTraceServiceResponse.
    if "protobuf" in content_type:
        try:
            from opentelemetry.proto.collector.trace.v1.trace_service_pb2 import (
                ExportTraceServiceResponse,
            )
        except ImportError:
            return Response(status_code=200)
        return Response(
            ExportTraceServiceResponse().SerializeToString(),
            media_type="application/x-protobuf",
        )
    return {
        "partialSuccess": {},
        "acceptedSpans": accepted,
        "decisionIds": [str(value) for value in decision_ids],
    }
