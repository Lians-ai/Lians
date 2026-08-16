from __future__ import annotations

import json
import subprocess

import pytest
from lians_easy import claude_experiment


def completed(stdout: str, *, returncode: int = 0) -> subprocess.CompletedProcess[str]:
    return subprocess.CompletedProcess(["claude"], returncode, stdout=stdout, stderr="")


def test_plan_uses_a_smaller_bounded_prompt_with_the_exact_required_facts() -> None:
    plan = claude_experiment.build_experiment_plan()

    full = plan.report["variants"]["full_replay"]
    bounded = plan.report["variants"]["lians_bounded"]
    assert full["memory_count"] == 24
    assert bounded["memory_count"] == 3
    assert bounded["prompt_token_estimate"] < full["prompt_token_estimate"]
    assert plan.report["planned_prompt_reduction_percent"] > 70
    assert "Lotus Lantern" in plan.lians_prompt
    assert "Canada" in plan.lians_prompt
    assert "4200 USD" in plan.lians_prompt
    assert "five-point confidence scale" not in plan.lians_prompt
    assert "does not prove" in plan.report["claim_boundary"]


def test_preflight_fails_closed_before_auth_check_when_api_key_is_present() -> None:
    called = False

    def runner(*args, **kwargs):  # type: ignore[no-untyped-def]
        nonlocal called
        called = True
        return completed("{}")

    with pytest.raises(claude_experiment.ClaudeExperimentError, match="API or cloud billing"):
        claude_experiment.claude_preflight(
            environment={"ANTHROPIC_API_KEY": "do-not-print-this-secret"},
            executable="claude",
            run_command=runner,
        )
    assert called is False


def test_preflight_rejects_api_key_auth_without_echoing_identifiers() -> None:
    payload = json.dumps(
        {
            "loggedIn": True,
            "authMethod": "api_key",
            "apiProvider": "firstParty",
            "email": "private@example.com",
        }
    )
    with pytest.raises(claude_experiment.ClaudeExperimentError) as error:
        claude_experiment.claude_preflight(
            environment={},
            executable="claude",
            run_command=lambda *args, **kwargs: completed(payload),
        )
    assert "private@example.com" not in str(error.value)
    assert "API key" in str(error.value)


def test_preflight_rejects_non_anthropic_provider_route() -> None:
    payload = json.dumps(
        {
            "loggedIn": True,
            "authMethod": "oauth_token",
            "apiProvider": "bedrock",
        }
    )
    with pytest.raises(claude_experiment.ClaudeExperimentError, match="non-Anthropic"):
        claude_experiment.claude_preflight(
            environment={},
            executable="claude",
            run_command=lambda *args, **kwargs: completed(payload),
        )


def test_preflight_accepts_current_claude_ai_subscription_label() -> None:
    payload = json.dumps(
        {
            "loggedIn": True,
            "authMethod": "claude.ai",
            "apiProvider": "firstParty",
        }
    )

    result = claude_experiment.claude_preflight(
        environment={},
        executable="claude",
        run_command=lambda *args, **kwargs: completed(payload),
    )

    assert result["auth_method"] == "claude.ai"
    assert result["provider"] == "firstParty"


def test_live_comparison_uses_isolated_subscription_calls_and_scores_exact_answers() -> None:
    calls: list[tuple[list[str], str | None, str | None]] = []
    answer = json.dumps(
        {
            "campaign_codename": "Lotus Lantern",
            "launch_region": "Canada",
            "budget_cap_usd": 4200,
        },
        separators=(",", ":"),
    )

    def runner(command, **kwargs):  # type: ignore[no-untyped-def]
        calls.append((command, kwargs.get("input"), kwargs.get("cwd")))
        if command[1:3] == ["auth", "status"]:
            return completed(
                json.dumps(
                    {
                        "loggedIn": True,
                        "authMethod": "oauth_token",
                        "apiProvider": "firstParty",
                    }
                )
            )
        input_tokens = 800 if "five-point confidence scale" in kwargs["input"] else 180
        return completed(
            json.dumps(
                {
                    "result": answer,
                    "usage": {
                        "input_tokens": input_tokens,
                        "cache_creation_input_tokens": 20,
                        "cache_read_input_tokens": 10,
                        "output_tokens": 24,
                    },
                }
            )
        )

    result = claude_experiment.run_claude_experiment(
        environment={}, executable="claude", run_command=runner
    )

    assert result["status"] == "completed"
    assert result["auth"] == {
        "logged_in": True,
        "auth_method": "oauth_token",
        "provider": "firstParty",
    }
    assert result["comparison"]["both_variants_answered_correctly"] is True
    assert result["comparison"]["provider_reported_input_token_reduction_percent"] > 70
    assert len(calls) == 3
    for command, prompt, working_directory in calls[1:]:
        assert prompt
        assert working_directory
        assert "--bare" not in command
        assert command[command.index("--tools") + 1] == ""
        assert "--strict-mcp-config" in command
        assert command[command.index("--setting-sources") + 1] == ""
        assert "--disable-slash-commands" in command
        assert "--no-session-persistence" in command
        assert command[command.index("--max-turns") + 1] == "1"


def test_usage_keeps_cache_and_uncached_input_visible() -> None:
    usage = claude_experiment._usage(
        {
            "usage": {
                "input_tokens": 100,
                "cache_creation_input_tokens": 30,
                "cache_read_input_tokens": 40,
                "output_tokens": 9,
            }
        }
    )

    assert usage == {
        "input_tokens": 100,
        "cache_creation_input_tokens": 30,
        "cache_read_input_tokens": 40,
        "provider_reported_total_input_tokens": 170,
        "output_tokens": 9,
    }
