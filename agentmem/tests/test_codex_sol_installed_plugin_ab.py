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
    SOL_CREDIT_RATES,
    BenchmarkConfig,
    BenchmarkError,
    InstalledPlugin,
    PreparedProjects,
    _app_server_command,
    _bge_artifact_identity,
    _classify_subject_reference,
    _plugin_from_document,
    _public_receipt,
    estimate_sol_credits,
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
        candidate_session_dispatch: bool = True,
        candidate_user_prompt_dispatch: bool = True,
        baseline_session_dispatch: bool = True,
        baseline_user_prompt_dispatch: bool = True,
        reverse_candidate_user_prompt_order: bool = False,
        candidate_user_prompt_failure: bool = False,
        candidate_user_prompt_invalid_source: bool = False,
        unrelated_hook_failure: bool = False,
        candidate_answer: str = "7 May 2023",
        invalid_candidate_receipt: bool = False,
    ) -> None:
        self.plugin = plugin
        self.hook_warning = hook_warning
        self.candidate_tool = candidate_tool
        self.baseline_delegation = baseline_delegation
        self.baseline_reroute = baseline_reroute
        self.missing_usage = missing_usage
        self.omit_optional_cache_write = omit_optional_cache_write
        self.high_candidate_usage = high_candidate_usage
        self.candidate_session_dispatch = candidate_session_dispatch
        self.candidate_user_prompt_dispatch = candidate_user_prompt_dispatch
        self.baseline_session_dispatch = baseline_session_dispatch
        self.baseline_user_prompt_dispatch = baseline_user_prompt_dispatch
        self.reverse_candidate_user_prompt_order = reverse_candidate_user_prompt_order
        self.candidate_user_prompt_failure = candidate_user_prompt_failure
        self.candidate_user_prompt_invalid_source = candidate_user_prompt_invalid_source
        self.unrelated_hook_failure = unrelated_hook_failure
        self.candidate_answer = candidate_answer
        self.invalid_candidate_receipt = invalid_candidate_receipt
        self.calls: list[tuple[str, str, dict[str, Any]]] = []
        self.notifications: deque[dict[str, Any]] = deque()
        self.threads: dict[str, dict[str, Any]] = {}
        self.turn_count = 0
        self.read_methods: list[str] = []
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
            session_dispatch = (
                self.candidate_session_dispatch if candidate else self.baseline_session_dispatch
            )
            if session_dispatch:
                # Real app-server streams SessionStart lifecycle with the live
                # turn events, not as proof from thread/start alone.
                events.extend(self._hook_events("sessionStart", thread_id, turn_id))
            dispatch = (
                self.candidate_user_prompt_dispatch
                if candidate
                else self.baseline_user_prompt_dispatch
            )
            if dispatch:
                hook_events = self._hook_events("userPromptSubmit", thread_id, turn_id)
                if candidate and self.reverse_candidate_user_prompt_order:
                    hook_events.reverse()
                if candidate and (
                    self.candidate_user_prompt_failure
                    or self.candidate_user_prompt_invalid_source
                ):
                    completed_run = hook_events[-1]["params"]["run"]
                    if self.candidate_user_prompt_failure:
                        completed_run["status"] = "failed"
                        completed_run["entries"] = [
                            {
                                "receipt": {
                                    "prompt": "UNTRUSTED_PRIVATE_PROMPT",
                                    "token": "UNTRUSTED_PRIVATE_TOKEN",
                                }
                            }
                        ]
                    if self.candidate_user_prompt_invalid_source:
                        completed_run["sourcePath"] = str(
                            self.plugin.root.parent / "UNTRUSTED_PRIVATE_SOURCE" / "hooks.json"
                        )
                    self.notifications.extend(hook_events)
                else:
                    events.extend(hook_events)
            if candidate and self.unrelated_hook_failure:
                unrelated = self._hook_events("userPromptSubmit", thread_id, turn_id)[-1]
                unrelated_run = unrelated["params"]["run"]
                unrelated_run["id"] = "unrelated-plugin-run"
                unrelated_run["status"] = "failed"
                unrelated_run["sourcePath"] = str(
                    self.plugin.root.parent / "unrelated-plugin" / "hooks.json"
                )
                self.notifications.append(unrelated)
            state = cwd.parent / "plugin-state"
            receipt = state / (
                "candidate-receipts.jsonl" if candidate else "baseline-receipts.jsonl"
            )
            receipt_document = _receipt(prompt, candidate=candidate)
            if candidate and self.invalid_candidate_receipt:
                receipt_document["prompt_sha256"] = "0" * 64
            with receipt.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(receipt_document) + "\n")
            self._queue_turn(thread_id, turn_id, candidate=candidate)
            return {"turn": {"id": turn_id, "status": "inProgress", "items": [], "error": None}}
        if method == "turn/interrupt":
            assert copied == {
                "threadId": f"thread-{self.turn_count}",
                "turnId": f"turn-{self.turn_count}",
            }
            return {}
        raise AssertionError(f"unexpected request: {method}")

    def _hook_events(
        self,
        event_name: str,
        thread_id: str,
        turn_id: str | None,
    ) -> list[dict[str, Any]]:
        scope = "thread" if event_name == "sessionStart" else "turn"
        run = {
            "id": f"{event_name}-hook-{thread_id}-{turn_id or 'session'}",
            "eventName": event_name,
            "handlerType": "command",
            "executionMode": "sync",
            "scope": scope,
            "source": "plugin",
            "sourcePath": str(self.plugin.root / "hooks" / "hooks.json"),
            "displayOrder": 0,
            "startedAt": 1,
            "completedAt": 2,
            "durationMs": 1,
            "entries": [],
        }
        return [
            {
                "method": "hook/started",
                "params": {
                    "threadId": thread_id,
                    "turnId": turn_id,
                    "run": {**run, "status": "running"},
                },
            },
            {
                "method": "hook/completed",
                "params": {
                    "threadId": thread_id,
                    "turnId": turn_id,
                    "run": {**run, "status": "completed"},
                },
            },
        ]

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
                        "text": self.candidate_answer if candidate else "7 May 2023",
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
        notification = self.notifications.popleft()
        self.read_methods.append(str(notification.get("method")))
        return notification

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
    assert report["target"]["credit_threshold_semantics"] == {
        "kind": "post_turn_acceptance_and_continuation_threshold",
        "hard_provider_spend_ceiling": False,
        "completed_turn_may_overshoot": True,
        "candidate_runs_first": True,
        "baseline_runs_only_after_candidate_passes": True,
    }
    assert report["profile"]["transport"] == "persistent codex app-server stdio JSON-RPC"
    assert report["planned_preflight"]["before_paid_turns"] is True
    assert report["planned_preflight"]["sequence"] == ["initialize", "initialized", "hooks/list"]
    assert report["planned_preflight"]["lifecycle_dispatch_checks"] == (
        "deferred to each real live turn"
    )
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
    assert len(thread_requests) == 2
    assert len(turn_requests) == 2
    for thread, turn in zip(thread_requests, turn_requests, strict=True):
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
    assert report["hook_preflight"]["scope"] == "exact_trusted_inventory_only"
    assert report["hook_preflight"]["lifecycle_dispatch_checked"] is False
    assert report["hook_preflight"]["codex_model_calls_during_inventory"] == 0
    for run in report["runs"]:
        assert run["hook_lifecycle_valid"] is True
        assert run["hook_lifecycle"]["event_order"] == [
            "SessionStart:started",
            "SessionStart:completed",
            "UserPromptSubmit:started",
            "UserPromptSubmit:completed",
        ]
        assert run["hook_lifecycle"]["completed_before_first_provider_response"] is True
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


