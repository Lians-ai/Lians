"""Bounded HTTPS transport for the opaque Lians Cloud sync API."""

from __future__ import annotations

import json
import re
import urllib.error
import urllib.request
import uuid
from collections.abc import Callable
from typing import Any
from urllib.parse import urlsplit

from .sync import SyncPreconditionError, SyncProtocolError, SyncState

MAX_CLOUD_REQUEST_BYTES = 1_800_000
MAX_CLOUD_RESPONSE_BYTES = 2_000_000
_CONTROL = re.compile(r"[\x00-\x1f\x7f]")
_HEX_64 = re.compile(r"^[0-9a-f]{64}$")
_LOOPBACK_HOSTS = {"127.0.0.1", "::1", "localhost"}


class SyncCloudError(RuntimeError):
    """A bounded, secret-safe cloud transport failure."""

    def __init__(self, message: str, *, status: int | None = None) -> None:
        super().__init__(message)
        self.status = status


def _origin(value: str) -> str:
    parsed = urlsplit(value.strip())
    if (
        not value.strip()
        or parsed.username is not None
        or parsed.password is not None
        or not parsed.hostname
        or parsed.query
        or parsed.fragment
        or _CONTROL.search(value)
        or any(character.isspace() for character in value)
    ):
        raise ValueError("Lians Cloud URL must be a non-secret HTTPS origin")
    if parsed.scheme != "https" and not (
        parsed.scheme == "http" and parsed.hostname.lower() in _LOOPBACK_HOSTS
    ):
        raise ValueError("Lians Cloud URL must use HTTPS")
    try:
        _port = parsed.port
    except ValueError as exc:
        raise ValueError("Lians Cloud URL has an invalid port") from exc
    if parsed.path not in ("", "/"):
        raise ValueError("Lians Cloud URL must not contain a path")
    return value.strip().rstrip("/")


def _credential(value: str) -> str:
    rendered = value.strip()
    if (
        not rendered
        or len(rendered) > 4096
        or rendered != value
        or _CONTROL.search(rendered)
        or any(character.isspace() for character in rendered)
    ):
        raise ValueError("Lians Cloud credential is invalid")
    return rendered


def _workspace_id(value: str) -> str:
    if not isinstance(value, str):
        raise TypeError("Lians Cloud workspace ID is invalid")
    try:
        parsed = uuid.UUID(value)
    except ValueError as exc:
        raise ValueError("Lians Cloud workspace ID is invalid") from exc
    if str(parsed) != value:
        raise ValueError("Lians Cloud workspace ID is invalid")
    return value


def _request_id(value: str) -> str:
    try:
        return _workspace_id(value)
    except (TypeError, ValueError) as exc:
        raise ValueError("Lians device request ID is invalid") from exc


def _device_id(value: str) -> str:
    if not isinstance(value, str) or not _HEX_64.fullmatch(value):
        raise ValueError("Lians device ID is invalid")
    return value


def _detail(body: bytes, *, status: int) -> str:
    if not body:
        return f"Lians Cloud request failed with status {status}"
    try:
        document = json.loads(body)
        detail = document.get("detail") if isinstance(document, dict) else None
        if isinstance(detail, str) and 0 < len(detail) <= 300 and not _CONTROL.search(detail):
            return detail
        if isinstance(detail, dict):
            message = detail.get("message")
            if (
                isinstance(message, str)
                and 0 < len(message) <= 300
                and not _CONTROL.search(message)
            ):
                return message
    except (json.JSONDecodeError, UnicodeDecodeError):
        pass
    return f"Lians Cloud request failed with status {status}"


