from __future__ import annotations

import importlib.util
import json
import os
import sys
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest


INTEGRATION_ROOT = Path(__file__).resolve().parents[1]
DAEMON_PATH = INTEGRATION_ROOT / "local_recall_daemon.py"
SPEC = importlib.util.spec_from_file_location("lians_codex_daemon_test", DAEMON_PATH)
assert SPEC and SPEC.loader
daemon = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = daemon
SPEC.loader.exec_module(daemon)


def _settings(tmp_path: Path, **changes: Any) -> Any:
    values = {
        "local_db": str(tmp_path / "memory.db"),
        "namespace": "mcp-project-a",
        "agent_id": "mcp-project-a",
        "k": 20,
        "local_runtime_env": (
            ("EMBEDDING_PROVIDER", "sentence-transformers"),
            ("SENTENCE_TRANSFORMER_MODEL", "example/1024-model"),
        ),
        "daemon_runtime_dir": str(tmp_path / "runtime"),
        "daemon_idle_seconds": 60,
        "daemon_request_timeout_ms": 1_000,
        "daemon_start_timeout_ms": 5_000,
    }
    values.update(changes)
    return SimpleNamespace(**values)


class _FakeRuntime:
    instances: list["_FakeRuntime"] = []

    def __init__(self, _settings: Any) -> None:
        self.queries: list[str] = []
        self.prewarmed = False
        self.closed = False
        self.instances.append(self)

    def prewarm(self) -> None:
        self.prewarmed = True

    def recall(self, query: str) -> dict[str, Any]:
        self.queries.append(query)
        return {
            "memories": [{"content": "Ship Friday", "score": 0.9}],
            "candidate_window_complete": True,
            "graph_search_complete": True,
        }

    def close(self) -> None:
        self.closed = True


def _wait_ready(settings: Any) -> None:
    deadline = time.monotonic() + 3
    while time.monotonic() < deadline:
        try:
            daemon.health(settings)
        except RuntimeError:
            time.sleep(0.01)
        else:
            return
    raise AssertionError("test daemon did not become ready")


def test_identity_isolates_database_namespace_agent_and_embedding_model(tmp_path: Path) -> None:
    base = daemon.daemon_paths(_settings(tmp_path))
    variants = [
        _settings(tmp_path, local_db=str(tmp_path / "other.db")),
        _settings(tmp_path, namespace="other"),
        _settings(tmp_path, agent_id="other"),
        _settings(
            tmp_path,
            local_runtime_env=(
                ("EMBEDDING_PROVIDER", "sentence-transformers"),
                ("SENTENCE_TRANSFORMER_MODEL", "other/1024-model"),
            ),
        ),
        _settings(
            tmp_path,
            local_runtime_env=(
                ("EMBEDDING_PROVIDER", "sentence-transformers"),
                ("SENTENCE_TRANSFORMER_MODEL", "example/1024-model"),
                ("RECALL_RERANKER_PREFETCH", "100"),
            ),
        ),
        _settings(
            tmp_path,
            local_runtime_env=(
                ("EMBEDDING_PROVIDER", "bge-onnx"),
                ("BGE_ONNX_ARTIFACT_DIR", str(tmp_path / "bge-onnx")),
                ("BGE_ONNX_INTRA_OP_THREADS", "8"),
            ),
        ),
    ]

    assert (
        len({base.fingerprint, *(daemon.daemon_paths(item).fingerprint for item in variants)}) == 7
    )
    assert base.state.parent == (tmp_path / "runtime").resolve()