def test_missing_candidate_session_dispatch_aborts_before_baseline(tmp_path: Path) -> None:
    config, plugin = _fixture(tmp_path)
    server = _FakeAppServer(plugin, candidate_session_dispatch=False)

    with pytest.raises(BenchmarkError, match="no further call will run"):
        _run_with_server(config, plugin, server)

    assert server.turn_count == 1
    assert server.closed is True


def test_preflight_only_proves_inventory_without_thread_or_model_turn(tmp_path: Path) -> None:
    config, plugin = _fixture(tmp_path)
    server = _FakeAppServer(plugin)

    def forbidden_prepare(*_):  # pragma: no cover - must not run
        raise AssertionError("inventory-only preflight prepared projects or BGE embeddings")

    report = run_benchmark(
        config,
        dry_run=False,
        preflight_only=True,
        app_server_factory=lambda _: server,
        plugin_discoverer=lambda _: plugin,
        project_preparer=forbidden_prepare,
    )

    assert report["verdict"]["status"] == "preflight_only"
    assert report["verdict"]["qualified_target_met"] is None
    assert report["hook_preflight"]["codex_model_calls_during_inventory"] == 0
    assert report["hook_preflight"]["bge_embedding_inference_calls"] == 0
    assert report["hook_preflight"]["project_preparation_performed"] is False
    assert report["hook_preflight"]["lifecycle_dispatch_checked"] is False
    assert "zero Codex model calls" in report["verdict"]["statement"]
    assert "zero BGE embedding inferences" in report["verdict"]["statement"]
    assert "no dispatch or usage claim" in report["verdict"]["statement"]
    assert report["runs"] == []
    assert server.turn_count == 0
    assert [method for _, method, _ in server.calls] == ["initialize", "initialized", "hooks/list"]
    assert server.closed is True