class OpaqueSyncHTTPClient:
    """Store only public grants and encrypted revision envelopes over HTTPS."""

    def __init__(
        self,
        base_url: str,
        credential: str | None = None,
        *,
        bearer_token_provider: Callable[[], str] | None = None,
        timeout_seconds: float = 15,
        opener: Any = urllib.request.urlopen,
    ) -> None:
        if not 1 <= timeout_seconds <= 60:
            raise ValueError("Lians Cloud timeout must be between 1 and 60 seconds")
        self.base_url = _origin(base_url)
        if (credential is None) == (bearer_token_provider is None):
            raise ValueError("Configure exactly one Lians Cloud credential provider")
        self._credential = _credential(credential) if credential is not None else None
        self._bearer_token_provider = bearer_token_provider
        self.timeout_seconds = timeout_seconds
        self._opener = opener

    def __repr__(self) -> str:
        mode = "api-key" if self._credential is not None else "oauth"
        return f"OpaqueSyncHTTPClient(base_url={self.base_url!r}, auth={mode!r}, credential=<redacted>)"

    def _request(
        self,
        method: str,
        path: str,
        payload: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        if not path.startswith("/v1/sync/") or ".." in path or _CONTROL.search(path):
            raise ValueError("Lians Cloud request path is invalid")
        encoded = (
            None
            if payload is None
            else json.dumps(
                payload,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            ).encode()
        )
        if encoded is not None and len(encoded) > MAX_CLOUD_REQUEST_BYTES:
            raise SyncProtocolError("Encrypted sync request exceeds the cloud safety limit")
        headers = {
            "Accept": "application/json",
            "Content-Type": "application/json",
            "User-Agent": "Lians-Bridge/0.5 opaque-sync",
        }
        if self._credential is not None:
            headers["X-API-Key"] = self._credential
        else:
            assert self._bearer_token_provider is not None
            headers["Authorization"] = "Bearer " + _credential(self._bearer_token_provider())
        request = urllib.request.Request(
            f"{self.base_url}{path}",
            data=encoded,
            method=method,
            headers=headers,
        )
        try:
            with self._opener(request, timeout=self.timeout_seconds) as response:
                body = response.read(MAX_CLOUD_RESPONSE_BYTES + 1)
                if len(body) > MAX_CLOUD_RESPONSE_BYTES:
                    raise SyncCloudError("Lians Cloud response exceeded the safety limit")
        except urllib.error.HTTPError as exc:
            body = exc.read(4097)[:4096]
            message = _detail(body, status=exc.code)
            if exc.code == 412:
                raise SyncPreconditionError(message) from None
            raise SyncCloudError(message, status=exc.code) from None
        except (TimeoutError, urllib.error.URLError, OSError) as exc:
            raise SyncCloudError("Lians Cloud is unavailable; local memory is unchanged") from exc
        try:
            document = json.loads(body)
        except (json.JSONDecodeError, UnicodeDecodeError) as exc:
            raise SyncCloudError("Lians Cloud returned an invalid response") from exc
        if not isinstance(document, dict):
            raise SyncCloudError("Lians Cloud returned an invalid response")
        return document

    def create_workspace(self, state: SyncState) -> dict[str, Any]:
        return self._request(
            "POST",
            "/v1/sync/workspaces",
            {
                "workspace_id": state.workspace_id,
                "epoch": state.epoch,
                "root_device": state.device,
            },
        )

    def create_enrollment(self, request: dict[str, Any]) -> dict[str, Any]:
        if not isinstance(request, dict):
            raise TypeError("Lians device request is invalid")
        return self._request("POST", "/v1/sync/enrollments", {"request": request})

    def enrollments(self) -> list[dict[str, Any]]:
        document = self._request("GET", "/v1/sync/enrollments")
        enrollments = document.get("enrollments")
        if not isinstance(enrollments, list) or not all(
            isinstance(item, dict) for item in enrollments
        ):
            raise SyncCloudError("Lians Cloud returned an invalid device request list")
        return enrollments

    def enrollment(self, request_id: str) -> dict[str, Any]:
        request_id = _request_id(request_id)
        return self._request("GET", f"/v1/sync/enrollments/{request_id}")

    def approve_enrollment(
        self,
        request_id: str,
        approval: dict[str, Any],
    ) -> dict[str, Any]:
        request_id = _request_id(request_id)
        if not isinstance(approval, dict):
            raise TypeError("Lians device approval is invalid")
        return self._request(
            "POST",
            f"/v1/sync/enrollments/{request_id}/approval",
            {"approval": approval},
        )

    def delete_enrollment(self, request_id: str, *, confirmed: bool = False) -> dict[str, Any]:
        request_id = _request_id(request_id)
        if not confirmed:
            raise ValueError("Device request removal requires confirmed=true")
        return self._request(
            "DELETE",
            f"/v1/sync/enrollments/{request_id}",
            {"confirmed": True},
        )

    def head(self, workspace_id: str) -> dict[str, Any]:
        workspace_id = _workspace_id(workspace_id)
        return self._request("GET", f"/v1/sync/workspaces/{workspace_id}/head")

    def register_approval(self, workspace_id: str, approval: dict[str, Any]) -> dict[str, Any]:
        workspace_id = _workspace_id(workspace_id)
        try:
            payload = {
                "grant": approval["grant"],
                "signature": approval["grant_signature"],
            }
        except (KeyError, TypeError) as exc:
            raise SyncProtocolError("Enrollment approval is invalid or incomplete") from exc
        return self._request(
            "POST",
            f"/v1/sync/workspaces/{workspace_id}/devices",
            payload,
        )

    def grants(self, workspace_id: str) -> list[dict[str, Any]]:
        workspace_id = _workspace_id(workspace_id)
        document = self._request("GET", f"/v1/sync/workspaces/{workspace_id}/devices/grants")
        grants = document.get("grants")
        if not isinstance(grants, list) or not all(isinstance(item, dict) for item in grants):
            raise SyncCloudError("Lians Cloud returned an invalid device registry")
        return grants

    def devices(self, workspace_id: str) -> dict[str, Any]:
        workspace_id = _workspace_id(workspace_id)
        document = self._request("GET", f"/v1/sync/workspaces/{workspace_id}/devices")
        devices = document.get("devices")
        if not isinstance(devices, list) or not all(isinstance(item, dict) for item in devices):
            raise SyncCloudError("Lians Cloud returned an invalid device registry")
        return document

    def key_rotations(self, workspace_id: str, *, after: int) -> dict[str, Any]:
        workspace_id = _workspace_id(workspace_id)
        if type(after) is not int or after < 0:
            raise ValueError("Lians key-rotation cursor is invalid")
        document = self._request(
            "GET",
            f"/v1/sync/workspaces/{workspace_id}/key-rotations?after={after}",
        )
        rotations = document.get("rotations")
        if not isinstance(rotations, list) or not all(isinstance(item, dict) for item in rotations):
            raise SyncCloudError("Lians Cloud returned an invalid key-rotation list")
        return document

    def remove_device(
        self,
        workspace_id: str,
        device_id: str,
        rotation: dict[str, Any],
        *,
        confirmed: bool = False,
    ) -> dict[str, Any]:
        workspace_id = _workspace_id(workspace_id)
        device_id = _device_id(device_id)
        if not confirmed:
            raise ValueError("Protecting future memory requires confirmed=true")
        if not isinstance(rotation, dict) or set(rotation) != {"rotation", "signature"}:
            raise TypeError("Lians workspace-key rotation is invalid")
        return self._request(
            "POST",
            f"/v1/sync/workspaces/{workspace_id}/devices/{device_id}/remove",
            rotation,
        )

    def push(self, workspace_id: str, envelope: dict[str, Any]) -> dict[str, Any]:
        workspace_id = _workspace_id(workspace_id)
        return self._request(
            "POST",
            f"/v1/sync/workspaces/{workspace_id}/revisions",
            {"envelope": envelope},
        )

    def revisions_after(
        self,
        workspace_id: str,
        revision: int,
        *,
        limit: int = 100,
    ) -> dict[str, Any]:
        workspace_id = _workspace_id(workspace_id)
        if type(revision) is not int or revision < 0 or not 1 <= limit <= 100:
            raise ValueError("Lians Cloud sync cursor is invalid")
        return self._request(
            "GET",
            f"/v1/sync/workspaces/{workspace_id}/revisions?after={revision}&limit={limit}",
        )

    def delete_workspace(self, workspace_id: str, *, confirmed: bool = False) -> dict[str, Any]:
        workspace_id = _workspace_id(workspace_id)
        if not confirmed:
            raise ValueError("Cloud deletion requires confirmed=true")
        return self._request(
            "DELETE",
            f"/v1/sync/workspaces/{workspace_id}",
            {
                "confirmed": True,
                "confirmation": "DELETE ENCRYPTED LIANS CLOUD MEMORY",
            },
        )
