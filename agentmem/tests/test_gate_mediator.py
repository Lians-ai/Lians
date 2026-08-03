"""Focused contracts for the standalone first-party Gate mediator.

These tests deliberately avoid a live provider or Lians server.  Transport and
ordering are exercised through exact fakes so the suite can prove that no
provider call occurs before authoritative permit consumption.
"""

from __future__ import annotations

import inspect
import socket
import tomllib
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from lians.gate_mediator.app import PERMIT_HEADERS, create_gate_mediator_app
from lians.gate_mediator.canonical import (
    RequestContractViolation,
    build_provider_headers,
    derive_execution_binding,
    validate_request_body,
)
from lians.gate_mediator.config import MediatorConfig, MediatorRouteConfig
from lians.gate_mediator.schemas import (
    GateConsumptionReceipt,
    GatePrincipal,
    ProviderDispatchResult,
)
from lians.gate_mediator.transport import (
    DestinationRejected,
    PermitRejected,
    parse_https_destination,
    resolve_and_pin,
)
from pydantic import ValidationError


_REPO_ROOT = Path(__file__).resolve().parents[2]

MEDIATOR = "lians:principal:v1:api-key:00000000-0000-0000-0000-000000000001"


def _secret(tmp_path: Path, name: str, value: str) -> str:
    path = tmp_path / name
    path.write_text(value, encoding="ascii")
    path.chmod(0o640)
    return str(path.resolve())


def _route(tmp_path: Path, **updates) -> MediatorRouteConfig:
    values = {
        "route_id": "orders.release.v1",
        "route_version": "v1",
        "action": "orders.release",
        "target_ref": "urn:lians:orders:production",
        "target_binding": "fixed-route-authority-v1",
        "upstream_url": "https://provider.example/v1/orders/release?account=production",
        "method": "POST",
        "request_content_type": "application/json",
        "request_contract_ref": "orders-release-request.v1",
        "allowed_json_top_level_fields": ["order_id", "release"],
        "required_json_top_level_fields": [],
        "response_content_types": ["application/json"],
        "max_request_bytes": 1024,
        "max_response_bytes": 4096,
        "allowed_ip_cidrs": ["8.8.8.8/32"],
        "fixed_headers": {"x-provider-account": "production"},
        "credential": {
            "header_name": "authorization",
            "secret_file": _secret(tmp_path, "provider-token", "p" * 40),
            "binding_ref": "provider-account-production.v3",
            "value_prefix": "Bearer ",
        },
        "idempotency_header_name": "idempotency-key",
        "audit_correlation_header_name": "x-lians-audit-correlation",
    }
    values.update(updates)
    return MediatorRouteConfig.model_validate(values)


def _config(tmp_path: Path, route: MediatorRouteConfig) -> MediatorConfig:
    return MediatorConfig.model_validate(
        {
            "schema_version": 1,
            "server_tls": {
                "certificate_file": str((tmp_path / "server.crt").resolve()),
                "private_key_file": str((tmp_path / "server.key").resolve()),
            },
            "gate": {
                "base_url": "https://lians.internal.example/",
                "api_key_file": _secret(tmp_path, "gate-key", "g" * 40),
                "allowed_ip_cidrs": ["10.42.0.0/16"],
            },
            "expected_mediator_principal_id": MEDIATOR,
            "expected_namespace": "tenant-a",
            "expected_barrier_group": "orders",
            "caller_token_file": _secret(tmp_path, "caller-token", "c" * 40),
            "metrics_bearer_token_file": _secret(tmp_path, "metrics-token", "m" * 40),
            "max_in_flight": 2,
            "routes": [route.model_dump(mode="python")],
        }
    )


def test_route_config_forbids_dynamic_destinations_and_unsafe_headers(tmp_path):
    route = _route(tmp_path)
    common = route.model_dump(mode="python")
    for url in (
        "http://provider.example/v1/release",
        "https://127.0.0.1/v1/release",
        "https://provider.example/v1/%2E%2E/admin",
        "https://provider.example//evil.example/release",
        "https://provider.example/v1/release#fragment",
        "https://user:secret@provider.example/v1/release",
        "https://provider.example:0/v1/release",
    ):
        with pytest.raises(ValidationError):
            MediatorRouteConfig.model_validate({**common, "upstream_url": url})
    for header in ("Host", "Authorization", "Transfer-Encoding", "X-Forwarded-Host"):
        with pytest.raises(ValidationError):
            MediatorRouteConfig.model_validate(
                {**common, "fixed_headers": {header: "caller-controlled"}}
            )


