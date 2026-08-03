"""Shared capture minimization for telemetry and Recorder ingestion.

The same rules protect every protocol path. Content is omitted or replaced by
an integrity hash, while secret-shaped fields are always redacted before any
hash is calculated so stored digests cannot become credential-guessing oracles.
"""
from __future__ import annotations

import hashlib
import json
import re
from typing import Any, Collection

CAPTURE_MODES = frozenset({"metadata_only", "hash_only", "full"})

_SECRET_KEYS = frozenset(
    {
        "api_key",
        "apikey",
        "authorization",
        "bearer",
        "client_secret",
        "connection_string",
        "cookie",
        "credential",
        "credentials",
        "database_url",
        "dsn",
        "id_token",
        "passwd",
        "password",
        "passphrase",
        "private_key",
        "proxy_authorization",
        "refresh_token",
        "secret",
        "set_cookie",
        "signing_key",
        "token",
        "access_token",
        "webhook_secret",
        "webhook_signature",
        "x_api_key",
    }
)
_SECRET_SUFFIXES = (
    "_api_key",
    "_access_token",
    "_client_secret",
    "_id_token",
    "_password",
    "_passwd",
    "_private_key",
    "_proxy_authorization",
    "_refresh_token",
    "_secret",
    "_signing_key",
    "_token",
    "_webhook_signature",
)
_CONTENT_KEYS = frozenset(
    {
        "args",
        "arguments",
        "artifacts",
        "completion",
        "completions",
        "content",
        "input",
        "inputs",
        "messages",
        "output",
        "outputs",
        "parts",
        "prompt",
        "prompts",
        "result",
        "results",
    }
)

# Only protocol structure and bounded operational identifiers survive minimized
# capture verbatim. Unknown vendor metadata is omitted or hashed as one value,
# preventing arbitrary dictionaries from becoming a plaintext side channel.
_STRUCTURAL_KEYS = frozenset(
    {
        "actor",
        "attributes",
        "correlation",
        "data",
        "events",
        "instrumentation_scope",
        "links",
        "payload",
        "resource",
        "scope",
        "semantic",
        "spans",
    }
)
_SAFE_METADATA_KEYS = frozenset(
    {
        "acr",
        "agent_id",
        "agent_name",
        "agent_version",
        "algorithm",
        "amr",
        "auth_method",
        "auth_time",
        "boundary_kind",
        "capture_mode",
        "chain_position",
        "context_id",
        "correlation_type",
        "decision_id",
        "duration_ms",
        "end_time_unix_nano",
        "error_type",
        "event_id",
        "event_kind",
        "event_name",
        "event_type",
        "finish_reason",
        "idempotency_key_hash",
        "input_hash",
        "input_tokens",
        "issuer",
        "key_id",
        "max_tokens",
        "message_id",
        "model",
        "model_id",
        "model_name",
        "model_version",
        "name",
        "operation",
        "output_hash",
        "output_tokens",
        "parent_span_id",
        "phase",
        "policy_version",
        "principal_id",
        "principal_ref",
        "protocol",
        "provider",
        "recorded_at",
        "request_id",
        "response_id",
        "roles",
        "run_id",
        "schema_version",
        "service_name",
        "service_version",
        "session_id",
        "span_id",
        "start_time_unix_nano",
        "status",
        "status_code",
        "system",
        "task_id",
        "timestamp",
        "time_unix_nano",
        "token_count",
        "tool_call_id",
        "tool_name",
        "total_tokens",
        "trace_id",
        "usage_input_tokens",
        "usage_output_tokens",
    }
)
_CAMEL_BOUNDARY = re.compile(r"(?<=[a-z0-9])(?=[A-Z])")
_KEY_SEPARATOR = re.compile(r"[^a-z0-9]+")
_CREDENTIAL_URL = re.compile(r"^[a-z][a-z0-9+.-]*://[^/@\s:]+:[^/@\s]+@", re.I)
_JWT_VALUE = re.compile(r"^[A-Za-z0-9_-]{12,}\.[A-Za-z0-9_-]{12,}\.[A-Za-z0-9_-]{12,}$")


def canonical_json(value: Any) -> str:
    """Serialize a capture value deterministically and reject NaN/infinity."""
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
        default=str,
    )


def capture_sha256(value: Any) -> str:
    """Hash strings as supplied and structured values in canonical form."""
    source = value if isinstance(value, str) else canonical_json(value)
    return hashlib.sha256(source.encode("utf-8")).hexdigest()


