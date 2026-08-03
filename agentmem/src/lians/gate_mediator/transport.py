"""IP-pinned HTTPS transport for Gate redemption and provider dispatch.

The standard HTTP client stacks resolve DNS inside ``connect()``, which leaves
a validate-then-connect rebinding race.  This module instead opens the socket to
the exact validated IP and wraps it with TLS using the configured DNS hostname
for SNI and certificate verification.  It is HTTP/1.1-only by design and never
redirects or retries.
"""

from __future__ import annotations

import asyncio
import http.client
import ipaddress
import json
import socket
import ssl
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any
from urllib.parse import urlsplit

from pydantic import ValidationError

from .canonical import ExecutionBinding, build_provider_headers, canonical_json_bytes
from .config import (
    GateControlPlaneConfig,
    MediatorConfigError,
    MediatorRouteConfig,
    TLSFiles,
    read_header_secret,
)
from .schemas import (
    GateConsumptionReceipt,
    GatePrincipal,
    PresentedExecutionPermit,
    ProviderDispatchResult,
)

_MAX_DNS_ANSWERS = 16
_MAX_RESPONSE_HEADERS = 100
_MAX_RESPONSE_HEADER_BYTES = 32_768
_GATE_RESPONSE_LIMIT = 131_072
_METADATA_ADDRESSES = tuple(
    ipaddress.ip_network(value)
    for value in (
        "169.254.0.0/16",
        "100.100.100.200/32",
        "192.0.0.192/32",
        "fd00:ec2::254/128",
    )
)


class PinnedTransportError(RuntimeError):
    """A safe, non-diagnostic transport failure."""


class DestinationRejected(PinnedTransportError):
    """DNS or destination policy failed closed."""


class ResponseRejected(PinnedTransportError):
    """A bounded upstream response violated the configured contract."""


class PermitRejected(RuntimeError):
    """Gate rejected the presented permit without a provider dispatch."""


class GateUnavailable(RuntimeError):
    """Gate could not authoritatively redeem a permit."""


@dataclass(frozen=True)
class HTTPSDestination:
    url: str
    hostname: str
    port: int
    request_target: str
    authority: str


@dataclass(frozen=True)
class ResolvedDestination:
    destination: HTTPSDestination
    pinned_ip: str
    all_addresses: tuple[str, ...]


@dataclass(frozen=True)
class PinnedHTTPSRequest:
    destination: HTTPSDestination
    pinned_ip: str
    method: str
    headers: dict[str, str] = field(repr=False)
    body: bytes = field(repr=False)
    timeout_seconds: float
    max_response_bytes: int
    tls_context: ssl.SSLContext = field(repr=False, compare=False)


@dataclass(frozen=True)
class PinnedHTTPSResponse:
    status_code: int
    headers: tuple[tuple[str, str], ...]
    body: bytes = field(repr=False)

    def header_values(self, name: str) -> list[str]:
        normalized = name.lower()
        return [value for key, value in self.headers if key.lower() == normalized]


@dataclass(frozen=True)
class PreparedProviderCall:
    route: MediatorRouteConfig
    request: PinnedHTTPSRequest = field(repr=False)


