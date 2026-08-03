"""Public discovery and immutable specification distribution contracts."""

from __future__ import annotations

import hashlib

import pytest
from httpx import ASGITransport, AsyncClient
from lians.main import app


def test_scim_openapi_declares_bearer_security_on_every_operation():
    document = app.openapi()
    bearer_scheme = document["components"]["securitySchemes"]["HTTPBearer"]
    assert bearer_scheme["type"] == "http"
    assert bearer_scheme["scheme"] == "bearer"

    operations = []
    for path, path_item in document["paths"].items():
        if not path.startswith("/scim/v2/"):
            continue
        for method in ("get", "post", "put", "patch", "delete"):
            operation = path_item.get(method)
            if operation is not None:
                operations.append(operation)

    assert operations
    for operation in operations:
        assert {"HTTPBearer": []} in operation["security"]
        assert all(
            parameter.get("name", "").casefold() != "authorization"
            for parameter in operation.get("parameters", [])
        )


@pytest.mark.asyncio
async def test_discovery_links_every_machine_readable_public_contract():
    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
    ) as client:
        response = await client.get("/.well-known/lians")

    assert response.status_code == 200
    links = response.json()["links"]
    assert links["decision_receipt_schema"].endswith("/schema.json")
    assert links["decision_receipt_conformance"].endswith("/manifest.json")
    assert links["decision_receipt_mappings"].endswith("/mappings/manifest.json")
    assert links["recorder_schema"].endswith("/envelope.schema.json")
    assert links["recorder_event_schema"].endswith("/event.schema.json")


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "path",
    (
        "/specs/decision-receipt/v0.1/schema.json",
        "/specs/decision-receipt/v0.1/conformance/manifest.json",
        "/specs/decision-receipt/v0.1/conformance/fixtures/valid-signed.json",
        "/specs/decision-receipt/v0.1/mappings/manifest.json",
        "/specs/decision-receipt/v0.1/mappings/manifest.schema.json",
        "/specs/decision-receipt/v0.1/mappings/opentelemetry-genai.md",
        "/specs/decision-receipt/v0.1/mappings/mcp.md",
        "/specs/decision-receipt/v0.1/mappings/a2a.md",
        "/specs/universal-recorder/v0.1/envelope.schema.json",
        "/specs/universal-recorder/v0.1/event.schema.json",
    ),
)
async def test_public_spec_artifacts_are_immutable_and_support_etags(path: str):
    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
    ) as client:
        first = await client.get(path)
        second = await client.get(path, headers={"If-None-Match": first.headers["etag"]})

    assert first.status_code == 200
    assert first.headers["cache-control"] == "public, max-age=31536000, immutable"
    assert first.headers["x-content-type-options"] == "nosniff"
    assert second.status_code == 304


@pytest.mark.asyncio
async def test_conformance_distribution_rejects_unpublished_or_traversal_paths():
    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
    ) as client:
        unpublished = await client.get(
            "/specs/decision-receipt/v0.1/conformance/reference_runner.py"
        )
        traversal = await client.get(
            "/specs/decision-receipt/v0.1/conformance/%2E%2E/schema.json"
        )
        unpublished_mapping = await client.get(
            "/specs/decision-receipt/v0.1/mappings/private-notes.md"
        )

    assert unpublished.status_code == 404
    assert traversal.status_code == 404
    assert unpublished_mapping.status_code == 404


@pytest.mark.asyncio
async def test_receipt_mapping_manifest_pins_every_published_mapping():
    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
    ) as client:
        response = await client.get(
            "/specs/decision-receipt/v0.1/mappings/manifest.json"
        )
        assert response.status_code == 200
        manifest = response.json()
        assert {item["standard"] for item in manifest["mappings"]} == {
            "opentelemetry_genai",
            "mcp",
            "a2a",
        }
        for item in manifest["mappings"]:
            artifact = await client.get(
                f"/specs/decision-receipt/v0.1/mappings/{item['path']}"
            )
            assert artifact.status_code == 200
            assert hashlib.sha256(artifact.content).hexdigest() == item["sha256"]