def _normalized_key(field_name: str | None) -> str:
    value = _CAMEL_BOUNDARY.sub("_", (field_name or "").strip()).casefold()
    return _KEY_SEPARATOR.sub("_", value).strip("_")


def _key_candidates(field_name: str | None) -> set[str]:
    raw = (field_name or "").strip()
    segments = [segment for segment in re.split(r"[./:]", raw) if segment]
    candidates = {_normalized_key(raw)}
    candidates.update(_normalized_key(segment) for segment in segments)
    candidates.discard("")
    return candidates


def _is_secret_field(field_name: str | None) -> bool:
    for candidate in _key_candidates(field_name):
        if candidate in _SECRET_KEYS:
            return True
        if candidate.endswith(_SECRET_SUFFIXES) and not candidate.endswith(
            ("_credential_id", "_token_count", "_tokens")
        ):
            return True
    return False


def _is_named(field_name: str | None, names: Collection[str]) -> bool:
    candidates = _key_candidates(field_name)
    return bool(candidates.intersection(names))


def _looks_like_secret_value(value: Any) -> bool:
    if not isinstance(value, str):
        return False
    candidate = value.strip()
    lowered = candidate.casefold()
    return bool(
        lowered.startswith(("bearer ", "basic ", "sk-", "ghp_", "xoxb-"))
        or "-----begin private key-----" in lowered
        or "-----begin rsa private key-----" in lowered
        or _CREDENTIAL_URL.match(candidate)
        or _JWT_VALUE.fullmatch(candidate)
    )


def _minimized_value(
    value: Any,
    *,
    mode: str,
    sensitive_fields: Collection[str],
) -> dict[str, str]:
    if mode == "metadata_only":
        return {"$captured": "omitted"}
    redacted = sanitize_capture(
        value,
        mode="full",
        sensitive_fields=sensitive_fields,
    )
    return {"$captured": "hash_only", "$sha256": capture_sha256(redacted)}


def sanitize_capture(
    value: Any,
    *,
    mode: str,
    sensitive_fields: Collection[str] = (),
    field_name: str | None = None,
) -> Any:
    """Apply a deployment capture mode recursively to an arbitrary value."""
    if mode not in CAPTURE_MODES:
        raise ValueError(f"unsupported capture mode: {mode!r}")

    normalized_name = _normalized_key(field_name)
    sensitive = {_normalized_key(str(item)) for item in sensitive_fields}

    if (
        _is_secret_field(field_name)
        or normalized_name in sensitive
        or bool(_key_candidates(field_name).intersection(sensitive))
    ):
        return {"$captured": "redacted"}
    if _looks_like_secret_value(value):
        return {"$captured": "redacted"}

    if _is_named(field_name, _CONTENT_KEYS):
        if mode == "metadata_only":
            return {"$captured": "omitted"}
        if mode == "hash_only":
            redacted = sanitize_capture(
                value,
                mode="full",
                sensitive_fields=sensitive,
            )
            return {"$captured": "hash_only", "$sha256": capture_sha256(redacted)}

    if isinstance(value, dict):
        sanitized: dict[str, Any] = {}
        for key, item in value.items():
            key_text = str(key)
            if (
                mode == "full"
                or _is_secret_field(key_text)
                or _is_named(key_text, _CONTENT_KEYS)
                or _is_named(key_text, _STRUCTURAL_KEYS)
                or _is_named(key_text, _SAFE_METADATA_KEYS)
                or _normalized_key(key_text) in sensitive
            ):
                sanitized[key_text] = sanitize_capture(
                    item,
                    mode=mode,
                    sensitive_fields=sensitive,
                    field_name=key_text,
                )
            else:
                sanitized[key_text] = _minimized_value(
                    item,
                    mode=mode,
                    sensitive_fields=sensitive,
                )
        return sanitized
    if isinstance(value, (list, tuple)):
        if field_name and mode != "full" and not (
            _is_named(field_name, _STRUCTURAL_KEYS)
            or _is_named(field_name, _SAFE_METADATA_KEYS)
        ):
            return _minimized_value(
                value,
                mode=mode,
                sensitive_fields=sensitive,
            )
        return [
            sanitize_capture(item, mode=mode, sensitive_fields=sensitive)
            for item in value
        ]
    if field_name and mode != "full" and not _is_named(
        field_name, _SAFE_METADATA_KEYS
    ):
        return _minimized_value(
            value,
            mode=mode,
            sensitive_fields=sensitive,
        )
    return value
