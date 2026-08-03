"""Standalone FastAPI application for mediated provider execution."""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import logging
import time
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID, uuid4

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from pydantic import ValidationError
from starlette.datastructures import MutableHeaders
from starlette.responses import JSONResponse, Response

from ..version import __version__
from .canonical import RequestContractViolation, derive_execution_binding, validate_request_body
from .config import MediatorConfig, MediatorConfigError, MediatorRouteConfig, read_header_secret
from .metrics import generate_metrics, observe_upstream
from .schemas import PreparedExecution, PresentedExecutionPermit
from .transport import (
    DestinationRejected,
    GateClient,
    GateUnavailable,
    PermitRejected,
    PinnedTransportError,
    ProviderDispatcher,
    permit_is_current,
)

logger = logging.getLogger("lians.gate_mediator")


def _operator_ref(kind: str, value: object) -> str:
    """Correlate execution identifiers without logging their raw values."""

    material = f"lians/gate-mediator-log-ref/v1\0{kind}\0{value}".encode()
    return hashlib.sha256(material).hexdigest()[:16]

CALLER_TOKEN_HEADER = "x-lians-mediator-client-token"
DECISION_ID_HEADER = "x-lians-decision-id"
PERMIT_HEADERS = {
    "permit_id": "x-lians-permit-id",
    "enforcement_principal_id": "x-lians-permit-enforcement-principal",
    "action": "x-lians-permit-action",
    "target_ref": "x-lians-permit-target-ref",
    "decision_id": "x-lians-permit-decision-id",
    "execution_request_hash": "x-lians-permit-request-hash",
    "issued_at": "x-lians-permit-issued-at",
    "expires_at": "x-lians-permit-expires-at",
    "token": "x-lians-permit-token",
}
_MAX_INGRESS_HEADERS = 64
_MAX_INGRESS_HEADER_BYTES = 32_768
_MAX_SECURITY_HEADER_BYTES = 2_048


class MediatorHTTPError(RuntimeError):
    def __init__(
        self,
        status_code: int,
        detail: str,
        *,
        headers: dict[str, str] | None = None,
    ) -> None:
        super().__init__(detail)
        self.status_code = status_code
        self.detail = detail
        self.headers = headers or {}


class MediatorSecurityHeadersMiddleware:
    """Apply defensive headers even when an endpoint fails unexpectedly."""

    def __init__(self, app) -> None:
        self.app = app

    async def __call__(self, scope, receive, send) -> None:
        if scope.get("type") != "http":
            await self.app(scope, receive, send)
            return

        request_id = str(uuid4())
        scope.setdefault("state", {})["mediator_request_id"] = request_id
        response_started = False

        async def send_secure(message) -> None:
            nonlocal response_started
            if message.get("type") == "http.response.start":
                response_started = True
                headers = MutableHeaders(scope=message)
                headers["Cache-Control"] = "no-store"
                headers["Pragma"] = "no-cache"
                headers["X-Content-Type-Options"] = "nosniff"
                headers["Referrer-Policy"] = "no-referrer"
                headers["X-Lians-Request-Id"] = request_id
            await send(message)

        try:
            await self.app(scope, receive, send_secure)
        except Exception:
            if response_started:
                raise
            logger.error("event=mediator_internal_error request_id=%s", request_id)
            response = JSONResponse(
                status_code=500,
                content={"detail": "Mediator failed closed"},
            )
            await response(scope, receive, send_secure)


def _raw_headers(request: Request) -> list[tuple[bytes, bytes]]:
    headers = list(request.scope.get("headers", ()))
    if (
        len(headers) > _MAX_INGRESS_HEADERS
        or sum(len(name) + len(value) + 4 for name, value in headers) > _MAX_INGRESS_HEADER_BYTES
    ):
        raise MediatorHTTPError(431, "Request headers are too large")
    return headers