def test_candidate_contract_failure_aborts_before_baseline(tmp_path: Path) -> None:
    config, plugin = _fixture(tmp_path)
    server = _FakeAppServer(plugin, candidate_tool=True)

    with pytest.raises(BenchmarkError, match="no baseline call will run"):
        _run_with_server(config, plugin, server)

    assert server.turn_count == 1
    assert server.closed is True


def test_baseline_delegation_and_model_reroute_reject_the_contract(tmp_path: Path) -> None:
    config, plugin = _fixture(tmp_path)
    server = _FakeAppServer(
        plugin,
        baseline_delegation=True,
        baseline_reroute=True,
    )

    report = _run_with_server(config, plugin, server)

    assert report["verdict"]["qualified_target_met"] is False
    assert report["verdict"]["all_runs_no_tools_or_delegation"] is False
    assert report["verdict"]["all_runs_no_model_or_provider_reroute"] is False
    assert "model delegated" in report["runs"][1]["violations"]
    assert "model or provider rerouted" in report["runs"][1]["violations"]


def test_candidate_hook_event_order_fails_closed_before_baseline(tmp_path: Path) -> None:
    config, plugin = _fixture(tmp_path)
    server = _FakeAppServer(plugin, reverse_candidate_user_prompt_order=True)

    with pytest.raises(BenchmarkError, match="completed before it started"):
        _run_with_server(config, plugin, server)

    assert server.turn_count == 1
    assert server.closed is True


def test_failed_installed_prompt_hook_interrupts_before_provider_response(tmp_path: Path) -> None:
    config, plugin = _fixture(tmp_path)
    server = _FakeAppServer(plugin, candidate_user_prompt_failure=True)

    with pytest.raises(BenchmarkError, match=r"fail-fast.*hook/completed") as raised:
        _run_with_server(config, plugin, server)

    assert server.read_methods == ["hook/started", "hook/completed"]
    assert [method for _, method, _ in server.calls][-1] == "turn/interrupt"
    assert server.turn_count == 1
    assert server.closed is True
    assert raised.value.evidence == {
        "schema": "lians.installed_hook_fail_fast.v1",
        "notification": "hook/completed",
        "hook_event": "UserPromptSubmit",
        "failure_kind": "non_completed_status",
        "attribution": "matching_exact_source_start",
        "provider_response_observed_before_failure": False,
        "turn_interrupt": {
            "method": "turn/interrupt",
            "attempted": True,
            "request_completed": True,
        },
        "untrusted_details_retained": False,
    }
    sanitized = f"{raised.value}\n{json.dumps(raised.value.evidence)}"
    assert "UNTRUSTED_PRIVATE_PROMPT" not in sanitized
    assert "UNTRUSTED_PRIVATE_TOKEN" not in sanitized
    assert "receipt" not in sanitized


