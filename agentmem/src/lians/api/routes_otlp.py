"""Authenticated OTLP/HTTP trace ingestion."""

from __future__ import annotations

import gzip
import io
import uuid
from datetime import UTC, datetime

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from sqlalchemy import insert
from sqlalchemy.ext.asyncio import AsyncSession

from ..config import get_settings
from ..db import get_db
from ..decision_evidence import attach_otel_spans
from ..models import OTelSpan
from ..otlp import OtlpDecodeError, decode_trace_request
from .deps import AuthContext, get_auth

router = APIRouter(tags=["opentelemetry"])


def _request_body(body: bytes, content_encoding: str) -> bytes:
    """Decode standard OTLP compression without allowing decompression bombs."""
    encoding = content_encoding.strip().lower()
    if encoding in {"", "identity"}:
        return body
    if encoding != "gzip":
        raise HTTPException(status_code=415, detail=f"unsupported content encoding: {encoding}")
    maximum = get_settings().max_request_body_bytes
    try:
        with gzip.GzipFile(fileobj=io.BytesIO(body)) as stream:
            decoded = stream.read(maximum + 1)
    except (EOFError, OSError) as exc:
        raise HTTPException(status_code=400, detail="invalid gzip request body") from exc
    if len(decoded) > maximum:
        raise HTTPException(status_code=413, detail="decompressed request body too large")
    return decoded


@router.post(
    "/v1/traces",
    status_code=200,
    summary="Receive OTLP/HTTP traces",
    responses={200: {"description": "OTLP export accepted"}},
)
async def ingest_traces(
    request: Request,
    auth: AuthContext = Depends(get_auth),
    db: AsyncSession = Depends(get_db),
):
    """Accept every span in an OTLP export request without server-side sampling."""
    auth.require("write")
    content_type = request.headers.get("content-type", "").split(";", 1)[0].strip().lower()
    body = _request_body(await request.body(), request.headers.get("content-encoding", ""))
    try:
        spans = decode_trace_request(body, content_type)
    except OtlpDecodeError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    received_at = datetime.now(UTC)
    accepted = 0
    values = [
        {
            "id": uuid.uuid4(),
            "namespace": auth.namespace,
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

            stmt = (
                dialect_insert(OTelSpan)
                .values(chunk)
                .on_conflict_do_nothing(index_elements=["namespace", "trace_id", "span_id"])
            )
        elif dialect == "sqlite":
            from sqlalchemy.dialects.sqlite import insert as dialect_insert

            stmt = (
                dialect_insert(OTelSpan)
                .values(chunk)
                .on_conflict_do_nothing(index_elements=["namespace", "trace_id", "span_id"])
            )
        else:
            stmt = insert(OTelSpan).values(chunk)
        result = await db.execute(stmt.returning(OTelSpan.id))
        accepted += len(result.scalars().all())
    linked_evidence = await attach_otel_spans(
        db,
        auth.namespace,
        {item.trace_id for item in spans},
        auth.barrier_group,
    )
    await db.commit()

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
        "linkedDecisionEvidence": linked_evidence,
    }