def _single_header(
    request: Request,
    name: str,
    *,
    required: bool = True,
    maximum_bytes: int = _MAX_SECURITY_HEADER_BYTES,
) -> str | None:
    encoded_name = name.encode("ascii")
    values = [value for key, value in _raw_headers(request) if key.lower() == encoded_name]
    if len(values) > 1:
        raise MediatorHTTPError(400, "Duplicate security header")
    if not values:
        if required:
            raise MediatorHTTPError(400, "Required security header is missing")
        return None
    value = values[0]
    if not value or len(value) > maximum_bytes or b"\x00" in value:
        raise MediatorHTTPError(400, "Security header is invalid")
    return value.decode("latin-1")


async def _read_exact_bounded_body(request: Request, maximum_bytes: int) -> bytes:
    content_length = _single_header(
        request,
        "content-length",
        required=False,
        maximum_bytes=20,
    )
    if content_length is not None:
        if not content_length.isascii() or not content_length.isdigit():
            raise MediatorHTTPError(400, "Content-Length is invalid")
        if int(content_length, 10) > maximum_bytes:
            raise MediatorHTTPError(413, "Request body exceeds the configured route limit")

    transfer_encoding = _single_header(
        request,
        "transfer-encoding",
        required=False,
        maximum_bytes=32,
    )
    if transfer_encoding is not None and (
        content_length is not None or transfer_encoding.lower() != "chunked"
    ):
        raise MediatorHTTPError(400, "Request transfer framing is invalid")

    content_encoding = _single_header(
        request,
        "content-encoding",
        required=False,
        maximum_bytes=32,
    )
    if content_encoding is not None and content_encoding.lower() != "identity":
        raise MediatorHTTPError(415, "Encoded request bodies are not supported")

    body = bytearray()
    async for chunk in request.stream():
        body.extend(chunk)
        if len(body) > maximum_bytes:
            raise MediatorHTTPError(413, "Request body exceeds the configured route limit")
    if content_length is not None and len(body) != int(content_length, 10):
        raise MediatorHTTPError(400, "Request body length is inconsistent")
    return bytes(body)


def _parse_uuid_header(request: Request, name: str) -> UUID:
    value = _single_header(request, name, maximum_bytes=36)
    try:
        return UUID(str(value))
    except ValueError:
        raise MediatorHTTPError(400, "Identifier header is invalid") from None


def _presented_permit(request: Request) -> PresentedExecutionPermit:
    document = {field: _single_header(request, header) for field, header in PERMIT_HEADERS.items()}
    try:
        return PresentedExecutionPermit.model_validate(document)
    except ValidationError:
        raise MediatorHTTPError(403, "Execution permit is invalid or unusable") from None