def test_invalid_installed_prompt_hook_source_interrupts_from_started_run_identity(
    tmp_path: Path,
) -> None:
    config, plugin = _fixture(tmp_path)
    server = _FakeAppServer(plugin, candidate_user_prompt_invalid_source=True)

    with pytest.raises(BenchmarkError, match=r"fail-fast.*turn/interrupt") as raised:
        _run_with_server(config, plugin, server)

    assert server.read_methods == ["hook/started", "hook/completed"]
    assert raised.value.evidence is not None
    assert raised.value.evidence["failure_kind"] == "invalid_source"
    assert raised.value.evidence["attribution"] == "matching_exact_source_start"
    rendered = f"{raised.value}\n{json.dumps(raised.value.evidence)}"
    assert "UNTRUSTED_PRIVATE_SOURCE" not in rendered
    assert str(tmp_path) not in rendered


def test_unrelated_failed_hook_does_not_trip_installed_lians_monitor(tmp_path: Path) -> None:
    config, plugin = _fixture(tmp_path)
    server = _FakeAppServer(plugin, unrelated_hook_failure=True)

    report = _run_with_server(config, plugin, server)

    assert report["verdict"]["qualified_target_met"] is True
    assert "turn/interrupt" not in [method for _, method, _ in server.calls]
    assert server.read_methods[0] == "hook/completed"


def test_baseline_must_emit_its_own_prompt_hook_lifecycle(tmp_path: Path) -> None:
    config, plugin = _fixture(tmp_path)
    server = _FakeAppServer(plugin, baseline_user_prompt_dispatch=False)

    with pytest.raises(BenchmarkError, match="baseline.*UserPromptSubmit"):
        _run_with_server(config, plugin, server)

    assert server.turn_count == 2
    assert server.closed is True


def test_candidate_exact_answer_failure_aborts_before_baseline(tmp_path: Path) -> None:
    config, plugin = _fixture(tmp_path)
    server = _FakeAppServer(plugin, candidate_answer="May 7, 2023")

    with pytest.raises(BenchmarkError, match="answer did not exactly match gold"):
        _run_with_server(config, plugin, server)

    assert server.turn_count == 1
    assert server.closed is True


def test_candidate_receipt_failure_aborts_before_baseline(tmp_path: Path) -> None:
    config, plugin = _fixture(tmp_path)
    server = _FakeAppServer(plugin, invalid_candidate_receipt=True)

    with pytest.raises(BenchmarkError, match="hook receipt prompt hash mismatch"):
        _run_with_server(config, plugin, server)

    assert server.turn_count == 1
    assert server.closed is True


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


def test_credit_estimate_uses_only_published_sol_rates_and_rejects_cache_writes() -> None:
    assert SOL_CREDIT_RATES == {
        "uncached_input_credits_per_million": 125.0,
        "cached_input_credits_per_million": 12.5,
        "output_credits_per_million": 750.0,
    }
    with pytest.raises(BenchmarkError, match="does not publish that rate"):
        estimate_sol_credits(
            {
                "uncached_input_tokens": 1,
                "cached_input_tokens": 0,
                "cache_write_input_tokens": 1,
                "output_tokens": 0,
            }
        )


