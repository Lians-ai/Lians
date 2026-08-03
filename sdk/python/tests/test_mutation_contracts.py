"""Deferred SDK serialization and sensitive-response contract coverage."""

import json
from datetime import UTC, datetime

import httpx
import respx
from lians import LiansClient, SupersessionActionResult
from lians.types import WorkloadCredentialCreated

BASE = "https://lians.test"


async def test_batch_serializes_datetime_without_mutating_input():
    event_time = datetime(2026, 8, 2, 12, 30, tzinfo=UTC)
    memory = {"agent_id": "agent-1", "content": "fact", "event_time": event_time}
    async with LiansClient(BASE, "key", http2=False) as client:
        with respx.mock(base_url=BASE) as mock:
            route = mock.post("/v1/memories/batch").mock(
                return_value=httpx.Response(200, json={"added": 0, "memories": []})
            )
            await client.batch_add([memory], idempotency_key="batch-import-1")

    body = json.loads(route.calls[0].request.content)
    assert body["memories"][0]["event_time"] == event_time.isoformat()
    assert memory["event_time"] is event_time
    assert route.calls[0].request.headers["Idempotency-Key"] == "batch-import-1"


async def test_closure_attestation_serializes_sensitive_read_flag_and_hash_fields():
    payload = {
        "id": "00000000-0000-0000-0000-000000000001",
        "namespace": "tenant-1",
        "barrier_group": None,
        "resource_type": "case",
        "resource_id": "00000000-0000-0000-0000-000000000002",
        "attested_by": "oidc:subject",
        "statement": None,
        "statement_hash": "a" * 64,
        "hash_version": 2,
        "evidence_refs": ["receipt:1"],
        "decision_id": None,
        "change_event_id": None,
        "attestation_hash": "b" * 64,
        "attested_at": "2026-08-02T12:30:00Z",
    }
    async with LiansClient(BASE, "key", http2=False) as client:
        with respx.mock(base_url=BASE) as mock:
            route = mock.get(
                "/v1/control/investigations/case/case-1/attestation",
                params={"include_statement": "true"},
            ).mock(return_value=httpx.Response(200, json=payload))
            attestation = await client.closure_attestation(
                "case",
                "case-1",
                include_statement=True,
            )

    assert route.called
    assert attestation.statement is None
    assert attestation.statement_hash == "a" * 64
    assert attestation.hash_version == 2


async def test_supersession_mutation_returns_typed_result():
    payload = {
        "memory_id": "00000000-0000-0000-0000-000000000001",
        "action": "confirm",
        "applied_at": "2026-08-02T12:30:00Z",
    }
    async with LiansClient(BASE, "key", http2=False) as client:
        with respx.mock(base_url=BASE) as mock:
            mock.patch("/v1/supersessions/memory-1").mock(
                return_value=httpx.Response(200, json=payload)
            )
            result = await client.confirm_supersession(
                "memory-1",
                expected_superseded_by=None,
            )

    assert isinstance(result, SupersessionActionResult)
    assert result.action == "confirm"


def test_one_time_workload_secret_is_excluded_from_repr():
    credential = WorkloadCredentialCreated.model_validate(
        {
            "id": "00000000-0000-0000-0000-000000000001",
            "namespace": "tenant-1",
            "label": None,
            "scopes": ["read"],
            "effective_scopes": ["read"],
            "role": "readonly",
            "barrier_group": None,
            "provisioning_source": "tenant_oidc",
            "created_by": "oidc:subject",
            "created_at": "2026-08-02T12:30:00Z",
            "expires_at": "2026-08-03T12:30:00Z",
            "last_used_at": None,
            "rotated_from_id": None,
            "rotated_at": None,
            "revoked_at": None,
            "version": 1,
            "status": "active",
            "secret": "lians_workload_v1_do-not-log",
        }
    )

    assert "do-not-log" not in repr(credential)