class GateMediatorService:
    def __init__(
        self,
        config: MediatorConfig,
        *,
        gate_client: GateClient | Any | None = None,
        provider_dispatcher: ProviderDispatcher | Any | None = None,
    ) -> None:
        self.config = config
        self.routes = config.route_map()
        self.gate_client = gate_client or GateClient(
            config.gate,
            dns_timeout_seconds=config.dns_timeout_seconds,
        )
        self.provider_dispatcher = provider_dispatcher or ProviderDispatcher(
            dns_timeout_seconds=config.dns_timeout_seconds,
        )
        self._slots = asyncio.Semaphore(config.max_in_flight)
        self._identity_check_lock = asyncio.Lock()
        self._identity_verified_until = 0.0
        self.initialized = False

    async def _assert_gate_identity(self) -> None:
        principal = await self.gate_client.whoami()
        if (
            principal.principal_id != self.config.expected_mediator_principal_id
            or principal.namespace != self.config.expected_namespace
            or principal.barrier_group != self.config.expected_barrier_group
            or principal.auth_method != "api_key"
            or "write" not in principal.scopes
        ):
            raise GateUnavailable("Gate identity does not match the pinned mediator identity")

    async def initialize(self) -> None:
        caller_token = read_header_secret(self.config.caller_token_file, minimum_bytes=32)
        metrics_token = read_header_secret(
            self.config.metrics_bearer_token_file,
            minimum_bytes=32,
        )
        if hmac.compare_digest(caller_token, metrics_token):
            raise MediatorConfigError("caller and metrics credentials must be distinct")
        await self._assert_gate_identity()
        self._identity_verified_until = time.monotonic() + self.config.identity_recheck_seconds
        startup_slots = asyncio.Semaphore(min(8, len(self.config.routes)))

        async def validate_route(route: MediatorRouteConfig) -> None:
            async with startup_slots:
                await self.provider_dispatcher.validate_route_startup(route)

        await asyncio.gather(*(validate_route(route) for route in self.config.routes))
        self.initialized = True
        logger.info(
            "event=mediator_ready route_count=%d",
            len(self.routes),
        )

    async def readiness(self) -> bool:
        if not self.initialized:
            return False
        if time.monotonic() < self._identity_verified_until:
            return True
        async with self._identity_check_lock:
            if time.monotonic() < self._identity_verified_until:
                return True
            try:
                await self._assert_gate_identity()
            except (GateUnavailable, MediatorConfigError):
                return False
            self._identity_verified_until = time.monotonic() + self.config.identity_recheck_seconds
            return True

    def authenticate_caller(self, request: Request) -> None:
        presented = _single_header(request, CALLER_TOKEN_HEADER, maximum_bytes=8_192)
        try:
            expected = read_header_secret(self.config.caller_token_file, minimum_bytes=32)
            metrics_token = read_header_secret(
                self.config.metrics_bearer_token_file,
                minimum_bytes=32,
            )
        except MediatorConfigError:
            raise MediatorHTTPError(503, "Mediator credentials are unavailable") from None
        if hmac.compare_digest(expected, metrics_token):
            raise MediatorHTTPError(503, "Mediator credentials are unavailable")
        if not hmac.compare_digest(str(presented).encode("latin-1"), expected.encode("ascii")):
            raise MediatorHTTPError(401, "Mediator caller authentication failed")

    def authenticate_metrics(self, request: Request) -> None:
        authorization = _single_header(
            request,
            "authorization",
            required=False,
            maximum_bytes=8_192,
        )
        supplied = ""
        if authorization is not None:
            scheme, separator, candidate = authorization.partition(" ")
            if separator and scheme.lower() == "bearer":
                supplied = candidate
        try:
            expected = read_header_secret(
                self.config.metrics_bearer_token_file,
                minimum_bytes=32,
            )
            caller_token = read_header_secret(
                self.config.caller_token_file,
                minimum_bytes=32,
            )
        except MediatorConfigError:
            raise MediatorHTTPError(503, "Mediator metrics are unavailable") from None
        if hmac.compare_digest(expected, caller_token):
            raise MediatorHTTPError(503, "Mediator metrics are unavailable")
        if not supplied or not hmac.compare_digest(
            supplied.encode("latin-1"), expected.encode("ascii")
        ):
            raise MediatorHTTPError(
                401,
                "Metrics authentication required",
                headers={"WWW-Authenticate": "Bearer"},
            )

    def route(self, route_id: str) -> MediatorRouteConfig:
        route = self.routes.get(route_id)
        if route is None:
            raise MediatorHTTPError(404, "Configured mediator route was not found")
        return route

    @staticmethod
    def validate_content_type(request: Request, route: MediatorRouteConfig) -> None:
        content_type = _single_header(request, "content-type", maximum_bytes=256)
        if content_type is None or content_type.lower() != route.request_content_type:
            raise MediatorHTTPError(415, "Request Content-Type does not match the configured route")

    def validate_permit(self, permit: PresentedExecutionPermit, binding) -> None:
        now = datetime.now(UTC)
        if (
            permit.enforcement_principal_id != self.config.expected_mediator_principal_id
            or permit.action != binding.action
            or permit.target_ref != binding.target_ref
            or permit.decision_id != binding.decision_id
            or permit.execution_request_hash != binding.execution_request_hash
            or not permit_is_current(permit)
            or permit.issued_at.astimezone(UTC) > now + timedelta(seconds=30)
            or permit.expires_at - permit.issued_at > timedelta(seconds=300)
        ):
            raise MediatorHTTPError(403, "Execution permit is invalid or unusable")

    @asynccontextmanager
    async def execution_slot(self):
        try:
            await asyncio.wait_for(
                self._slots.acquire(),
                timeout=self.config.queue_timeout_seconds,
            )
        except TimeoutError:
            raise MediatorHTTPError(503, "Mediator execution capacity is exhausted") from None
        try:
            yield
        finally:
            self._slots.release()


