"""Paid-call-free contracts for the installed Lians Codex Sol harness."""

from __future__ import annotations

import hashlib
import json
import sys
from collections import deque
from collections.abc import Mapping
from pathlib import Path
from typing import Any

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from benchmarks.codex_sol_installed_plugin_ab import (
    DEFAULT_SUITE_CREDIT_CAP,
    MAX_CONTEXT_TOKENS,
    MODEL,
    PLUGIN_ID,
    REASONING_EFFORT,
    SERVICE_TIER,
    BenchmarkConfig,
    BenchmarkError,
    InstalledPlugin,
    PreparedProjects,
    _app_server_command,
    _plugin_from_document,
    _protected_subject_reference,
    run_benchmark,
)


def _fixture(tmp_path: Path) -> tuple[BenchmarkConfig, InstalledPlugin]:
    codex = tmp_path / "codex.exe"
    codex.write_bytes(b"codex")
    dataset = tmp_path / "locomo10.json"
    dataset.write_text(
        json.dumps(
            [
                {
                    "conversation": {
                        "session_1_date_time": "1:56 pm on 8 May, 2023",
                        "session_1": [
                            {
                                "speaker": "Caroline",
                                "dia_id": "D1:3",
                                "text": "I went to a LGBTQ support group yesterday and it was powerful.",
                            }
                        ],
                    }
                }
            ]
        ),
        encoding="utf-8",
    )
    plugin_root = tmp_path / "installed-plugin"
    plugin_root.mkdir()
    plugin = InstalledPlugin(
        plugin_id=PLUGIN_ID,
        version="0.1.0+test",
        root=plugin_root,
        manifest_sha256="1" * 64,
        bootstrap_sha256="2" * 64,
        hook_sha256="3" * 64,
    )
    return (
        BenchmarkConfig(
            codex_exe=codex,
            dataset_file=dataset,
            order="ba",
            raw_dir=tmp_path / "raw",
        ),
        plugin,
    )


def _prepared(
    _: InstalledPlugin,
    baseline: Path,
    candidate: Path,
    records,
) -> PreparedProjects:
    state = baseline.parent / "plugin-state"
    state.mkdir(exist_ok=True)
    return PreparedProjects(
        baseline_root=baseline,
        candidate_root=candidate,
        baseline_receipt=state / "baseline-receipts.jsonl",
        candidate_receipt=state / "candidate-receipts.jsonl",
        baseline_db=state / "baseline.sqlite3",
        candidate_db=state / "candidate.sqlite3",
        seed_report={
            "record_count": len(records),
            "evidence_ids": [item["metadata"]["dia_id"] for item in records],
            "encrypted_rows": len(records),
            "plaintext_absent": True,
            "embedding_provider": "bge-onnx",
        },
        data_home=state,
    )


def _hook(plugin: InstalledPlugin, event: str) -> dict[str, Any]:
    if event == "sessionStart":
        suffix = "session_start:0:0"
        matcher: str | None = "^(startup|resume|clear)$"
        context_limit = None
        digest = "a" * 64
    else:
        suffix = "user_prompt_submit:0:0"
        matcher = None
        context_limit = MAX_CONTEXT_TOKENS
        digest = "b" * 64
    return {
        "key": f"{PLUGIN_ID}:hooks/hooks.json:{suffix}",
        "eventName": event,
        "handlerType": "command",
        "matcher": matcher,
        "command": "installed Lians hook command",
        "timeoutSec": 120,
        "statusMessage": "Lians memory",
        "additionalContextLimit": context_limit,
        "sourcePath": str(plugin.root / "hooks" / "hooks.json"),
        "source": "plugin",
        "pluginId": PLUGIN_ID,
        "displayOrder": 0,
        "enabled": True,
        "isManaged": False,
        "currentHash": f"sha256:{digest}",
        "trustStatus": "trusted",
    }