class _PinnedHTTPSConnection(http.client.HTTPSConnection):
    """HTTPSConnection that connects to one IP while authenticating ``host``."""

    def __init__(
        self,
        host: str,
        port: int,
        *,
        pinned_ip: str,
        context: ssl.SSLContext,
        timeout: float,
        deadline: float,
    ):
        super().__init__(host=host, port=port, context=context, timeout=timeout)
        self._pinned_ip = pinned_ip
        self._deadline = deadline
        self._connecting_socket: socket.socket | None = None

    def _remaining_timeout(self) -> float:
        remaining = self._deadline - time.monotonic()
        if remaining <= 0:
            raise TimeoutError("HTTPS deadline exceeded")
        return remaining

    def abort(self) -> None:
        """Interrupt connect, TLS, write, header, or body I/O at the deadline."""
        active_socket = self.sock or self._connecting_socket
        if active_socket is not None:
            try:
                active_socket.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
            active_socket.close()

    def connect(self) -> None:
        if self._tunnel_host is not None:
            raise OSError("HTTP tunneling is disabled")
        address = _normalized_ip(self._pinned_ip)
        family = socket.AF_INET if address.version == 4 else socket.AF_INET6
        raw_socket = socket.socket(family, socket.SOCK_STREAM, socket.IPPROTO_TCP)
        self._connecting_socket = raw_socket
        try:
            raw_socket.settimeout(self._remaining_timeout())
            if self.source_address is not None:
                raw_socket.bind(self.source_address)
            raw_socket.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
            endpoint = (
                (str(address), self.port)
                if address.version == 4
                else (str(address), self.port, 0, 0)
            )
            raw_socket.connect(endpoint)
            raw_socket.settimeout(self._remaining_timeout())
            wrapped_socket = self._context.wrap_socket(
                raw_socket,
                server_hostname=self.host,
            )
            self.sock = wrapped_socket
            self._connecting_socket = None
        except BaseException:
            self._connecting_socket = None
            raw_socket.close()
            raise

    def send(self, data) -> None:
        if self.sock is not None:
            self.sock.settimeout(self._remaining_timeout())
        super().send(data)


def parse_https_destination(url: str) -> HTTPSDestination:
    parsed = urlsplit(url)
    if parsed.scheme != "https" or parsed.hostname is None:
        raise DestinationRejected("destination is unavailable")
    try:
        port = parsed.port or 443
    except ValueError:
        raise DestinationRejected("destination is unavailable") from None
    request_target = parsed.path
    if parsed.query:
        request_target = f"{request_target}?{parsed.query}"
    authority = parsed.hostname if port == 443 else f"{parsed.hostname}:{port}"
    return HTTPSDestination(
        url=url,
        hostname=parsed.hostname,
        port=port,
        request_target=request_target,
        authority=authority,
    )


def _normalized_ip(value: str) -> ipaddress.IPv4Address | ipaddress.IPv6Address:
    if "%" in value:
        raise DestinationRejected("destination is unavailable")
    try:
        address = ipaddress.ip_address(value)
    except ValueError:
        raise DestinationRejected("destination is unavailable") from None
    if isinstance(address, ipaddress.IPv6Address) and address.ipv4_mapped is not None:
        return address.ipv4_mapped
    return address