def create_gate_mediator_app(
    config: MediatorConfig,
    *,
    gate_client: GateClient | Any | None = None,
    provider_dispatcher: ProviderDispatcher | Any | None = None,
) -> FastAPI:
    service = GateMediatorService(
        config,
        gate_client=gate_client,
        provider_dispatcher=provider_dispatcher,
    )

    @asynccontextmanager
    async def lifespan(_: FastAPI):
        try:
            await service.initialize()
        except (GateUnavailable, DestinationRejected, MediatorConfigError) as exc:
            raise RuntimeError("Gate mediator failed closed during startup") from exc
        yield

    app = FastAPI(
        title="Lians Gate Enforcement Mediator",
        version=__version__,
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
        lifespan=lifespan,
    )
    app.router.redirect_slashes = False
    app.state.gate_mediator = service
    app.add_middleware(MediatorSecurityHeadersMiddleware)

    @app.exception_handler(MediatorHTTPError)
    async def mediator_error(_: Request, error: MediatorHTTPError):
        return JSONResponse(
            status_code=error.status_code,
            content={"detail": error.detail},
            headers=error.headers,
        )

    @app.exception_handler(RequestValidationError)
    async def request_validation_error(_: Request, __: RequestValidationError):
        return JSONResponse(
            status_code=422,
            content={"detail": "Request validation failed"},
        )

    @app.get("/livez", include_in_schema=False)
    async def livez() -> JSONResponse:
        return JSONResponse({"status": "alive"})

    @app.get("/readyz", include_in_schema=False)
    async def readyz() -> JSONResponse:
        ready = await service.readiness()
        return JSONResponse(
            {"status": "ready" if ready else "unready"},
            status_code=200 if ready else 503,
        )

    @app.get("/metrics", include_in_schema=False)
    async def metrics(request: Request) -> Response:
        service.authenticate_metrics(request)
        content, content_type = generate_metrics()
        return Response(content=content, headers={"Content-Type": content_type})

    @app.post("/v1/prepare/{route_id}", response_model=PreparedExecution)
    async def prepare(route_id: str, request: Request) -> PreparedExecution:
        service.authenticate_caller(request)
        route = service.route(route_id)
        service.validate_content_type(request, route)
        decision_id = _parse_uuid_header(request, DECISION_ID_HEADER)
        body = await _read_exact_bounded_body(request, route.max_request_bytes)
        try:
            validate_request_body(route, body)
        except RequestContractViolation:
            raise MediatorHTTPError(
                422, "Request body violates the configured route contract"
            ) from None
        binding = derive_execution_binding(route, decision_id, body)
        logger.info(
            "event=execution_prepared request_ref=%s route_ref=%s decision_ref=%s",
            _operator_ref("request", request.state.mediator_request_id),
            _operator_ref("route", route.route_id),
            _operator_ref("decision", decision_id),
        )
        return PreparedExecution(
            route_id=route.route_id,
            route_config_sha256=binding.route_config_sha256,
            canonicalization=binding.canonicalization,
            enforcement_principal_id=config.expected_mediator_principal_id,
            action=binding.action,
            target_ref=binding.target_ref,
            decision_id=binding.decision_id,
            request_body_sha256=binding.request_body_sha256,
            request_body_bytes=binding.request_body_bytes,
            execution_request_hash=binding.execution_request_hash,
        )

    @app.post("/v1/execute/{route_id}")
    async def execute(route_id: str, request: Request) -> Response:
        service.authenticate_caller(request)
        route = service.route(route_id)
        service.validate_content_type(request, route)
        permit = _presented_permit(request)
        body = await _read_exact_bounded_body(request, route.max_request_bytes)
        try:
            validate_request_body(route, body)
        except RequestContractViolation:
            raise MediatorHTTPError(
                422, "Request body violates the configured route contract"
            ) from None
        binding = derive_execution_binding(route, permit.decision_id, body)
        service.validate_permit(permit, binding)
        correlation_headers = {
            "X-Lians-Correlation-Id": str(permit.permit_id),
        }

        async with service.execution_slot():
            try:
                prepared_call = await service.provider_dispatcher.prepare(
                    route,
                    permit=permit,
                    body=body,
                )
            except (DestinationRejected, MediatorConfigError, PinnedTransportError):
                raise MediatorHTTPError(
                    503,
                    "Provider route is unavailable; permit was not consumed",
                    headers=correlation_headers,
                ) from None

            try:
                receipt = await service.gate_client.consume(permit, binding)
            except PermitRejected:
                raise MediatorHTTPError(
                    403,
                    "Execution permit is invalid or unusable",
                    headers=correlation_headers,
                ) from None
            except (GateUnavailable, MediatorConfigError):
                raise MediatorHTTPError(
                    503,
                    "Gate redemption is unavailable; provider was not called",
                    headers=correlation_headers,
                ) from None

            correlation_headers.update(
                {
                    "X-Lians-Gate-Consumption-Id": str(receipt.id),
                    "X-Lians-Gate-Consumption-Hash": receipt.consumption_hash,
                    "X-Lians-Gate-Evaluation-Id": str(receipt.evaluation_id),
                }
            )
            provider_started = time.perf_counter()
            # Once Gate commits consumption, every provider-path failure is an
            # outcome-unknown response; never expose an exception or invite an
            # automatic retry merely because a new adapter raised unexpectedly.
            try:
                result = await service.provider_dispatcher.dispatch(prepared_call)
            except Exception:  # noqa: BLE001
                observe_upstream(
                    "outcome_unknown",
                    time.perf_counter() - provider_started,
                )
                logger.warning(
                    "event=provider_outcome_unknown request_ref=%s route_ref=%s "
                    "permit_ref=%s consumption_ref=%s",
                    _operator_ref("request", request.state.mediator_request_id),
                    _operator_ref("route", route.route_id),
                    _operator_ref("permit", permit.permit_id),
                    _operator_ref("consumption", receipt.id),
                )
                raise MediatorHTTPError(
                    502,
                    "Provider outcome is unknown; reconcile using the correlation ID",
                    headers=correlation_headers,
                ) from None

            provider_outcome = (
                "success"
                if result.status_code < 400
                else "client_error"
                if result.status_code < 500
                else "server_error"
            )
            observe_upstream(
                provider_outcome,
                time.perf_counter() - provider_started,
            )

        logger.info(
            "event=execution_dispatched request_ref=%s route_ref=%s permit_ref=%s "
            "consumption_ref=%s provider_status=%s",
            _operator_ref("request", request.state.mediator_request_id),
            _operator_ref("route", route.route_id),
            _operator_ref("permit", permit.permit_id),
            _operator_ref("consumption", receipt.id),
            result.status_code,
        )
        response_headers = dict(correlation_headers)
        if result.content_type is not None:
            response_headers["Content-Type"] = result.content_type
        return Response(
            content=result.body,
            status_code=result.status_code,
            headers=response_headers,
        )

    return app