def _receipt(prompt: str, *, candidate: bool) -> dict[str, Any]:
    common = {
        "prompt_sha256": hashlib.sha256(prompt.encode()).hexdigest(),
        "backend": "local",
        "retrieval_degraded": False,
        "candidate_window_complete": True,
        "graph_search_complete": True,
    }
    if candidate:
        return {
            **common,
            "status": "injected",
            "injected": True,
            "memory_count": 1,
            "token_estimate": 80,
            "top_score": 0.91,
            "query_source": "explicit_tag",
        }
    return {
        **common,
        "status": "no_match",
        "injected": False,
        "memory_count": 0,
        "token_estimate": 0,
        "top_score": None,
        "query_source": "bounded_prompt",
    }


class _FakeAppServer:
    def __init__(
        self,
        plugin: InstalledPlugin,
        *,
        hook_warning: bool = False,
        candidate_tool: bool = False,
        baseline_delegation: bool = False,
        baseline_reroute: bool = False,
        missing_usage: bool = False,
        omit_optional_cache_write: bool = False,
        high_candidate_usage: bool = False,
        session_dispatch: bool = True,
    ) -> None:
        self.plugin = plugin
        self.hook_warning = hook_warning
        self.candidate_tool = candidate_tool
        self.baseline_delegation = baseline_delegation
        self.baseline_reroute = baseline_reroute
        self.missing_usage = missing_usage
        self.omit_optional_cache_write = omit_optional_cache_write
        self.high_candidate_usage = high_candidate_usage
        self.session_dispatch = session_dispatch
        self.calls: list[tuple[str, str, dict[str, Any]]] = []
        self.notifications: deque[dict[str, Any]] = deque()
        self.threads: dict[str, dict[str, Any]] = {}
        self.turn_count = 0
        self.closed = False

    def request(
        self,
        method: str,
        params: Mapping[str, Any],
        *,
        timeout_seconds: float,
        events: list[dict[str, Any]],
    ) -> Mapping[str, Any]:
        assert timeout_seconds > 0
        assert isinstance(events, list)
        copied = dict(params)
        self.calls.append(("request", method, copied))
        if method == "initialize":
            return {"userAgent": "Codex Test/1.0", "platformFamily": "windows"}
        if method == "hooks/list":
            return {
                "data": [
                    {
                        "cwd": cwd,
                        "hooks": [
                            _hook(self.plugin, "sessionStart"),
                            _hook(self.plugin, "userPromptSubmit"),
                        ],
                        "warnings": ["test warning"] if self.hook_warning else [],
                        "errors": [],
                    }
                    for cwd in copied["cwds"]
                ]
            }
        if method == "thread/start":
            thread_id = f"thread-{len(self.threads) + 1}"
            self.threads[thread_id] = copied
            if self.session_dispatch:
                run = {
                    "id": f"session-hook-{thread_id}",
                    "eventName": "SessionStart",
                    "handlerType": "command",
                    "executionMode": "sync",
                    "scope": "thread",
                    "source": "plugin",
                    "sourcePath": str(self.plugin.root / "hooks" / "hooks.json"),
                    "displayOrder": 0,
                    "startedAt": 1,
                    "completedAt": 2,
                    "durationMs": 1,
                    "entries": [],
                }
                events.extend(
                    [
                        {
                            "method": "hook/started",
                            "params": {
                                "threadId": thread_id,
                                "turnId": None,
                                "run": {**run, "status": "running"},
                            },
                        },
                        {
                            "method": "hook/completed",
                            "params": {
                                "threadId": thread_id,
                                "turnId": None,
                                "run": {**run, "status": "completed"},
                            },
                        },
                    ]
                )
            return {
                "model": copied["model"],
                "modelProvider": copied["modelProvider"],
                "reasoningEffort": copied["config"]["model_reasoning_effort"],
                "serviceTier": copied["serviceTier"],
                "cwd": copied["cwd"],
                "approvalPolicy": copied["approvalPolicy"],
                "sandbox": {"type": "readOnly", "networkAccess": False},
                "thread": {
                    "id": thread_id,
                    "ephemeral": copied["ephemeral"],
                    "modelProvider": copied["modelProvider"],
                },
            }
        if method == "turn/start":
            self.turn_count += 1
            thread_id = str(copied["threadId"])
            turn_id = f"turn-{self.turn_count}"
            thread = self.threads[thread_id]
            cwd = Path(str(thread["cwd"]))
            candidate = cwd.name == "candidate"
            prompt = str(copied["input"][0]["text"])
            state = cwd.parent / "plugin-state"
            receipt = state / (
                "candidate-receipts.jsonl" if candidate else "baseline-receipts.jsonl"
            )
            with receipt.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(_receipt(prompt, candidate=candidate)) + "\n")
            self._queue_turn(thread_id, turn_id, candidate=candidate)
            return {"turn": {"id": turn_id, "status": "inProgress", "items": [], "error": None}}
        raise AssertionError(f"unexpected request: {method}")

    def _queue_turn(self, thread_id: str, turn_id: str, *, candidate: bool) -> None:
        identity = {"threadId": thread_id, "turnId": turn_id}
        if candidate and self.candidate_tool:
            self.notifications.append(
                {
                    "method": "item/completed",
                    "params": {
                        **identity,
                        "item": {
                            "id": "tool-1",
                            "type": "mcpToolCall",
                            "server": "lians_memory",
                            "tool": "recall",
                            "status": "completed",
                        },
                    },
                }
            )
        if not candidate and self.baseline_delegation:
            self.notifications.append(
                {
                    "method": "item/completed",
                    "params": {
                        **identity,
                        "item": {
                            "id": "delegate-1",
                            "type": "collabAgentToolCall",
                            "tool": "spawn_agent",
                            "status": "completed",
                        },
                    },
                }
            )
        self.notifications.append(
            {
                "method": "item/completed",
                "params": {
                    **identity,
                    "item": {
                        "id": "answer",
                        "type": "agentMessage",
                        "phase": "final_answer",
                        "text": "7 May 2023",
                    },
                },
            }
        )
        if not candidate and self.baseline_reroute:
            self.notifications.append(
                {
                    "method": "model/rerouted",
                    "params": {
                        **identity,
                        "fromModel": MODEL,
                        "toModel": "gpt-5.6-terra",
                        "reason": "test",
                    },
                }
            )
        total_input = (
            90_000 if candidate and self.high_candidate_usage else (10_000 if candidate else 30_000)
        )
        first_input = total_input * 2 // 5
        for index, (input_tokens, output_tokens) in enumerate(
            ((first_input, 4), (total_input - first_input, 6)),
            start=1,
        ):
            usage = {
                "inputTokens": input_tokens,
                "cachedInputTokens": 0,
                "cacheWriteInputTokens": 0,
                "outputTokens": output_tokens,
                "reasoningOutputTokens": 0,
                "totalTokens": input_tokens + output_tokens,
            }
            if candidate and self.missing_usage and index == 1:
                usage.pop("reasoningOutputTokens")
            if self.omit_optional_cache_write:
                usage.pop("cacheWriteInputTokens")
            self.notifications.append(
                {
                    "method": "rawResponse/completed",
                    "params": {
                        **identity,
                        "responseId": f"response-{turn_id}-{index}",
                        "usage": usage,
                    },
                }
            )
        self.notifications.append(
            {
                "method": "turn/completed",
                "params": {
                    "threadId": thread_id,
                    "turn": {"id": turn_id, "status": "completed", "items": [], "error": None},
                },
            }
        )

    def notify(self, method: str, params: Mapping[str, Any] | None = None) -> None:
        self.calls.append(("notify", method, dict(params or {})))

    def read(self, *, timeout_seconds: float) -> dict[str, Any]:
        assert timeout_seconds > 0
        if not self.notifications:
            raise AssertionError("test app-server has no queued notification")
        return self.notifications.popleft()

    def stderr_bytes(self) -> bytes:
        return b""

    def close(self) -> None:
        self.closed = True


