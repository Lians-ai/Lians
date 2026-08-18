from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest
from lians_easy import agent_experiment


def completed(
    stdout: str,
    *,
    returncode: int = 0,
    stderr: str = "",
) -> subprocess.CompletedProcess[str]:
    return subprocess.CompletedProcess(["agent"], returncode, stdout=stdout, stderr=stderr)


def test_codex_preflight_refuses_api_billing_before_calling_status() -> None:
    called = False

    def runner(*args, **kwargs):  # type: ignore[no-untyped-def]
        nonlocal called
        called = True
        return completed("")

    with pytest.raises(agent_experiment.AgentExperimentError, match="API billing"):
        agent_experiment.codex_preflight(
            environment={"OPENAI_API_KEY": "never-print-this"},
            executable="codex",
            run_command=runner,
        )
    assert called is False


def test_codex_preflight_accepts_chatgpt_and_rejects_api_key_login() -> None:
    auth = agent_experiment.codex_preflight(
        environment={},
        executable="codex",
        run_command=lambda *args, **kwargs: completed("Logged in using ChatGPT"),
    )
    assert auth["auth_method"] == "chatgpt"
    assert auth["provider"] == "openai"

    with pytest.raises(agent_experiment.AgentExperimentError, match="API key"):
        agent_experiment.codex_preflight(
            environment={},
            executable="codex",
            run_command=lambda *args, **kwargs: completed("Logged in using an API key"),
        )


def test_cursor_preflight_requires_account_login_without_returning_identity() -> None:
    auth = agent_experiment.cursor_preflight(
        environment={},
        executable="cursor-agent",
        run_command=lambda *args, **kwargs: completed("Logged in as private@example.com"),
    )
    assert auth == {
        "logged_in": True,
        "auth_method": "account",
        "provider": "cursor",
        "executable": "cursor-agent",
    }
    assert "private@example.com" not in json.dumps(auth)


def test_codex_comparison_uses_ephemeral_read_only_jsonl_calls() -> None:
    expected = {
        "campaign_codename": "Lotus Lantern",
        "launch_region": "Canada",
        "budget_cap_usd": 4200,
    }
    answer = json.dumps(expected, separators=(",", ":"))
    calls: list[tuple[list[str], str | None]] = []

    def runner(command, **kwargs):  # type: ignore[no-untyped-def]
        calls.append((command, kwargs.get("input")))
        if command[1:3] == ["login", "status"]:
            return completed("Logged in using ChatGPT")
        input_tokens = 900 if "five-point confidence scale" in kwargs["input"] else 180
        events = [
            {"type": "item.completed", "item": {"type": "agent_message", "text": answer}},
            {
                "type": "turn.completed",
                "usage": {"input_tokens": input_tokens, "cached_input_tokens": 20, "output_tokens": 24},
            },
        ]
        return completed("\n".join(json.dumps(event) for event in events))

    result = agent_experiment.run_provider_experiment(
        "codex",
        repetitions=1,
        scenario="baseline",
        environment={},
        executable="codex",
        run_command=runner,
    )

    assert result["evidence_gate"]["met"] is True
    assert result["provider"] == "codex"
    for command, prompt in calls[1:]:
        assert prompt
        assert command[1] == "exec"
        assert "--ephemeral" in command
        assert "--ignore-user-config" in command
        assert "--ignore-rules" in command
        assert command[command.index("--sandbox") + 1] == "read-only"
        assert command[-1] == "-"


def test_cursor_comparison_uses_ask_mode_and_one_synthetic_prompt_file() -> None:
    expected = {
        "campaign_codename": "Lotus Lantern",
        "launch_region": "Canada",
        "budget_cap_usd": 4200,
    }
    answer = json.dumps(expected, separators=(",", ":"))
    calls: list[list[str]] = []

    def runner(command, **kwargs):  # type: ignore[no-untyped-def]
        calls.append(command)
        if command[1:] == ["status"]:
            return completed("Logged in as private@example.com")
        prompt_path = Path(kwargs["cwd"], "PROMPT.txt")
        prompt = prompt_path.read_text(encoding="utf-8")
        input_tokens = 900 if "five-point confidence scale" in prompt else 180
        return completed(
            json.dumps(
                {
                    "type": "result",
                    "subtype": "success",
                    "is_error": False,
                    "result": answer,
                    "usage": {
                        "inputTokens": input_tokens,
                        "outputTokens": 24,
                        "cacheReadTokens": 10,
                        "cacheWriteTokens": 0,
                    },
                }
            )
        )

    result = agent_experiment.run_provider_experiment(
        "cursor",
        repetitions=1,
        scenario="baseline",
        environment={},
        executable="cursor-agent",
        run_command=runner,
    )

    assert result["evidence_gate"]["met"] is True
    assert result["provider"] == "cursor"
    for command in calls[1:]:
        assert command[command.index("--mode") + 1] == "ask"
        assert command[command.index("--output-format") + 1] == "json"
        assert "--trust" in command
        assert "--force" not in command
        assert "--yolo" not in command
        assert "--approve-mcps" not in command