def test_binding_covers_every_security_relevant_mutation(tmp_path):
    route = _route(tmp_path)
    decision_id = uuid4()
    body = b'{"order_id":"o-123","release":true}'
    baseline = derive_execution_binding(route, decision_id, body)
    assert (
        baseline.execution_request_hash
        == derive_execution_binding(route, decision_id, body).execution_request_hash
    )
    assert (
        baseline.execution_request_hash
        != derive_execution_binding(route, decision_id, body + b" ").execution_request_hash
    )
    assert (
        baseline.execution_request_hash
        != derive_execution_binding(route, uuid4(), body).execution_request_hash
    )
    changed_route = route.model_copy(update={"target_ref": "urn:lians:orders:other"})
    assert (
        baseline.execution_request_hash
        != derive_execution_binding(changed_route, decision_id, body).execution_request_hash
    )


def test_json_contract_rejects_authority_smuggling_and_parser_ambiguity(tmp_path):
    route = _route(
        tmp_path,
        required_json_top_level_fields=["order_id"],
        max_json_depth=3,
    )
    validate_request_body(route, b'{"order_id":"o-123","release":true}')
    for body in (
        b'{"order_id":"o-123","tenant":"other"}',
        b'{"order_id":"one","order_id":"two"}',
        b'{"order_id":"o-123","release":NaN}',
        b'{"order_id":"o-123","release":1e400}',
        b'{"order_id":"\\ud800"}',
        b'{"release":true}',
        b'{"order_id":{"nested":{"too_deep":true}}}',
        b'["o-123"]',
    ):
        with pytest.raises(RequestContractViolation):
            validate_request_body(route, body)


def test_mediator_identity_must_be_dedicated_and_barrier_scoped(tmp_path):
    route = _route(tmp_path)
    document = _config(tmp_path, route).model_dump(mode="python")
    with pytest.raises(ValidationError):
        MediatorConfig.model_validate({**document, "expected_barrier_group": None})


def test_provider_headers_are_rebuilt_and_credential_value_is_not_in_binding(tmp_path):
    route = _route(tmp_path)
    decision_id = uuid4()
    permit_id = uuid4()
    body = b"{}"
    binding = derive_execution_binding(route, decision_id, body)
    headers = build_provider_headers(
        route,
        decision_id=decision_id,
        permit_id=permit_id,
        body_length=len(body),
        credential_secret="server-secret",
    )
    assert headers["authorization"] == "Bearer server-secret"
    assert headers["x-lians-audit-correlation"] == str(permit_id)
    assert "host" not in headers
    assert "connection" not in headers
    assert b"server-secret" not in binding.canonical_envelope


@pytest.mark.asyncio
async def test_dns_rejects_private_or_mixed_answers_and_pins_the_validated_ip():
    destination = parse_https_destination("https://provider.example/v1/release")

    def mixed_resolver(*_args, **_kwargs):
        return [
            (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("8.8.8.8", 443)),
            (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("127.0.0.1", 443)),
        ]

    with pytest.raises(DestinationRejected):
        await resolve_and_pin(
            destination,
            allowed_ip_cidrs=[],
            require_global=True,
            timeout_seconds=1,
            resolver=mixed_resolver,
        )

    def public_resolver(*_args, **_kwargs):
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("8.8.8.8", 443))]

    resolved = await resolve_and_pin(
        destination,
        allowed_ip_cidrs=["8.8.8.8/32"],
        require_global=True,
        timeout_seconds=1,
        resolver=public_resolver,
    )
    assert resolved.pinned_ip == "8.8.8.8"


def test_transport_source_proves_ip_pin_with_original_hostname_tls_identity():
    from lians.gate_mediator import transport

    source = inspect.getsource(transport._PinnedHTTPSConnection.connect)
    assert "self._pinned_ip" in source
    assert "socket.create_connection" not in source
    assert "raw_socket.connect(endpoint)" in source
    assert "server_hostname=self.host" in source
    module_source = inspect.getsource(transport)
    assert "follow_redirects" not in module_source
    assert "httpx" not in module_source


def test_cli_is_tls_only_no_access_log_and_packaged_as_a_separate_entrypoint():
    from lians.gate_mediator import cli

    source = inspect.getsource(cli)
    assert "TLSv1_2" in source
    assert "access_log=False" in source
    assert "proxy_headers=False" in source
    with (_REPO_ROOT / "pyproject.toml").open("rb") as stream:
        project = tomllib.load(stream)
    assert project["project"]["scripts"]["lians-gate-mediator"] == "lians.gate_mediator.cli:main"


class _FakeGate:
    def __init__(self, events: list[str], *, reject: bool = False):
        self.events = events
        self.reject = reject

    async def whoami(self):
        return GatePrincipal(
            namespace="tenant-a",
            scopes=["write"],
            barrier_group="orders",
            principal_id=MEDIATOR,
            auth_method="api_key",
        )

    async def consume(self, permit, binding):
        self.events.append("consume")
        if self.reject:
            raise PermitRejected("invalid")
        return GateConsumptionReceipt(
            id=uuid4(),
            permit_id=permit.permit_id,
            evaluation_id=uuid4(),
            decision_id=binding.decision_id,
            consuming_principal_id=MEDIATOR,
            action=binding.action,
            target_ref=binding.target_ref,
            execution_request_hash=binding.execution_request_hash,
            consumed_at=datetime.now(UTC),
            consumption_hash="a" * 64,
        )