def _run_with_server(
    config: BenchmarkConfig,
    plugin: InstalledPlugin,
    server: _FakeAppServer,
) -> dict[str, Any]:
    return run_benchmark(
        config,
        dry_run=False,
        app_server_factory=lambda _: server,
        plugin_discoverer=lambda _: plugin,
        project_preparer=_prepared,
    )


def test_dry_run_is_strict_two_call_ba_and_never_prepares_or_starts_server(tmp_path: Path) -> None:
    config, plugin = _fixture(tmp_path)

    def forbidden_prepare(*_):  # pragma: no cover - must not run
        raise AssertionError("dry run prepared BGE or project databases")

    def forbidden_server(_: BenchmarkConfig):  # pragma: no cover - must not run
        raise AssertionError("dry run launched app-server")

    report = run_benchmark(
        config,
        dry_run=True,
        app_server_factory=forbidden_server,
        plugin_discoverer=lambda _: plugin,
        project_preparer=forbidden_prepare,
    )

    assert report["profile"]["execution_order"] == ["candidate:1", "baseline:1"]
    assert report["profile"]["paid_call_count"] == 2
    assert report["profile"]["transport"] == "persistent codex app-server stdio JSON-RPC"
    assert report["planned_preflight"]["before_paid_turns"] is True
    assert report["profile"]["normal_user_config_loaded"] is True
    assert report["profile"]["normal_enabled_plugins_loaded"] is True
    assert report["profile"]["hook_trust_bypass"] is False
    assert report["seed_plan"]["copies_gold_answer"] is False
    assert report["verdict"]["status"] == "dry_run_only"
    rendered = json.dumps(report)
    assert str(tmp_path) not in rendered
    assert "memory-benchmarks" not in rendered