def _forbidden_address(address: ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
    return bool(
        address.is_unspecified
        or address.is_loopback
        or address.is_link_local
        or address.is_multicast
        or address.is_reserved
        or any(address in network for network in _METADATA_ADDRESSES)
    )


async def resolve_and_pin(
    destination: HTTPSDestination,
    *,
    allowed_ip_cidrs: list[str],
    require_global: bool,
    timeout_seconds: float,
    resolver: Callable[..., Any] = socket.getaddrinfo,
) -> ResolvedDestination:
    """Resolve once, validate every answer, and select one exact connection IP."""

    def _resolve():
        return resolver(
            destination.hostname,
            destination.port,
            family=socket.AF_UNSPEC,
            type=socket.SOCK_STREAM,
            proto=socket.IPPROTO_TCP,
        )

    try:
        answers = await asyncio.wait_for(asyncio.to_thread(_resolve), timeout=timeout_seconds)
    except (OSError, TimeoutError):
        raise DestinationRejected("destination is unavailable") from None
    addresses: dict[str, ipaddress.IPv4Address | ipaddress.IPv6Address] = {}
    for answer in answers:
        try:
            address = _normalized_ip(answer[4][0])
        except (IndexError, TypeError):
            raise DestinationRejected("destination is unavailable") from None
        addresses[str(address)] = address
    if not addresses or len(addresses) > _MAX_DNS_ANSWERS:
        raise DestinationRejected("destination is unavailable")

    allowlist = tuple(ipaddress.ip_network(value) for value in allowed_ip_cidrs)
    for address in addresses.values():
        if _forbidden_address(address):
            raise DestinationRejected("destination is unavailable")
        if require_global and not address.is_global:
            raise DestinationRejected("destination is unavailable")
        if allowlist and not any(address in network for network in allowlist):
            raise DestinationRejected("destination is unavailable")
    if not require_global and not allowlist:
        raise DestinationRejected("private control-plane destinations require an IP allowlist")

    selected = min(
        addresses.values(),
        key=lambda address: (address.version, address.packed),
    )
    return ResolvedDestination(
        destination=destination,
        pinned_ip=str(selected),
        all_addresses=tuple(sorted(addresses)),
    )


def build_tls_context(files: TLSFiles) -> ssl.SSLContext:
    try:
        context = ssl.create_default_context(cafile=files.ca_file)
        context.minimum_version = ssl.TLSVersion.TLSv1_2
        context.check_hostname = True
        context.verify_mode = ssl.CERT_REQUIRED
        context.set_alpn_protocols(["http/1.1"])
        if files.client_certificate_file and files.client_private_key_file:
            context.load_cert_chain(
                certfile=files.client_certificate_file,
                keyfile=files.client_private_key_file,
            )
        return context
    except (OSError, ssl.SSLError):
        raise MediatorConfigError("mediator TLS material is unavailable") from None


def _perform_pinned_https(request: PinnedHTTPSRequest) -> PinnedHTTPSResponse:
    deadline = time.monotonic() + request.timeout_seconds
    connection = _PinnedHTTPSConnection(
        request.destination.hostname,
        request.destination.port,
        pinned_ip=request.pinned_ip,
        context=request.tls_context,
        timeout=request.timeout_seconds,
        deadline=deadline,
    )
    headers = dict(request.headers)
    headers["host"] = request.destination.authority

    def _set_remaining_timeout() -> None:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise TimeoutError("HTTPS deadline exceeded")
        if connection.sock is None:
            raise OSError("HTTPS socket is unavailable")
        connection.sock.settimeout(remaining)

    watchdog = threading.Timer(request.timeout_seconds, connection.abort)
    watchdog.daemon = True
    watchdog.start()
    try:
        connection.connect()
        _set_remaining_timeout()
        connection.request(
            request.method,
            request.destination.request_target,
            body=request.body,
            headers=headers,
            encode_chunked=False,
        )
        _set_remaining_timeout()
        response = connection.getresponse()
        response_headers = tuple(response.getheaders())
        if (
            len(response_headers) > _MAX_RESPONSE_HEADERS
            or sum(len(name) + len(value) + 4 for name, value in response_headers)
            > _MAX_RESPONSE_HEADER_BYTES
        ):
            raise ResponseRejected("upstream response was rejected")

        content_lengths = [
            value for name, value in response_headers if name.lower() == "content-length"
        ]
        transfer_encodings = [
            value for name, value in response_headers if name.lower() == "transfer-encoding"
        ]
        if len(content_lengths) > 1 or (content_lengths and transfer_encodings):
            raise ResponseRejected("upstream response was rejected")
        if len(transfer_encodings) > 1 or (
            transfer_encodings and transfer_encodings[0].strip().lower() != "chunked"
        ):
            raise ResponseRejected("upstream response was rejected")
        if content_lengths:
            if not content_lengths[0].isdigit():
                raise ResponseRejected("upstream response was rejected")
            declared_length = int(content_lengths[0], 10)
            if declared_length < 0 or declared_length > request.max_response_bytes:
                raise ResponseRejected("upstream response was rejected")

        chunks: list[bytes] = []
        total = 0
        while True:
            _set_remaining_timeout()
            chunk = response.read(min(65_536, request.max_response_bytes - total + 1))
            if not chunk:
                break
            total += len(chunk)
            if total > request.max_response_bytes:
                raise ResponseRejected("upstream response was rejected")
            chunks.append(chunk)
        if content_lengths and total != declared_length:
            raise ResponseRejected("upstream response was rejected")
        return PinnedHTTPSResponse(
            status_code=response.status,
            headers=response_headers,
            body=b"".join(chunks),
        )
    finally:
        watchdog.cancel()
        connection.abort()


async def pinned_https_request(request: PinnedHTTPSRequest) -> PinnedHTTPSResponse:
    try:
        return await asyncio.to_thread(_perform_pinned_https, request)
    except ResponseRejected:
        raise
    except (OSError, ssl.SSLError, http.client.HTTPException, TimeoutError):
        raise PinnedTransportError("HTTPS transport is unavailable") from None


def _json_response(response: PinnedHTTPSResponse) -> Any:
    def _reject_constant(_: str) -> None:
        raise ValueError("non-finite JSON values are forbidden")

    def _unique_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
        document: dict[str, object] = {}
        for key, value in pairs:
            if key in document:
                raise ValueError("duplicate JSON object keys are forbidden")
            document[key] = value
        return document

    try:
        return json.loads(
            response.body.decode("utf-8"),
            object_pairs_hook=_unique_object,
            parse_constant=_reject_constant,
        )
    except (UnicodeDecodeError, json.JSONDecodeError, RecursionError, ValueError):
        raise GateUnavailable("Gate response is unavailable") from None


class GateClient:
    """Pinned, no-retry client for identity checks and permit consumption."""

    def __init__(
        self,
        config: GateControlPlaneConfig,
        *,
        dns_timeout_seconds: float,
        resolver: Callable[..., Any] = socket.getaddrinfo,
    ) -> None:
        self._config = config
        self._dns_timeout_seconds = dns_timeout_seconds
        self._resolver = resolver

    async def _request(
        self,
        *,
        method: str,
        path: str,
        body: bytes,
        api_key: str,
    ) -> PinnedHTTPSResponse:
        destination = parse_https_destination(f"{self._config.base_url}{path}")
        resolved = await resolve_and_pin(
            destination,
            allowed_ip_cidrs=self._config.allowed_ip_cidrs,
            require_global=False,
            timeout_seconds=self._dns_timeout_seconds,
            resolver=self._resolver,
        )
        tls_context = build_tls_context(self._config.tls)
        headers = {
            "accept": "application/json",
            "accept-encoding": "identity",
            "content-length": str(len(body)),
            "x-api-key": api_key,
        }
        if body:
            headers["content-type"] = "application/json"
        return await pinned_https_request(
            PinnedHTTPSRequest(
                destination=destination,
                pinned_ip=resolved.pinned_ip,
                method=method,
                headers=headers,
                body=body,
                timeout_seconds=self._config.timeout_seconds,
                max_response_bytes=_GATE_RESPONSE_LIMIT,
                tls_context=tls_context,
            )
        )

    async def whoami(self) -> GatePrincipal:
        api_key = read_header_secret(self._config.api_key_file, minimum_bytes=16)
        try:
            response = await self._request(
                method="GET",
                path="/v1/identity/whoami",
                body=b"",
                api_key=api_key,
            )
        except (PinnedTransportError, MediatorConfigError):
            raise GateUnavailable("Gate identity is unavailable") from None
        if response.status_code != 200:
            raise GateUnavailable("Gate identity is unavailable")
        try:
            return GatePrincipal.model_validate(_json_response(response))
        except (ValidationError, GateUnavailable):
            raise GateUnavailable("Gate identity is unavailable") from None

    async def consume(
        self,
        permit: PresentedExecutionPermit,
        binding: ExecutionBinding,
    ) -> GateConsumptionReceipt:
        api_key = read_header_secret(self._config.api_key_file, minimum_bytes=16)
        document = {
            "permit_id": str(permit.permit_id),
            "token": permit.token.get_secret_value(),
            "action": binding.action,
            "target_ref": binding.target_ref,
            "decision_id": str(binding.decision_id),
            "execution_request_hash": binding.execution_request_hash,
        }
        try:
            response = await self._request(
                method="POST",
                path="/v1/control/gate/permits/consume",
                body=canonical_json_bytes(document),
                api_key=api_key,
            )
        except (PinnedTransportError, MediatorConfigError):
            raise GateUnavailable("Gate redemption is unavailable") from None
        if response.status_code == 403:
            raise PermitRejected("execution permit is invalid or unusable")
        if response.status_code != 201:
            raise GateUnavailable("Gate redemption is unavailable")
        try:
            receipt = GateConsumptionReceipt.model_validate(_json_response(response))
        except (ValidationError, GateUnavailable):
            raise GateUnavailable("Gate redemption is unavailable") from None
        if (
            receipt.permit_id != permit.permit_id
            or receipt.decision_id != binding.decision_id
            or receipt.consuming_principal_id != permit.enforcement_principal_id
            or receipt.action != binding.action
            or receipt.target_ref != binding.target_ref
            or receipt.execution_request_hash != binding.execution_request_hash
        ):
            raise GateUnavailable("Gate redemption is unavailable")
        return receipt


class ProviderDispatcher:
    """Prepare one exact provider request before consume, then send it once."""

    def __init__(
        self,
        *,
        dns_timeout_seconds: float,
        resolver: Callable[..., Any] = socket.getaddrinfo,
    ) -> None:
        self._dns_timeout_seconds = dns_timeout_seconds
        self._resolver = resolver

    async def validate_route_startup(self, route: MediatorRouteConfig) -> None:
        destination = parse_https_destination(route.upstream_url)
        await resolve_and_pin(
            destination,
            allowed_ip_cidrs=route.allowed_ip_cidrs,
            require_global=True,
            timeout_seconds=self._dns_timeout_seconds,
            resolver=self._resolver,
        )
        read_header_secret(route.credential.secret_file, minimum_bytes=1)
        build_tls_context(route.tls)

    async def prepare(
        self,
        route: MediatorRouteConfig,
        *,
        permit: PresentedExecutionPermit,
        body: bytes,
    ) -> PreparedProviderCall:
        destination = parse_https_destination(route.upstream_url)
        resolved = await resolve_and_pin(
            destination,
            allowed_ip_cidrs=route.allowed_ip_cidrs,
            require_global=True,
            timeout_seconds=self._dns_timeout_seconds,
            resolver=self._resolver,
        )
        credential = read_header_secret(route.credential.secret_file, minimum_bytes=1)
        tls_context = build_tls_context(route.tls)
        headers = build_provider_headers(
            route,
            decision_id=permit.decision_id,
            permit_id=permit.permit_id,
            body_length=len(body),
            credential_secret=credential,
        )
        if (
            len(headers) > 24
            or sum(len(name) + len(value) + 4 for name, value in headers.items()) > 16_384
        ):
            raise MediatorConfigError("configured provider headers exceed the safe limit")
        return PreparedProviderCall(
            route=route,
            request=PinnedHTTPSRequest(
                destination=destination,
                pinned_ip=resolved.pinned_ip,
                method=route.method,
                headers=headers,
                body=body,
                timeout_seconds=route.timeout_seconds,
                max_response_bytes=route.max_response_bytes,
                tls_context=tls_context,
            ),
        )

    async def dispatch(self, prepared: PreparedProviderCall) -> ProviderDispatchResult:
        response = await pinned_https_request(prepared.request)
        if response.status_code < 200 or 300 <= response.status_code <= 399:
            raise ResponseRejected("upstream redirects are forbidden")
        content_types = response.header_values("content-type")
        if len(content_types) > 1:
            raise ResponseRejected("upstream response was rejected")
        content_encodings = response.header_values("content-encoding")
        if len(content_encodings) > 1 or (
            content_encodings and content_encodings[0].strip().lower() != "identity"
        ):
            raise ResponseRejected("upstream response was rejected")
        content_type = content_types[0].split(";", 1)[0].strip().lower() if content_types else None
        if response.body and content_type not in prepared.route.response_content_types:
            raise ResponseRejected("upstream response was rejected")
        return ProviderDispatchResult(
            status_code=response.status_code,
            content_type=content_type,
            body=response.body,
        )


def permit_is_current(permit: PresentedExecutionPermit) -> bool:
    return permit.expires_at.astimezone(UTC) > datetime.now(UTC)