def test_loopback_client_ignores_proxy_environment(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    token = tmp_path / "user.token"
    token.write_text("a" * 64, encoding="ascii")
    paths = SimpleNamespace(
        token=token,
        fingerprint="f" * 64,
    )
    captured: dict[str, Any] = {}

    class _Response:
        def __enter__(self) -> "_Response":
            return self

        def __exit__(self, *_args: object) -> None:
            return None

        def read(self, _limit: int) -> bytes:
            return b'{"ok":true}'

    class _DirectOpener:
        def open(self, request: urllib.request.Request, *, timeout: float) -> _Response:
            captured["url"] = request.full_url
            captured["token"] = request.get_header("X-lians-token")
            captured["timeout"] = timeout
            return _Response()

    monkeypatch.setenv("HTTP_PROXY", "http://proxy-attacker.invalid:8080")
    monkeypatch.setenv("HTTPS_PROXY", "http://proxy-attacker.invalid:8080")
    monkeypatch.setattr(daemon, "daemon_paths", lambda _settings: paths)
    monkeypatch.setattr(
        daemon,
        "_read_state",
        lambda _paths: {"port": 43123, "fingerprint": paths.fingerprint},
    )
    monkeypatch.setattr(daemon, "_DIRECT_LOOPBACK_OPENER", _DirectOpener())
    monkeypatch.setattr(
        urllib.request,
        "urlopen",
        lambda *_args, **_kwargs: pytest.fail("global proxy-aware urlopen must not be used"),
    )

    assert daemon._post(_settings(tmp_path), "/health", {"probe": True}) == {"ok": True}
    assert captured == {
        "url": "http://127.0.0.1:43123/health",
        "token": "a" * 64,
        "timeout": 1.0,
    }


def test_authenticated_loopback_recall_health_and_clean_stop(tmp_path: Path) -> None:
    _FakeRuntime.instances.clear()
    settings = _settings(tmp_path)
    worker = threading.Thread(
        target=daemon.serve,
        args=(settings,),
        kwargs={"runtime_factory": _FakeRuntime},
        daemon=True,
    )
    worker.start()
    _wait_ready(settings)

    result = daemon.recall(settings, "When is launch?")
    paths = daemon.daemon_paths(settings)
    state = json.loads(paths.state.read_text(encoding="utf-8"))
    unauthorized = urllib.request.Request(
        f"http://127.0.0.1:{state['port']}/health",
        data=json.dumps({"fingerprint": paths.fingerprint}).encode(),
        method="POST",
        headers={"Content-Type": "application/json", "X-Lians-Token": "wrong"},
    )

    try:
        urllib.request.urlopen(unauthorized, timeout=1)  # noqa: S310
    except urllib.error.HTTPError as rejected:
        assert rejected.code == 403
    except ConnectionAbortedError:
        # Windows endpoint security can abort a deliberately unauthorized
        # loopback connection before urllib receives the server's 403. That
        # is still a rejection; the authenticated health check below proves
        # the daemon itself remained available.
        if os.name != "nt":  # pragma: no cover - Windows-specific socket path
            raise
    else:  # pragma: no cover - security boundary must reject
        pytest.fail("unauthorized daemon health request unexpectedly succeeded")

    assert daemon.health(settings)["status"] == "ready"
    assert result["memories"][0]["content"] == "Ship Friday"
    assert result["_lians_hook_transport"] == "daemon"
    assert _FakeRuntime.instances[0].prewarmed is True
    assert _FakeRuntime.instances[0].queries == ["When is launch?"]
    assert paths.token.stat().st_size == 64
    assert state["host"] == "127.0.0.1"
    assert daemon.stop(settings) is True
    worker.join(timeout=3)
    assert worker.is_alive() is False
    assert _FakeRuntime.instances[0].closed is True
    assert paths.state.exists() is False


def test_wrong_project_and_oversized_query_are_rejected(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    worker = threading.Thread(
        target=daemon.serve,
        args=(settings,),
        kwargs={"runtime_factory": _FakeRuntime},
        daemon=True,
    )
    worker.start()
    _wait_ready(settings)

    with pytest.raises(RuntimeError):
        daemon.health(_settings(tmp_path, namespace="other"))
    with pytest.raises(RuntimeError):
        daemon.recall(settings, "x" * (daemon.MAX_QUERY_CHARS + 1))

    assert daemon.stop(settings) is True
    worker.join(timeout=3)