def test_preflight_precedes_model_and_requests_pin_the_sol_profile(tmp_path: Path) -> None:
    config, plugin = _fixture(tmp_path)
    server = _FakeAppServer(plugin)

    report = _run_with_server(config, plugin, server)

    methods = [(kind, method) for kind, method, _ in server.calls]
    assert methods[:3] == [
        ("request", "initialize"),
        ("notify", "initialized"),
        ("request", "hooks/list"),
    ]
    assert methods[3:] == [
        ("request", "thread/start"),
        ("request", "thread/start"),
        ("request", "thread/start"),
        ("request", "turn/start"),
        ("request", "thread/start"),
        ("request", "turn/start"),
    ]
    initialize = server.calls[0][2]
    assert initialize["capabilities"]["experimentalApi"] is True
    assert initialize["capabilities"]["requestAttestation"] is False
    hooks = server.calls[2][2]
    assert len(hooks["cwds"]) == 2
    assert {Path(cwd).name for cwd in hooks["cwds"]} == {"baseline", "candidate"}

    thread_requests = [params for kind, method, params in server.calls if method == "thread/start"]
    turn_requests = [params for kind, method, params in server.calls if method == "turn/start"]
    assert len(thread_requests) == 4
    assert len(turn_requests) == 2
    for thread, turn in zip(thread_requests[-2:], turn_requests, strict=True):
        assert thread["model"] == MODEL
        assert thread["modelProvider"] == "openai"
        assert thread["approvalPolicy"] == "never"
        assert thread["sandbox"] == "read-only"
        assert thread["ephemeral"] is True
        assert thread["experimentalRawEvents"] is True
        assert thread["sessionStartSource"] == "startup"
        assert thread["serviceTier"] == SERVICE_TIER
        assert thread["config"]["model_reasoning_effort"] == REASONING_EFFORT
        assert turn["model"] == MODEL
        assert turn["effort"] == REASONING_EFFORT
        assert turn["clientUserMessageId"].startswith("lians-ab-")
        assert turn["serviceTier"] == SERVICE_TIER
        assert turn["cwd"] == thread["cwd"]
        assert turn["approvalPolicy"] == "never"
        assert turn["sandboxPolicy"] == {"type": "readOnly", "networkAccess": False}
        assert turn["input"] and turn["input"][0]["type"] == "text"
    assert _app_server_command(config) == (str(config.codex_exe), "app-server", "--strict-config")
    assert report["hook_preflight"]["passed"] is True
    assert len(report["hook_preflight"]["session_start_dispatch"]) == 2
    assert report["hook_preflight"]["model_calls_during_preflight"] == 0
    assert report["verdict"]["exactly_two_paid_turns"] is True
    assert server.closed is True


