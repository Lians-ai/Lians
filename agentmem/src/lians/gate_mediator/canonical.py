"""Canonical request binding shared by prepare and execute."""

from __future__ import annotations

import hashlib
import json
import math
from dataclasses import dataclass
from uuid import UUID

from .config import MediatorRouteConfig

CANONICALIZATION_ID = "lians-http-execution-v1"


class RequestContractViolation(ValueError):
    """Raw request bytes do not satisfy the immutable route contract."""


def canonical_json_bytes(value: object) -> bytes:
    """Serialize the deliberately integer/string-only binding format."""
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
        allow_nan=False,
    ).encode("ascii")


def sha256_hex(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def validate_request_body(route: MediatorRouteConfig, body: bytes) -> None:
    """Validate JSON structure without rewriting the exact outbound bytes."""
    if route.allowed_json_top_level_fields is None:
        return

    def _reject_constant(_: str) -> None:
        raise RequestContractViolation("non-finite JSON numbers are forbidden")

    def _unique_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
        result: dict[str, object] = {}
        for key, value in pairs:
            if key in result:
                raise RequestContractViolation("duplicate JSON object keys are forbidden")
            result[key] = value
        return result

    try:
        document = json.loads(
            body.decode("utf-8"),
            object_pairs_hook=_unique_object,
            parse_constant=_reject_constant,
        )
    except RequestContractViolation:
        raise
    except (UnicodeDecodeError, json.JSONDecodeError, RecursionError, ValueError):
        raise RequestContractViolation("request body is not valid bounded JSON") from None
    if not isinstance(document, dict):
        raise RequestContractViolation("JSON request body must be an object")
    allowed = set(route.allowed_json_top_level_fields)
    required = set(route.required_json_top_level_fields)
    if not set(document).issubset(allowed) or not required.issubset(document):
        raise RequestContractViolation("JSON request fields violate the route contract")

    nodes = 0
    stack: list[tuple[object, int]] = [(document, 1)]
    while stack:
        value, depth = stack.pop()
        nodes += 1
        if nodes > route.max_json_nodes or depth > route.max_json_depth:
            raise RequestContractViolation("JSON request exceeds structural limits")
        if isinstance(value, dict):
            if any(any(0xD800 <= ord(character) <= 0xDFFF for character in key) for key in value):
                raise RequestContractViolation("JSON request contains invalid Unicode")
            stack.extend((child, depth + 1) for child in value.values())
        elif isinstance(value, list):
            stack.extend((child, depth + 1) for child in value)
        elif isinstance(value, float) and not math.isfinite(value):
            raise RequestContractViolation("non-finite JSON numbers are forbidden")
        elif isinstance(value, str) and any(
            0xD800 <= ord(character) <= 0xDFFF for character in value
        ):
            raise RequestContractViolation("JSON request contains invalid Unicode")


def route_config_sha256(route: MediatorRouteConfig) -> str:
    return sha256_hex(canonical_json_bytes(route.security_manifest()))


def idempotency_value(route: MediatorRouteConfig, decision_id: UUID) -> str | None:
    if route.idempotency_header_name is None:
        return None
    material = canonical_json_bytes(
        {
            "schema": "lians-route-decision-idempotency-v1",
            "route_id": route.route_id,
            "decision_id": str(decision_id),
        }
    )
    return f"lians-{sha256_hex(material)}"


def _security_relevant_headers(
    route: MediatorRouteConfig,
    decision_id: UUID,
    body_length: int,
) -> dict[str, str]:
    headers = dict(route.fixed_headers)
    headers.update(
        {
            "accept": ", ".join(route.response_content_types),
            "accept-encoding": "identity",
            "content-length": str(body_length),
            "content-type": route.request_content_type,
        }
    )
    stable_idempotency_value = idempotency_value(route, decision_id)
    if route.idempotency_header_name and stable_idempotency_value:
        headers[route.idempotency_header_name] = stable_idempotency_value
    return dict(sorted(headers.items()))


@dataclass(frozen=True)
class ExecutionBinding:
    route_id: str
    route_config_sha256: str
    canonicalization: str
    action: str
    target_ref: str
    decision_id: UUID
    request_body_sha256: str
    request_body_bytes: int
    execution_request_hash: str
    canonical_envelope: bytes


def derive_execution_binding(
    route: MediatorRouteConfig,
    decision_id: UUID,
    body: bytes,
) -> ExecutionBinding:
    """Bind the exact body and every security-relevant configured argument.

    Provider credential bytes and the permit-ID correlation header are injected
    by the mediator but are deliberately excluded.  Their stable authority and
    non-authorizing semantics are committed through the route manifest.
    """
    body_digest = sha256_hex(body)
    route_digest = route_config_sha256(route)
    envelope = {
        "schema": CANONICALIZATION_ID,
        "route_config_sha256": route_digest,
        "route": route.security_manifest(),
        "action": route.action,
        "target_ref": route.target_ref,
        "decision_id": str(decision_id),
        "provider_request": {
            "method": route.method,
            "url": route.upstream_url,
            "headers": _security_relevant_headers(route, decision_id, len(body)),
            "body_sha256": body_digest,
            "body_bytes": len(body),
            "credential_value": {
                "included": False,
                "bound_as": route.credential.binding_ref,
                "reason": "server-held-rotating-secret",
            },
            "audit_correlation_value": {
                "included": False,
                "reason": "non-authorizing-permit-id-created-after-evaluation",
            },
        },
    }
    canonical = canonical_json_bytes(envelope)
    return ExecutionBinding(
        route_id=route.route_id,
        route_config_sha256=route_digest,
        canonicalization=CANONICALIZATION_ID,
        action=route.action,
        target_ref=route.target_ref,
        decision_id=decision_id,
        request_body_sha256=body_digest,
        request_body_bytes=len(body),
        execution_request_hash=sha256_hex(canonical),
        canonical_envelope=canonical,
    )


def build_provider_headers(
    route: MediatorRouteConfig,
    *,
    decision_id: UUID,
    permit_id: UUID,
    body_length: int,
    credential_secret: str,
) -> dict[str, str]:
    """Construct upstream headers from scratch; no caller header is forwarded."""
    headers = _security_relevant_headers(route, decision_id, body_length)
    headers[route.credential.header_name] = f"{route.credential.value_prefix}{credential_secret}"
    headers[route.audit_correlation_header_name] = str(permit_id)
    return headers