class _FakeProvider:
    def __init__(self, events: list[str]):
        self.events = events

    async def validate_route_startup(self, _route):
        return None

    async def prepare(self, _route, *, permit, body):
        self.events.append("provider_prepare")
        return SimpleNamespace(permit=permit, body=body)

    async def dispatch(self, _prepared):
        self.events.append("provider_dispatch")
        return ProviderDispatchResult(
            status_code=200,
            content_type="application/json",
            body=b'{"released":true}',
        )


def _permit_headers(prepared: dict[str, object]) -> dict[str, str]:
    now = datetime.now(UTC)
    values = {
        "permit_id": str(uuid4()),
        "enforcement_principal_id": MEDIATOR,
        "action": str(prepared["action"]),
        "target_ref": str(prepared["target_ref"]),
        "decision_id": str(prepared["decision_id"]),
        "execution_request_hash": str(prepared["execution_request_hash"]),
        "issued_at": now.isoformat(),
        "expires_at": (now + timedelta(seconds=60)).isoformat(),
        "token": "lians_permit_v1_" + ("x" * 43),
    }
    return {PERMIT_HEADERS[key]: value for key, value in values.items()}


def _prepare(client: TestClient, route: MediatorRouteConfig, body: bytes) -> dict[str, object]:
    response = client.post(
        f"/v1/prepare/{route.route_id}",
        content=body,
        headers={
            "Content-Type": route.request_content_type,
            "X-Lians-Mediator-Client-Token": "c" * 40,
            "X-Lians-Decision-Id": str(uuid4()),
        },
    )
    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store"
    assert response.headers["pragma"] == "no-cache"
    return response.json()


def test_execute_consumes_once_immediately_before_one_provider_dispatch(tmp_path):
    route = _route(tmp_path, allowed_ip_cidrs=[])
    config = _config(tmp_path, route)
    events: list[str] = []
    app = create_gate_mediator_app(
        config,
        gate_client=_FakeGate(events),
        provider_dispatcher=_FakeProvider(events),
    )
    body = b'{"order_id":"o-123"}'
    with TestClient(app) as client:
        prepared = _prepare(client, route, body)
        response = client.post(
            f"/v1/execute/{route.route_id}",
            content=body,
            headers={
                "Content-Type": route.request_content_type,
                "X-Lians-Mediator-Client-Token": "c" * 40,
                **_permit_headers(prepared),
            },
        )
    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store"
    assert events == ["provider_prepare", "consume", "provider_dispatch"]


def test_gate_rejection_never_dispatches_provider(tmp_path):
    route = _route(tmp_path, allowed_ip_cidrs=[])
    config = _config(tmp_path, route)
    events: list[str] = []
    app = create_gate_mediator_app(
        config,
        gate_client=_FakeGate(events, reject=True),
        provider_dispatcher=_FakeProvider(events),
    )
    body = b"{}"
    with TestClient(app) as client:
        prepared = _prepare(client, route, body)
        response = client.post(
            f"/v1/execute/{route.route_id}",
            content=body,
            headers={
                "Content-Type": route.request_content_type,
                "X-Lians-Mediator-Client-Token": "c" * 40,
                **_permit_headers(prepared),
            },
        )
    assert response.status_code == 403
    assert events == ["provider_prepare", "consume"]


def test_metrics_use_a_distinct_bearer_and_only_bounded_non_tenant_labels(tmp_path):
    from lians.gate_mediator import metrics as mediator_metrics

    route = _route(tmp_path, allowed_ip_cidrs=[])
    app = create_gate_mediator_app(
        _config(tmp_path, route),
        gate_client=_FakeGate([]),
        provider_dispatcher=_FakeProvider([]),
    )
    with TestClient(app) as client:
        unauthenticated = client.get("/metrics")
        caller_credential = client.get(
            "/metrics",
            headers={"Authorization": f"Bearer {'c' * 40}"},
        )
        response = client.get(
            "/metrics",
            headers={"Authorization": f"Bearer {'m' * 40}"},
        )
    assert unauthenticated.status_code == 401
    assert caller_credential.status_code == 401
    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store"
    if mediator_metrics._PROM_AVAILABLE:
        assert "lians_gate_mediator_upstream_requests_total" in response.text
        assert "lians_gate_mediator_upstream_duration_seconds" in response.text
    for forbidden_label in (
        "namespace=",
        "tenant=",
        "principal=",
        "route_id=",
        "target=",
        "permit_id=",
    ):
        assert forbidden_label not in response.text


def test_public_api_never_imports_or_mounts_the_mediator():
    main_source = (_REPO_ROOT / "agentmem/src/lians/main.py").read_text(encoding="utf-8")
    assert "gate_mediator" not in main_source