def test_raw_provider_usage_is_aggregated_and_qualified_artifacts_are_sanitized(
    tmp_path: Path,
) -> None:
    config, plugin = _fixture(tmp_path)
    server = _FakeAppServer(plugin)

    report = _run_with_server(config, plugin, server)

    candidate, baseline = report["runs"]
    assert candidate["usage"]["input_tokens"] == 10_000
    assert baseline["usage"]["input_tokens"] == 30_000
    assert candidate["provider_response_count"] == baseline["provider_response_count"] == 2
    assert candidate["usage_source"].startswith("matching rawResponse/completed")
    assert report["verdict"]["qualified_target_met"] is True
    assert report["observed"]["same_budget_usage_extension_percent"] > 80
    assert report["observed"]["suite_estimated_sol_credits"] < DEFAULT_SUITE_CREDIT_CAP
    assert all(
        run["raw_stdout_artifact"] == f"{run['label']}.stdout.jsonl" for run in report["runs"]
    )
    rendered = json.dumps(report)
    assert str(tmp_path) not in rendered
    assert "C:\\Users\\" not in rendered
    for artifact in (config.raw_dir or tmp_path).glob("*.jsonl"):
        raw = artifact.read_text(encoding="utf-8")
        assert str(tmp_path) not in raw
        assert "C:\\Users\\" not in raw


def test_hook_warning_fails_closed_before_thread_or_paid_turn(tmp_path: Path) -> None:
    config, plugin = _fixture(tmp_path)
    server = _FakeAppServer(plugin, hook_warning=True)

    with pytest.raises(BenchmarkError, match="warnings or errors"):
        _run_with_server(config, plugin, server)

    methods = [method for _, method, _ in server.calls]
    assert methods == ["initialize", "initialized", "hooks/list"]
    assert server.turn_count == 0
    assert server.closed is True


def test_missing_session_dispatch_fails_before_paid_turn(tmp_path: Path) -> None:
    config, plugin = _fixture(tmp_path)
    server = _FakeAppServer(plugin, session_dispatch=False)

    with pytest.raises(BenchmarkError, match="no paid turn will run"):
        _run_with_server(config, plugin, server)

    assert server.turn_count == 0
    assert server.closed is True


def test_preflight_only_proves_dispatch_without_model_turn(tmp_path: Path) -> None:
    config, plugin = _fixture(tmp_path)
    server = _FakeAppServer(plugin)

    report = run_benchmark(
        config,
        dry_run=False,
        preflight_only=True,
        app_server_factory=lambda _: server,
        plugin_discoverer=lambda _: plugin,
        project_preparer=_prepared,
    )

    assert report["verdict"]["status"] == "preflight_only"
    assert report["verdict"]["qualified_target_met"] is None
    assert report["hook_preflight"]["model_calls_during_preflight"] == 0
    assert report["runs"] == []
    assert server.turn_count == 0
    assert server.closed is True


def test_tool_delegation_and_model_reroute_reject_the_contract(tmp_path: Path) -> None:
    config, plugin = _fixture(tmp_path)
    server = _FakeAppServer(
        plugin,
        candidate_tool=True,
        baseline_delegation=True,
        baseline_reroute=True,
    )

    report = _run_with_server(config, plugin, server)

    assert report["verdict"]["qualified_target_met"] is False
    assert report["verdict"]["all_runs_no_tools_or_delegation"] is False
    assert report["verdict"]["all_runs_no_model_or_provider_reroute"] is False
    assert "model used a tool" in report["runs"][0]["violations"]
    assert "model delegated" in report["runs"][1]["violations"]
    assert "model or provider rerouted" in report["runs"][1]["violations"]


