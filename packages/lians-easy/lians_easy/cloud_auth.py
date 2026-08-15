"""Native OAuth 2.1 sign-in for Lians Bridge.

The Bridge is a public native client: it has no client secret, opens the system
browser, uses Authorization Code + PKCE (S256), receives the redirect only on a
random loopback port, and encrypts rotating tokens with the OS-protected Lians
root key. The React application sees status only, never credentials.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import re
import secrets
import tempfile
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import webbrowser
from dataclasses import dataclass
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from cryptography.exceptions import InvalidTag

from .crypto import LocalCipher

MAX_METADATA_BYTES = 64 * 1024
MAX_TOKEN_RESPONSE_BYTES = 64 * 1024
MAX_CALLBACK_PATH_BYTES = 8 * 1024
TOKEN_ASSOCIATED_DATA = b"lians-cloud-oauth-session-v1"
_CONTROL = re.compile(r"[\x00-\x1f\x7f]")
_LOOPBACK_HOSTS = {"127.0.0.1", "::1", "localhost"}
_DEFAULT_SCOPES = ("openid", "profile", "email", "offline_access", "memory:sync")


class CloudAuthError(RuntimeError):
    """A safe, user-actionable native sign-in failure."""


def _https_url(value: str, *, label: str, allow_loopback: bool = False) -> str:
    candidate = value.strip()
    parsed = urlsplit(candidate)
    if (
        not candidate
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
        or parsed.query
        or parsed.fragment
        or _CONTROL.search(candidate)
        or any(character.isspace() for character in candidate)
    ):
        raise ValueError(f"{label} must be a non-secret HTTPS URL")
    if parsed.scheme != "https" and not (
        allow_loopback
        and parsed.scheme == "http"
        and parsed.hostname.lower() in _LOOPBACK_HOSTS
    ):
        raise ValueError(f"{label} must use HTTPS")
    try:
        _port = parsed.port
    except ValueError as exc:
        raise ValueError(f"{label} has an invalid port") from exc
    return candidate.rstrip("/")


def _public_value(value: str, *, label: str, maximum: int = 1024) -> str:
    candidate = value.strip()
    if (
        not candidate
        or len(candidate) > maximum
        or candidate != value
        or _CONTROL.search(candidate)
        or any(character.isspace() for character in candidate)
    ):
        raise ValueError(f"{label} is invalid")
    return candidate


def _token(value: Any, *, label: str, required: bool = True) -> str:
    if value is None and not required:
        return ""
    if not isinstance(value, str):
        raise CloudAuthError(f"Lians Cloud returned an invalid {label}")
    if (
        not value
        or len(value) > 16_384
        or _CONTROL.search(value)
        or any(character.isspace() for character in value)
    ):
        raise CloudAuthError(f"Lians Cloud returned an invalid {label}")
    return value


def _bounded_json_response(response: Any, maximum: int) -> dict[str, Any]:
    body = response.read(maximum + 1)
    if len(body) > maximum:
        raise CloudAuthError("Lians Cloud returned an oversized sign-in response")
    try:
        document = json.loads(body)
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise CloudAuthError("Lians Cloud returned an invalid sign-in response") from exc
    if not isinstance(document, dict):
        raise CloudAuthError("Lians Cloud returned an invalid sign-in response")
    return document


@dataclass(frozen=True)
class CloudAuthConfig:
    issuer: str
    client_id: str
    audience: str
    cloud_url: str
    scopes: tuple[str, ...] = _DEFAULT_SCOPES
    timeout_seconds: int = 180

    def __post_init__(self) -> None:
        if not 30 <= self.timeout_seconds <= 600:
            raise ValueError("Lians sign-in timeout must be between 30 and 600 seconds")
        if self.issuer:
            _https_url(self.issuer, label="Lians OAuth issuer")
        if self.client_id:
            _public_value(self.client_id, label="Lians OAuth client ID")
        if bool(self.issuer) != bool(self.client_id):
            raise ValueError("Lians OAuth issuer and client ID must be configured together")
        if self.audience:
            _https_url(self.audience, label="Lians OAuth audience")
        _https_url(self.cloud_url, label="Lians Cloud URL", allow_loopback=True)
        if not self.scopes or "memory:sync" not in self.scopes:
            raise ValueError("Lians OAuth scopes must include memory:sync")
        for scope in self.scopes:
            _public_value(scope, label="Lians OAuth scope", maximum=128)

    @property
    def configured(self) -> bool:
        return bool(self.issuer and self.client_id and self.audience)

    @classmethod
    def from_environment(cls) -> CloudAuthConfig:
        cloud_url = os.getenv("LIANS_CLOUD_URL", "https://api.lians.ai").strip()
        scopes = tuple(
            value
            for value in os.getenv("LIANS_OAUTH_SCOPES", " ".join(_DEFAULT_SCOPES)).split()
            if value
        )
        return cls(
            issuer=os.getenv("LIANS_OAUTH_ISSUER", "").strip().rstrip("/"),
            client_id=os.getenv("LIANS_OAUTH_CLIENT_ID", "").strip(),
            audience=os.getenv("LIANS_OAUTH_AUDIENCE", cloud_url).strip().rstrip("/"),
            cloud_url=cloud_url.rstrip("/"),
            scopes=scopes,
            timeout_seconds=int(os.getenv("LIANS_OAUTH_TIMEOUT_SECONDS", "180")),
        )


@dataclass(frozen=True)
class CloudSession:
    access_token: str
    refresh_token: str
    expires_at: int
    scope: tuple[str, ...]

    @classmethod
    def from_document(cls, document: dict[str, Any]) -> CloudSession:
        access_token = _token(document.get("access_token"), label="access token")
        refresh_token = _token(document.get("refresh_token"), label="refresh token")
        expires_at = document.get("expires_at")
        scopes = document.get("scope")
        if type(expires_at) is not int or expires_at <= 0:
            raise CloudAuthError("Saved Lians sign-in has an invalid expiry")
        if not isinstance(scopes, list) or not all(
            isinstance(scope, str) and 0 < len(scope) <= 128 for scope in scopes
        ):
            raise CloudAuthError("Saved Lians sign-in has invalid permissions")
        return cls(
            access_token=access_token,
            refresh_token=refresh_token,
            expires_at=expires_at,
            scope=tuple(scopes),
        )

    def document(self) -> dict[str, Any]:
        return {
            "access_token": self.access_token,
            "refresh_token": self.refresh_token,
            "expires_at": self.expires_at,
            "scope": list(self.scope),
        }


class CloudTokenVault:
    """Encrypt OAuth tokens separately from memory and publish atomically."""

    def __init__(self, path: str | Path, cipher: LocalCipher) -> None:
        self.path = Path(path).expanduser()
        self.cipher = cipher

    def load(self) -> CloudSession | None:
        if not self.path.exists():
            return None
        try:
            envelope = json.loads(self.path.read_text(encoding="utf-8"))
            if envelope.get("version") != 1:
                raise ValueError
            ciphertext = base64.b64decode(envelope["ciphertext"], validate=True)
            nonce = base64.b64decode(envelope["nonce"], validate=True)
            plaintext = self.cipher.open(
                ciphertext,
                nonce,
                associated_data=TOKEN_ASSOCIATED_DATA,
            )
            document = json.loads(plaintext)
            if not isinstance(document, dict):
                raise TypeError
            return CloudSession.from_document(document)
        except (
            InvalidTag,
            KeyError,
            TypeError,
            ValueError,
            json.JSONDecodeError,
            UnicodeDecodeError,
        ) as exc:
            raise CloudAuthError("Saved Lians sign-in is invalid; sign in again") from exc

    def save(self, session: CloudSession) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        plaintext = json.dumps(
            session.document(),
            ensure_ascii=True,
            sort_keys=True,
            separators=(",", ":"),
        )
        ciphertext, nonce = self.cipher.seal(
            plaintext,
            associated_data=TOKEN_ASSOCIATED_DATA,
        )
        envelope = json.dumps(
            {
                "version": 1,
                "ciphertext": base64.b64encode(ciphertext).decode("ascii"),
                "nonce": base64.b64encode(nonce).decode("ascii"),
            },
            sort_keys=True,
        ) + "\n"
        descriptor, temporary_name = tempfile.mkstemp(
            prefix=f".{self.path.name}.",
            suffix=".tmp",
            dir=self.path.parent,
            text=True,
        )
        temporary = Path(temporary_name)
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as handle:
                handle.write(envelope)
                handle.flush()
                os.fsync(handle.fileno())
            if os.name != "nt":
                temporary.chmod(0o600)
            os.replace(temporary, self.path)
        finally:
            temporary.unlink(missing_ok=True)

    def clear(self) -> bool:
        existed = self.path.exists()
        self.path.unlink(missing_ok=True)
        return existed


class NativeCloudAuth:
    """System-browser PKCE sign-in and automatic refresh-token rotation."""

    def __init__(
        self,
        config: CloudAuthConfig,
        vault: CloudTokenVault,
        *,
        opener: Any = urllib.request.urlopen,
        browser_opener: Any = webbrowser.open,
        clock: Any = time.time,
    ) -> None:
        self.config = config
        self.vault = vault
        self._opener = opener
        self._browser_opener = browser_opener
        self._clock = clock
        self._lock = threading.RLock()
        self._metadata: dict[str, str] | None = None

    @classmethod
    def for_store(cls, store: Any) -> NativeCloudAuth:
        return cls(
            CloudAuthConfig.from_environment(),
            CloudTokenVault(store.path.with_name("cloud-session.json"), store.cipher),
        )

    def _request_json(self, request: urllib.request.Request, maximum: int) -> dict[str, Any]:
        try:
            with self._opener(request, timeout=15) as response:
                return _bounded_json_response(response, maximum)
        except urllib.error.HTTPError as exc:
            exc.read(4096)
            raise CloudAuthError("Lians sign-in was rejected; try signing in again") from None
        except (TimeoutError, urllib.error.URLError, OSError) as exc:
            raise CloudAuthError("Lians sign-in is temporarily unavailable") from exc

    def _discovery(self) -> dict[str, str]:
        if not self.config.configured:
            raise CloudAuthError("Lians Cloud sign-in is not configured in this build")
        if self._metadata is not None:
            return self._metadata
        discovery_url = f"{self.config.issuer}/.well-known/openid-configuration"
        request = urllib.request.Request(
            discovery_url,
            headers={"Accept": "application/json", "User-Agent": "Lians-Bridge/0.5 native-oauth"},
        )
        document = self._request_json(request, MAX_METADATA_BYTES)
        if document.get("issuer", "").rstrip("/") != self.config.issuer.rstrip("/"):
            raise CloudAuthError("Lians sign-in provider identity did not match")
        metadata: dict[str, str] = {}
        for field in ("authorization_endpoint", "token_endpoint"):
            value = document.get(field)
            if not isinstance(value, str):
                raise CloudAuthError("Lians sign-in provider metadata is incomplete")
            try:
                endpoint = _https_url(value, label="Lians OAuth endpoint")
            except ValueError as exc:
                raise CloudAuthError("Lians sign-in provider metadata is unsafe") from exc
            if urlsplit(endpoint).hostname != urlsplit(self.config.issuer).hostname:
                raise CloudAuthError("Lians sign-in provider endpoint did not match")
            metadata[field] = endpoint
        self._metadata = metadata
        return metadata

    def _exchange(self, values: dict[str, str]) -> dict[str, Any]:
        token_endpoint = self._discovery()["token_endpoint"]
        encoded = urllib.parse.urlencode(values).encode("ascii")
        request = urllib.request.Request(
            token_endpoint,
            data=encoded,
            method="POST",
            headers={
                "Accept": "application/json",
                "Content-Type": "application/x-www-form-urlencoded",
                "User-Agent": "Lians-Bridge/0.5 native-oauth",
            },
        )
        return self._request_json(request, MAX_TOKEN_RESPONSE_BYTES)

    def _session_from_token_response(
        self,
        document: dict[str, Any],
        *,
        previous_refresh_token: str = "",
    ) -> CloudSession:
        if str(document.get("token_type", "")).casefold() != "bearer":
            raise CloudAuthError("Lians Cloud returned an unsupported token type")
        access_token = _token(document.get("access_token"), label="access token")
        refresh_token = _token(
            document.get("refresh_token") or previous_refresh_token,
            label="refresh token",
        )
        expires_in = document.get("expires_in")
        if type(expires_in) not in {int, float} or not 60 <= int(expires_in) <= 86_400:
            raise CloudAuthError("Lians Cloud returned an invalid token lifetime")
        scope_value = document.get("scope")
        scopes = tuple(scope_value.split()) if isinstance(scope_value, str) else self.config.scopes
        if "memory:sync" not in scopes:
            raise CloudAuthError("Lians Cloud did not grant memory sync permission")
        return CloudSession(
            access_token=access_token,
            refresh_token=refresh_token,
            expires_at=int(self._clock()) + int(expires_in),
            scope=scopes,
        )

    @staticmethod
    def _callback_handler(
        *,
        expected_state: str,
        completed: threading.Event,
        result: dict[str, str],
    ) -> type[BaseHTTPRequestHandler]:
        class CallbackHandler(BaseHTTPRequestHandler):
            server_version = "LiansOAuthCallback/1"

            def log_message(self, format: str, *args: Any) -> None:
                return

            def _page(self, status: int, heading: str, message: str) -> None:
                body = (
                    "<!doctype html><meta charset=utf-8>"
                    f"<title>{heading}</title><h1>{heading}</h1><p>{message}</p>"
                    "<p>You can close this window and return to Lians.</p>"
                ).encode()
                self.send_response(status)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.send_header("Cache-Control", "no-store")
                self.send_header("X-Content-Type-Options", "nosniff")
                self.send_header(
                    "Content-Security-Policy",
                    "default-src 'none'; style-src 'unsafe-inline'; frame-ancestors 'none'",
                )
                self.end_headers()
                self.wfile.write(body)

            def do_GET(self) -> None:
                if len(self.path.encode("utf-8")) > MAX_CALLBACK_PATH_BYTES:
                    self._page(HTTPStatus.REQUEST_URI_TOO_LONG, "Sign-in stopped", "The response was too large.")
                    return
                parsed = urllib.parse.urlsplit(self.path)
                if parsed.path != "/oauth/callback":
                    self._page(HTTPStatus.NOT_FOUND, "Not found", "This is not a Lians sign-in response.")
                    return
                query = urllib.parse.parse_qs(parsed.query, keep_blank_values=True)
                state = query.get("state", [""])[0]
                if not hmac.compare_digest(state, expected_state):
                    self._page(HTTPStatus.BAD_REQUEST, "Sign-in stopped", "The security check did not match.")
                    return
                if completed.is_set():
                    self._page(HTTPStatus.CONFLICT, "Already complete", "This sign-in response was already used.")
                    return
                provider_error = query.get("error", [""])[0]
                code = query.get("code", [""])[0]
                if provider_error:
                    result["error"] = "Lians sign-in was cancelled or denied"
                    completed.set()
                    self._page(HTTPStatus.BAD_REQUEST, "Sign-in stopped", "Lians was not connected.")
                    return
                try:
                    result["code"] = _public_value(code, label="OAuth authorization code", maximum=4096)
                except ValueError:
                    result["error"] = "Lians sign-in returned an invalid authorization code"
                    completed.set()
                    self._page(HTTPStatus.BAD_REQUEST, "Sign-in stopped", "Lians was not connected.")
                    return
                completed.set()
                self._page(HTTPStatus.OK, "Lians is connected", "Your encrypted memory can now sync.")

        return CallbackHandler

    def sign_in(self) -> dict[str, Any]:
        with self._lock:
            metadata = self._discovery()
            state = secrets.token_urlsafe(32)
            verifier = secrets.token_urlsafe(64)
            challenge = base64.urlsafe_b64encode(
                hashlib.sha256(verifier.encode("ascii")).digest()
            ).rstrip(b"=").decode("ascii")
            completed = threading.Event()
            result: dict[str, str] = {}
            callback_server = ThreadingHTTPServer(
                ("127.0.0.1", 0),
                self._callback_handler(
                    expected_state=state,
                    completed=completed,
                    result=result,
                ),
            )
            callback_thread = threading.Thread(
                target=callback_server.serve_forever,
                name="lians-oauth-loopback",
                daemon=True,
            )
            callback_thread.start()
            redirect_uri = f"http://127.0.0.1:{callback_server.server_port}/oauth/callback"
            authorization_values = {
                "response_type": "code",
                "client_id": self.config.client_id,
                "redirect_uri": redirect_uri,
                "scope": " ".join(self.config.scopes),
                "state": state,
                "code_challenge": challenge,
                "code_challenge_method": "S256",
                "audience": self.config.audience,
            }
            authorization_url = (
                metadata["authorization_endpoint"]
                + "?"
                + urllib.parse.urlencode(authorization_values)
            )
            try:
                if not self._browser_opener(authorization_url):
                    raise CloudAuthError("Lians could not open your browser for sign-in")
                if not completed.wait(self.config.timeout_seconds):
                    raise CloudAuthError("Lians sign-in timed out; no account was connected")
            finally:
                callback_server.shutdown()
                callback_server.server_close()
                callback_thread.join(timeout=2)
            if result.get("error"):
                raise CloudAuthError(result["error"])
            code = result.get("code")
            if not code:
                raise CloudAuthError("Lians sign-in did not return an authorization code")
            token_document = self._exchange(
                {
                    "grant_type": "authorization_code",
                    "client_id": self.config.client_id,
                    "code": code,
                    "redirect_uri": redirect_uri,
                    "code_verifier": verifier,
                }
            )
            session = self._session_from_token_response(token_document)
            self.vault.save(session)
            return self.status()

    def access_token(self) -> str:
        with self._lock:
            session = self.vault.load()
            if session is None:
                raise CloudAuthError("Sign in to Lians Cloud to sync memory")
            if session.expires_at > int(self._clock()) + 60:
                return session.access_token
            document = self._exchange(
                {
                    "grant_type": "refresh_token",
                    "client_id": self.config.client_id,
                    "refresh_token": session.refresh_token,
                }
            )
            refreshed = self._session_from_token_response(
                document,
                previous_refresh_token=session.refresh_token,
            )
            self.vault.save(refreshed)
            return refreshed.access_token

    def status(self) -> dict[str, Any]:
        if not self.config.configured:
            return {
                "state": "unavailable",
                "configured": False,
                "message": "Cloud sync is not configured in this Lians build.",
            }
        try:
            session = self.vault.load()
        except CloudAuthError:
            return {
                "state": "needs_attention",
                "configured": True,
                "message": "Sign in again to repair cloud sync.",
            }
        if session is None:
            return {
                "state": "signed_out",
                "configured": True,
                "message": "Sign in to sync encrypted memory across devices.",
            }
        return {
            "state": "connected" if session.expires_at > int(self._clock()) else "refresh_required",
            "configured": True,
            "message": "Encrypted memory sync is connected.",
        }

    def sign_out(self, *, confirmed: bool = False) -> dict[str, Any]:
        if not confirmed:
            raise ValueError("Signing out requires confirmed=true")
        with self._lock:
            self.vault.clear()
        return {
            "state": "signed_out",
            "configured": self.config.configured,
            "local_memory_preserved": True,
            "message": "Signed out. Encrypted memory remains on this device.",
        }
