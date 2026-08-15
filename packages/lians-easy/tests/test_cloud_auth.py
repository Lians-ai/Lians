from __future__ import annotations

import base64
import hashlib
import json
import threading
import urllib.parse
import urllib.request
from http.server import ThreadingHTTPServer

import pytest
from lians_easy.cloud_auth import (
    CloudAuthConfig,
    CloudAuthError,
    CloudSession,
    CloudTokenVault,
    NativeCloudAuth,
)
from lians_easy.crypto import LocalCipher


class Response:
    def __init__(self, document):
        self.body = json.dumps(document).encode()

    def read(self, amount=-1):
        return self.body if amount < 0 else self.body[:amount]

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return None


def _config() -> CloudAuthConfig:
    return CloudAuthConfig(
        issuer="https://login.example",
        client_id="lians-native-client",
        audience="https://api.lians.ai",
        cloud_url="https://api.lians.ai",
        timeout_seconds=30,
    )


def _vault(tmp_path) -> CloudTokenVault:
    return CloudTokenVault(
        tmp_path / "cloud-session.json",
        LocalCipher(tmp_path / "bridge.key", key=b"a" * 32),
    )


def test_cloud_auth_config_is_local_only_until_a_native_client_is_configured():
    config = CloudAuthConfig(
        issuer="",
        client_id="",
        audience="https://api.lians.ai",
        cloud_url="https://api.lians.ai",
    )
    assert config.configured is False
    with pytest.raises(ValueError):
        CloudAuthConfig(
            issuer="https://login.example",
            client_id="",
            audience="https://api.lians.ai",
            cloud_url="https://api.lians.ai",
        )
    with pytest.raises(ValueError):
        CloudAuthConfig(
            issuer="https://login.example",
            client_id="native",
            audience="http://api.lians.ai",
            cloud_url="https://api.lians.ai",
        )


def test_token_vault_encrypts_rotating_tokens_and_detects_tampering(tmp_path):
    vault = _vault(tmp_path)
    session = CloudSession(
        access_token="private-access-token",
        refresh_token="private-refresh-token",
        expires_at=2_000_000_000,
        scope=("openid", "offline_access", "memory:sync"),
    )
    vault.save(session)
    encoded = vault.path.read_bytes()
    assert b"private-access-token" not in encoded
    assert b"private-refresh-token" not in encoded
    assert vault.load() == session

    document = json.loads(encoded)
    document["ciphertext"] = document["ciphertext"][:-2] + "AA"
    vault.path.write_text(json.dumps(document), encoding="utf-8")
    with pytest.raises(CloudAuthError, match="sign in again"):
        vault.load()
    assert vault.clear() is True
    assert vault.load() is None


def test_native_sign_in_uses_system_browser_pkce_and_exposes_status_only(tmp_path):
    vault = _vault(tmp_path)
    requests = []
    authorization = {}

    def opener(request, *, timeout):
        requests.append((request, timeout))
        if request.full_url.endswith("/.well-known/openid-configuration"):
            return Response(
                {
                    "issuer": "https://login.example/",
                    "authorization_endpoint": "https://login.example/authorize",
                    "token_endpoint": "https://login.example/oauth/token",
                }
            )
        values = urllib.parse.parse_qs(request.data.decode("ascii"))
        assert values["grant_type"] == ["authorization_code"]
        assert values["client_id"] == ["lians-native-client"]
        assert "client_secret" not in values
        verifier = values["code_verifier"][0]
        assert verifier
        challenge = (
            base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest())
            .rstrip(b"=")
            .decode()
        )
        assert challenge == authorization["code_challenge"]
        return Response(
            {
                "token_type": "Bearer",
                "access_token": "signed-in-access-token",
                "refresh_token": "rotating-refresh-token",
                "expires_in": 3600,
                "scope": "openid offline_access memory:sync",
            }
        )

    def browser_opener(url):
        parsed = urllib.parse.urlsplit(url)
        assert parsed.scheme == "https"
        assert parsed.netloc == "login.example"
        authorization.update(
            {key: value[0] for key, value in urllib.parse.parse_qs(parsed.query).items()}
        )
        assert authorization["code_challenge_method"] == "S256"
        assert authorization["audience"] == "https://api.lians.ai"
        assert "memory:sync" in authorization["scope"]
        callback = (
            authorization["redirect_uri"]
            + "?"
            + urllib.parse.urlencode(
                {"code": "one-time-code", "state": authorization["state"]}
            )
        )
        with urllib.request.urlopen(callback) as response:
            assert response.status == 200
        return True

    auth = NativeCloudAuth(
        _config(),
        vault,
        opener=opener,
        browser_opener=browser_opener,
        clock=lambda: 1_000,
    )
    status = auth.sign_in()
    assert status == {
        "state": "connected",
        "configured": True,
        "message": "Encrypted memory sync is connected.",
    }
    assert "token" not in json.dumps(status).lower()
    assert vault.load().access_token == "signed-in-access-token"
    assert len(requests) == 2


def test_access_token_refresh_rotates_without_returning_refresh_token(tmp_path):
    vault = _vault(tmp_path)
    vault.save(
        CloudSession(
            access_token="expired-access-token",
            refresh_token="old-refresh-token",
            expires_at=900,
            scope=("offline_access", "memory:sync"),
        )
    )
    exchanges = []

    def opener(request, *, timeout):
        if request.full_url.endswith("/.well-known/openid-configuration"):
            return Response(
                {
                    "issuer": "https://login.example",
                    "authorization_endpoint": "https://login.example/authorize",
                    "token_endpoint": "https://login.example/oauth/token",
                }
            )
        values = urllib.parse.parse_qs(request.data.decode("ascii"))
        exchanges.append(values)
        return Response(
            {
                "token_type": "bearer",
                "access_token": "fresh-access-token",
                "refresh_token": "fresh-refresh-token",
                "expires_in": 1800,
                "scope": "offline_access memory:sync",
            }
        )

    auth = NativeCloudAuth(_config(), vault, opener=opener, clock=lambda: 1_000)
    assert auth.access_token() == "fresh-access-token"
    assert exchanges == [
        {
            "grant_type": ["refresh_token"],
            "client_id": ["lians-native-client"],
            "refresh_token": ["old-refresh-token"],
        }
    ]
    assert vault.load().refresh_token == "fresh-refresh-token"
    result = auth.sign_out(confirmed=True)
    assert result["local_memory_preserved"] is True
    assert "token" not in json.dumps(result).lower()
    assert vault.load() is None


def test_loopback_callback_ignores_wrong_state_and_accepts_only_once():
    completed = threading.Event()
    result = {}
    handler = NativeCloudAuth._callback_handler(
        expected_state="expected-state",
        completed=completed,
        result=result,
    )
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    origin = f"http://127.0.0.1:{server.server_port}/oauth/callback"
    try:
        with pytest.raises(urllib.error.HTTPError) as wrong:
            urllib.request.urlopen(f"{origin}?code=attacker&state=wrong")
        assert wrong.value.code == 400
        assert completed.is_set() is False
        with urllib.request.urlopen(f"{origin}?code=valid&state=expected-state") as response:
            assert response.status == 200
        assert completed.is_set() is True
        assert result == {"code": "valid"}
        with pytest.raises(urllib.error.HTTPError) as replay:
            urllib.request.urlopen(f"{origin}?code=replay&state=expected-state")
        assert replay.value.code == 409
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
