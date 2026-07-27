"""Contract tests for the Grafana/OTLP and ValidMind integration surfaces."""
import hashlib

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import func, select

from src.lians.db import get_db
from src.lians.main import app
from src.lians.models import ApiKey, DecisionRecord, LedgerEvent, OTelSpan
from src.lians.otlp import decode_trace_request


KEY = "partner-integration-key"
NAMESPACE = "partner-test"


@pytest_asyncio.fixture
async def partner_client(db):
    db.add(
        ApiKey(
            hashed_key=hashlib.sha256(KEY.encode()).hexdigest(),
            namespace=NAMESPACE,
            scopes=["read", "write"],
        )
    )
    await db.commit()

    async def override_db():
        yield db

    app.dependency_overrides[get_db] = override_db
    try:
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            yield client, db
    finally:
        app.dependency_overrides.clear()


def _headers():
    return {"X-API-Key": KEY, "Content-Type": "application/json"}


def _otlp_payload():
    return {
        "resourceSpans": [
            {
                "resource": {
                    "attributes": [
                        {"key": "service.name", "value": {"stringValue": "checkout-agent"}}
                    ]
                },
                "scopeSpans": [
                    {
                        "scope": {"name": "openai.instrumentation", "version": "1.0"},
                        "spans": [
                            {
                                "traceId": "0123456789abcdef0123456789abcdef",
                                "spanId": "0123456789abcdef",
                                "name": "chat gpt-5",
                                "kind": 3,
                                "startTimeUnixNano": "1784900000000000000",
                                "endTimeUnixNano": "1784900001000000000",
                                "attributes": [
                                    {
                                        "key": "gen_ai.request.model",
                                        "value": {"stringValue": "gpt-5"},
                                    },
                                    {
                                        "key": "gen_ai.usage.input_tokens",
                                        "value": {"intValue": "42"},
                                    },
                                ],
                                "status": {"code": 1},
                            }
                        ],
                    }
                ],
            }
        ]
    }


def test_otlp_protobuf_decoding():
    from opentelemetry.proto.collector.trace.v1.trace_service_pb2 import (
        ExportTraceServiceRequest,
    )

    request = ExportTraceServiceRequest()
    resource_span = request.resource_spans.add()
    service = resource_span.resource.attributes.add()
    service.key = "service.name"
    service.value.string_value = "protobuf-agent"
    scope_span = resource_span.scope_spans.add()
    scope_span.scope.name = "protobuf-test"
    span = scope_span.spans.add()
    span.trace_id = bytes.fromhex("0123456789abcdef0123456789abcdef")
    span.span_id = bytes.fromhex("0123456789abcdef")
    span.name = "chat"
    span.start_time_unix_nano = 1
    span.end_time_unix_nano = 2
    model = span.attributes.add()
    model.key = "gen_ai.request.model"
    model.value.string_value = "gpt-5"

    decoded = decode_trace_request(
        request.SerializeToString(), "application/x-protobuf"
    )
    assert len(decoded) == 1
    assert decoded[0].is_genai is True
    assert decoded[0].model_id == "gpt-5"


@pytest.mark.asyncio
async def test_otlp_json_ingestion_is_authenticated_and_idempotent(partner_client):
    client, db = partner_client
    missing = await client.post("/v1/traces", json=_otlp_payload())
    assert missing.status_code == 401

    first = await client.post("/v1/traces", headers=_headers(), json=_otlp_payload())
    second = await client.post("/v1/traces", headers=_headers(), json=_otlp_payload())
    assert first.status_code == 200
    assert first.json()["acceptedSpans"] == 1
    assert len(first.json()["decisionIds"]) == 1
    assert second.json()["acceptedSpans"] == 0
    assert second.json()["decisionIds"] == first.json()["decisionIds"]

    assert (await db.scalar(select(func.count()).select_from(OTelSpan))) == 1
    assert (await db.scalar(select(func.count()).select_from(DecisionRecord))) == 1
    assert (await db.scalar(select(func.count()).select_from(LedgerEvent))) == 1
    row = (await db.execute(select(OTelSpan))).scalar_one()
    assert row.is_genai is True
    assert row.model_id == "gpt-5"
    assert row.attributes["gen_ai.usage.input_tokens"] == "42"
    decision = (await db.execute(select(DecisionRecord))).scalar_one()
    assert decision.metadata_["trace_id"] == row.trace_id
    assert decision.metadata_["capture_status"] == "partial"
    assert decision.model_id == "gpt-5"


@pytest.mark.asyncio
async def test_validmind_contract_exposes_model_and_accepts_link(partner_client):
    client, _ = partner_client
    await client.post("/v1/traces", headers=_headers(), json=_otlp_payload())

    health = await client.get("/api/v1/health", headers=_headers())
    models = await client.get("/api/v1/models?resource_type=llm", headers=_headers())
    schema = await client.get("/api/v1/schema", headers=_headers())
    resource_types = await client.get("/api/v1/resource-types", headers=_headers())
    assert health.json() == {"status": "healthy"}
    assert schema.status_code == 200
    assert resource_types.status_code == 200
    assert models.status_code == 200
    assert len(models.json()) == 1
    model = models.json()[0]
    assert model["name"] == "gpt-5"
    assert model["metadata"]["genai_span_count"] == 1

    linked = await client.put(
        f"/api/v1/models/{model['id']}",
        headers=_headers(),
        json={"vm_cuid": "mdl_validmind_123"},
    )
    assert linked.status_code == 200
    assert linked.json()["metadata"]["vm_cuid"] == "mdl_validmind_123"