def test_public_receipt_whitelists_safe_typed_evidence() -> None:
    secret_path = r"C:\\Users\\operator\\private-project"
    receipt = {
        **_receipt("question", candidate=True),
        "query_sha256": "a" * 64,
        "context_sha256": "b" * 64,
        "retrieval_transport": "daemon",
        "elapsed_ms": 123,
        "local_path": secret_path,
        "query": "private query text",
        "nested": {"token": "secret"},
    }

    public = _public_receipt(receipt)

    assert public["query_sha256"] == "a" * 64
    assert public["context_sha256"] == "b" * 64
    assert public["retrieval_transport"] == "daemon"
    assert public["elapsed_ms"] == 123
    rendered = json.dumps(public)
    assert secret_path not in rendered
    assert "private query text" not in rendered
    assert "secret" not in rendered
    assert set(receipt) - set(public) == {"local_path", "query", "nested"}


def test_bge_artifact_identity_hashes_manifest_not_local_path(tmp_path: Path) -> None:
    manifest = {
        "schema": "lians.bge-onnx-artifact.v1",
        "model": {
            "repository": "BAAI/bge-large-en-v1.5",
            "revision": "revision-1",
            "sha256": "a" * 64,
        },
        "tokenizer": {"sha256": "b" * 64},
    }
    artifact_dir = tmp_path / "private-local-path"
    artifact_dir.mkdir()
    manifest_path = artifact_dir / "manifest.json"
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

    class Bootstrap:
        BGE_REVISION = "revision-1"
        BGE_MODEL_SHA256 = "a" * 64
        BGE_TOKENIZER_SHA256 = "b" * 64

    identity = _bge_artifact_identity(Bootstrap, artifact_dir)

    assert (
        identity["bge_artifact_manifest_sha256"]
        == hashlib.sha256(manifest_path.read_bytes()).hexdigest()
    )
    assert identity["bge_model_sha256"] == "a" * 64
    assert identity["bge_tokenizer_sha256"] == "b" * 64
    assert str(artifact_dir) not in json.dumps(identity)


def test_credit_cap_aborts_before_second_paid_turn(tmp_path: Path) -> None:
    config, plugin = _fixture(tmp_path)
    server = _FakeAppServer(plugin, high_candidate_usage=True)

    with pytest.raises(BenchmarkError, match="no further calls"):
        _run_with_server(config, plugin, server)

    assert server.turn_count == 1
    assert server.closed is True


def test_seed_storage_accepts_a_v2_protected_subject_reference() -> None:
    expected = "codex-project:repo-0123456789ab"
    protected = "lians:subject:v2:hmac-sha256:" + "a" * 64 + ":" + "b" * 64

    assert _classify_subject_reference(
        protected,
        expected_project_subject_id=expected,
    ) == (protected, "v2_hmac_sha256", True)


def test_seed_storage_reports_the_exact_raw_synthetic_project_subject() -> None:
    expected = "codex-project:repo-0123456789ab"

    assert _classify_subject_reference(
        expected,
        expected_project_subject_id=expected,
    ) == (expected, "raw_synthetic_project_subject", False)


@pytest.mark.parametrize(
    ("stored", "expected"),
    [
        ("customer-7", "codex-project:repo-0123456789ab"),
        ("customer-7", "customer-7"),
        (
            "codex-project:other-0123456789ab",
            "codex-project:repo-0123456789ab",
        ),
    ],
)
def test_seed_storage_rejects_raw_user_or_mismatched_subject_references(
    stored: str,
    expected: str,
) -> None:
    with pytest.raises(BenchmarkError, match="project reference|Codex project reference"):
        _classify_subject_reference(stored, expected_project_subject_id=expected)


def test_plugin_inventory_rejects_source_root_without_versioned_cache(tmp_path: Path) -> None:
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
                "name": "lians-memory",
                "marketplaceName": "lians",
                "version": "0.1.0+test",
                "installed": True,
                "enabled": True,
                "source": {"path": str(root)},
            }
        ]
    }

    with pytest.raises(BenchmarkError, match="versioned installed cache is missing"):
        _plugin_from_document(document)


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