def test_missing_raw_provider_usage_component_fails_closed(tmp_path: Path) -> None:
    config, plugin = _fixture(tmp_path)
    server = _FakeAppServer(plugin, missing_usage=True)

    with pytest.raises(BenchmarkError, match="complete raw provider token usage"):
        _run_with_server(config, plugin, server)

    assert server.turn_count == 1
    assert server.closed is True


def test_optional_zero_cache_write_usage_may_be_omitted(tmp_path: Path) -> None:
    config, plugin = _fixture(tmp_path)
    server = _FakeAppServer(plugin, omit_optional_cache_write=True)

    report = _run_with_server(config, plugin, server)

    assert all(run["usage"]["cache_write_input_tokens"] == 0 for run in report["runs"])


def test_credit_cap_aborts_before_second_paid_turn(tmp_path: Path) -> None:
    config, plugin = _fixture(tmp_path)
    server = _FakeAppServer(plugin, high_candidate_usage=True)

    with pytest.raises(BenchmarkError, match="no further calls"):
        _run_with_server(config, plugin, server)

    assert server.turn_count == 1
    assert server.closed is True


def test_seed_storage_requires_a_v2_protected_subject_reference() -> None:
    protected = "lians:subject:v2:hmac-sha256:" + "a" * 64 + ":" + "b" * 64

    assert _protected_subject_reference(protected, raw_subject_id="customer-7") == protected
    with pytest.raises(BenchmarkError, match="protected stable reference"):
        _protected_subject_reference("customer-7", raw_subject_id="customer-7")


def test_plugin_inventory_requires_enabled_manifest_matching_install(tmp_path: Path) -> None:
    root = tmp_path / "lians-memory"
    (root / ".codex-plugin").mkdir(parents=True)
    (root / "scripts").mkdir()
    (root / "runtime").mkdir()
    (root / ".codex-plugin" / "plugin.json").write_text(
        json.dumps({"name": "lians-memory", "version": "0.1.0+test"}),
        encoding="utf-8",
    )
    (root / "scripts" / "bootstrap.py").write_text("# bootstrap\n", encoding="utf-8")
    (root / "runtime" / "user_prompt_submit_recall.py").write_text("# hook\n", encoding="utf-8")
    document = {
        "installed": [
            {
                "pluginId": PLUGIN_ID,
                "version": "0.1.0+test",
                "installed": True,
                "enabled": True,
                "source": {"path": str(root)},
            }
        ]
    }

    plugin = _plugin_from_document(document)

    assert plugin.root == root.resolve()
    assert plugin.version == "0.1.0+test"
    assert len(plugin.manifest_sha256) == 64


def test_plugin_inventory_prefers_the_versioned_installed_cache(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = tmp_path / "source-plugin"
    cache = tmp_path / "codex-home" / "plugins" / "cache" / "lians" / "lians-memory" / "0.1.0+test"
    for root, marker in ((source, "source"), (cache, "cache")):
        (root / ".codex-plugin").mkdir(parents=True)
        (root / "scripts").mkdir()
        (root / "runtime").mkdir()
        (root / ".codex-plugin" / "plugin.json").write_text(
            json.dumps({"name": "lians-memory", "version": "0.1.0+test"}),
            encoding="utf-8",
        )
        (root / "scripts" / "bootstrap.py").write_text(f"# {marker}\n", encoding="utf-8")
        (root / "runtime" / "user_prompt_submit_recall.py").write_text(
            f"# {marker}\n",
            encoding="utf-8",
        )
    monkeypatch.setenv("CODEX_HOME", str(tmp_path / "codex-home"))
    document = {
        "installed": [
            {
                "pluginId": PLUGIN_ID,
                "name": "lians-memory",
                "marketplaceName": "lians",
                "version": "0.1.0+test",
                "installed": True,
                "enabled": True,
                "source": {"path": str(source)},
            }
        ]
    }

    plugin = _plugin_from_document(document)

    assert plugin.root == cache.resolve()
    assert (
        plugin.bootstrap_sha256
        == hashlib.sha256((cache / "scripts" / "bootstrap.py").read_bytes()).hexdigest()
    )
