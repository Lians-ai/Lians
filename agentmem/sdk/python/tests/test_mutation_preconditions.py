"""Public SDK mutation methods must carry the server's stale-write tokens."""

from datetime import UTC, datetime

import httpx
from lians import AsyncLiansClient


async def test_webhook_and_investigation_mutations_serialize_preconditions(monkeypatch):
    requests: list[tuple[str, str, dict]] = []

    async def fake_request(self, method, url, **kwargs):
        requests.append((method, url, kwargs))
        return httpx.Response(
            200,
            json={},
            request=httpx.Request(method, url),
        )

    monkeypatch.setattr(httpx.AsyncClient, "request", fake_request)
    client = AsyncLiansClient(base_url="https://lians.test")
    updated_at = datetime(2026, 8, 2, 12, 30, tzinfo=UTC)
    token = updated_at.isoformat()

    await client.update_webhook(
        "webhook-1",
        expected_updated_at=updated_at,
        enabled=False,
    )
    await client.delete_webhook(
        "webhook-1",
        expected_updated_at=updated_at,
    )
    await client.update_investigation_case(
        "case-1",
        {"expected_updated_at": token, "severity": "high"},
    )
    await client.create_remediation_task(
        "case-1",
        {"expected_case_updated_at": token, "title": "Re-evaluate"},
    )
    await client.update_remediation_task(
        "task-1",
        {"expected_updated_at": token, "status": "in_progress"},
    )
    await client.close_remediation_task(
        "task-1",
        {
            "expected_updated_at": token,
            "statement": "Verified corrected outcome",
            "evidence_refs": ["receipt:1"],
        },
    )
    await client.closure_attestation(
        "case",
        "case-1",
        include_statement=True,
    )
    await client.aclose()

    assert requests[0][2]["json"]["expected_updated_at"] == token
    assert requests[1][2]["params"]["expected_updated_at"] == token
    assert requests[2][2]["json"]["expected_updated_at"] == token
    assert requests[3][2]["json"]["expected_case_updated_at"] == token
    assert requests[4][2]["json"]["expected_updated_at"] == token
    assert requests[5][2]["json"]["expected_updated_at"] == token
    assert requests[6][2]["params"]["include_statement"] == "true"


async def test_no_content_mutations_do_not_attempt_json_decode(monkeypatch):
    async def fake_request(self, method, url, **kwargs):
        return httpx.Response(204, request=httpx.Request(method, url))

    monkeypatch.setattr(httpx.AsyncClient, "request", fake_request)
    client = AsyncLiansClient(base_url="https://lians.test")

    await client.revoke_workload_credential("credential-1", expected_version=3)
    await client.delete_webhook(
        "webhook-1",
        expected_updated_at="2026-08-02T12:30:00+00:00",
    )
    await client.aclose()
